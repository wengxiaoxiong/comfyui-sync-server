import os
import uuid
import json
import time
import asyncio
import threading
import websocket
import requests
from io import BytesIO
from PIL import Image
from typing import Dict, Any, Optional, Union, Literal
from fastapi import FastAPI, HTTPException, Response
from starlette.staticfiles import StaticFiles
from pydantic import BaseModel
from dotenv import load_dotenv
import oss2
import datetime

# 加载环境变量
load_dotenv()

# 创建FastAPI应用
app = FastAPI(title="Comfy Sync Server", description="同步接收请求，转发至ComfyUI，同步返回图片URL或文件")

# 配置
COMFYUI_SERVER = os.getenv("COMFYUI_SERVER", "52.83.46.103:32713")
ENABLE_OSS = os.getenv("ENABLE_OSS", "true").lower() == "true"  # 默认启用OSS
SERVER_BASE_URL = os.getenv("SERVER_BASE_URL", "")  # 服务器基础URL，例如 http://example.com
OSS_ACCESS_KEY_ID = os.getenv("OSS_ACCESS_KEY_ID")
OSS_ACCESS_KEY_SECRET = os.getenv("OSS_ACCESS_KEY_SECRET")
OSS_ENDPOINT = os.getenv("OSS_ENDPOINT")
OSS_BUCKET_NAME = os.getenv("OSS_BUCKET_NAME")
OSS_BASE_PATH = os.getenv("OSS_BASE_PATH", "comfy_images/")

# 创建保存图片的目录
OUTPUT_DIR = "output_images"
os.makedirs(OUTPUT_DIR, exist_ok=True)

# 挂载静态文件目录
app.mount("/output_images", StaticFiles(directory=OUTPUT_DIR), name="output_images")

# 初始化阿里云OSS
def init_oss():
    if not ENABLE_OSS:
        print("阿里云OSS已禁用，将使用本地HTTP服务器存储图像")
        return None
        
    if not all([OSS_ACCESS_KEY_ID, OSS_ACCESS_KEY_SECRET, OSS_ENDPOINT, OSS_BUCKET_NAME]):
        print("警告: 阿里云OSS配置不完整，将使用本地HTTP服务器存储图像")
        return None
    
    auth = oss2.Auth(OSS_ACCESS_KEY_ID, OSS_ACCESS_KEY_SECRET)
    bucket = oss2.Bucket(auth, OSS_ENDPOINT, OSS_BUCKET_NAME)
    return bucket

oss_bucket = init_oss()

# 请求模型
class GenerateRequest(BaseModel):
    workflow_data: Dict[str, Any]
    output_node_id: int
    response_type: Optional[Literal["url", "file"]] = "url"  # 新增字段，指定返回类型

# 响应模型
class GenerateUrlResponse(BaseModel):
    url: str

# 图像生成结果类
class ImageGenerationResult:
    def __init__(self):
        self.image_path: Optional[str] = None
        self.image_url: Optional[str] = None
        self.image_data: Optional[bytes] = None  # 新增字段，存储图像二进制数据
        self.error: Optional[str] = None
        self.completed = False
        self.event = asyncio.Event()
        self.loop = None  # 存储事件循环引用

# 存储生成任务的字典
generation_tasks = {}

# 获取本地图片URL
def get_local_image_url(filename):
    # 如果设置了SERVER_BASE_URL，使用它作为基础URL
    if SERVER_BASE_URL:
        base_url = SERVER_BASE_URL.rstrip('/')
        return f"{base_url}/output_images/{filename}"
    
    # 否则使用相对路径
    return f"/output_images/{filename}"

# 上传图片到阿里云OSS
def upload_to_oss(local_path, object_name):
    if oss_bucket is None:
        return None
    
    try:
        # 构建OSS对象名称
        if not object_name.startswith(OSS_BASE_PATH):
            object_name = f"{OSS_BASE_PATH}{object_name}"
        
        # 上传文件
        oss_bucket.put_object_from_file(object_name, local_path)
        
        print(f"上传成功: {object_name}")
        
        # 生成URL - 修复URL格式
        # 从OSS_ENDPOINT中提取域名部分，去掉协议前缀
        endpoint = OSS_ENDPOINT
        if endpoint.startswith("http://") or endpoint.startswith("https://"):
            endpoint = endpoint.split("://")[1]
        
        # 生成带签名的URL，有效期30天
        expiration_time = int(time.time()) + 30 * 24 * 60 * 60  # 30天后的时间戳
        signed_url = oss_bucket.sign_url('GET', object_name, expiration_time)
        
        print(f"已生成签名URL，有效期30天: {signed_url}")
        return signed_url
    except Exception as e:
        print(f"上传到OSS失败: {str(e)}")
        return None

# WebSocket消息处理函数
def handle_websocket_messages(ws, client_id, output_node_id):
    result = generation_tasks[client_id]
    
    try:
        print(f"WebSocket连接已建立，client_id: {client_id}, 等待消息...")
        while True:
            try:
                message = ws.recv()
                
                if isinstance(message, str):  # 文本消息
                    print(f"收到文本消息: {message[:200]}..." if len(message) > 200 else message)
                    message_data = json.loads(message)
                    
                    if "type" in message_data:
                        msg_type = message_data["type"]
                        
                        if msg_type == "executing":
                            node = message_data.get("data", {}).get("node", "None")
                            print(f"正在执行: node={node}, 目标节点={output_node_id}")
                            
                            # 检查是否是输出节点
                            if node == str(output_node_id):
                                print(f"正在处理输出节点: {output_node_id}")
                        
                        elif msg_type in ["execution_success", "execution_interrupted", "execution_error"]:
                            if msg_type == "execution_error":
                                error_msg = message_data.get("error", "未知错误")
                                result.error = f"生成过程中出错: {error_msg}"
                                print(f"执行错误: {error_msg}")
                            
                            print(f"收到{msg_type}消息，停止执行")
                            break
                
                else:  # 二进制消息（图片数据）
                    print(f"收到二进制数据，长度: {len(message)} 字节，正在转换为PNG图片...")
                    try:
                        # 跳过前8个字节，然后处理剩余的二进制数据
                        binary_data = message[8:]
                        
                        # 保存原始二进制数据，用于文件响应
                        result.image_data = binary_data
                        
                        # 使用PIL库将二进制数据转换为图片
                        image = Image.open(BytesIO(binary_data))
                        
                        # 生成带有时间戳的文件名，确保唯一性
                        timestamp = int(time.time())
                        filename = f"image_{timestamp}.png"
                        local_path = f"{OUTPUT_DIR}/{filename}"
                        
                        # 保存为PNG格式
                        image.save(local_path, "PNG")
                        print(f"图片已保存为: {local_path}")
                        
                        # 设置结果
                        result.image_path = local_path
                        
                        # 上传到OSS或使用本地URL
                        if ENABLE_OSS and oss_bucket is not None:
                            image_url = upload_to_oss(local_path, filename)
                            if image_url:
                                result.image_url = image_url
                                print(f"图片已上传到OSS: {image_url}")
                        else:
                            # 使用本地HTTP服务器URL
                            local_url = get_local_image_url(filename)
                            result.image_url = local_url
                            print(f"使用本地URL: {local_url}")
                    
                    except Exception as e:
                        result.error = f"处理图片时出错: {str(e)}"
                        print(result.error)
            except Exception as inner_e:
                print(f"处理WebSocket消息时出错: {str(inner_e)}")
                import traceback
                traceback.print_exc()
                result.error = f"处理WebSocket消息时出错: {str(inner_e)}"
                break
    
    except Exception as e:
        print(f"WebSocket连接异常: {str(e)}")
        import traceback
        traceback.print_exc()
        result.error = f"WebSocket连接错误: {str(e)}"
    
    finally:
        try:
            ws.close()
            print(f"WebSocket连接已关闭，client_id: {client_id}")
        except:
            pass
            
        # 标记任务完成
        result.completed = True
        print(f"任务标记为完成，通知等待的协程，client_id: {client_id}")
        # 通知等待的协程，使用存储的事件循环引用
        if result.loop and not result.loop.is_closed():
            # 修复：event.set() 不是协程，需要使用 call_soon_threadsafe
            result.loop.call_soon_threadsafe(result.event.set)
        else:
            print("警告: 无法通知事件循环，事件循环可能已关闭")

# 通用的图像生成处理函数
async def process_generation_request(request: GenerateRequest):
    # 生成唯一的客户端ID
    client_id = str(uuid.uuid4())
    print(f"创建新的生成任务，client_id: {client_id}")
    
    # 创建结果对象
    result = ImageGenerationResult()
    # 存储当前事件循环的引用
    result.loop = asyncio.get_running_loop()
    generation_tasks[client_id] = result
    
    try:
        # 建立WebSocket连接
        print(f"尝试连接WebSocket: ws://{COMFYUI_SERVER}/ws?clientId={client_id}")
        ws = websocket.WebSocket()
        try:
            ws.connect(f"ws://{COMFYUI_SERVER}/ws?clientId={client_id}")
            print(f"WebSocket连接成功")
        except Exception as ws_error:
            print(f"WebSocket连接失败: {str(ws_error)}")
            raise HTTPException(status_code=500, detail=f"无法连接到ComfyUI WebSocket: {str(ws_error)}")
        
        # 启动WebSocket消息处理线程
        ws_thread = threading.Thread(
            target=handle_websocket_messages, 
            args=(ws, client_id, request.output_node_id)
        )
        ws_thread.daemon = True
        ws_thread.start()
        
        # 发送生成请求到ComfyUI的HTTP API
        url = f"http://{COMFYUI_SERVER}/prompt"
        headers = {"Content-Type": "application/json"}
        
        payload = {
            "client_id": client_id,
            "prompt": request.workflow_data
        }
        
        print(f"发送HTTP请求到: {url}")
        print(f"请求数据: client_id={client_id}, workflow节点数={len(request.workflow_data)}")
        
        response = requests.post(url, json=payload, headers=headers)
        
        if response.status_code != 200:
            print(f"请求失败，状态码: {response.status_code}, 响应: {response.text}")
            raise HTTPException(status_code=500, detail=f"发送生成请求失败: {response.text}")
        
        print(f"请求发送成功，等待WebSocket返回结果...")
        # 等待生成完成
        await result.event.wait()
        print(f"事件已触发，生成过程结束")
        
        # 检查是否有错误
        if result.error:
            print(f"生成过程中发生错误: {result.error}")
            raise HTTPException(status_code=500, detail=result.error)
        
        return result
    
    except Exception as e:
        print(f"处理请求时发生异常: {str(e)}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))
    
    finally:
        # 清理任务
        if client_id in generation_tasks:
            del generation_tasks[client_id]
            print(f"已清理任务: {client_id}")

@app.post("/api/generate", response_model=GenerateUrlResponse)
async def generate_image(request: GenerateRequest):
    """生成图像并返回URL"""
    # 确保响应类型为URL
    if request.response_type and request.response_type != "url":
        request.response_type = "url"
    
    result = await process_generation_request(request)
    
    # 检查是否有图像URL
    if not result.image_url:
        raise HTTPException(status_code=500, detail="未能生成图像URL")
    
    # 返回图像URL
    return {"url": result.image_url}


@app.post("/api/generate_file")
async def generate_image_file(request: GenerateRequest):
    """生成图像并直接返回文件内容，同时在响应头中提供URL"""
    # 设置响应类型为文件
    request.response_type = "file"
    
    result = await process_generation_request(request)
    
    # 检查是否有图像数据
    if not result.image_data:
        raise HTTPException(status_code=500, detail="未能生成图像数据")
    
    # 检查是否有图像URL
    image_url = result.image_url or ""
    
    # 直接返回图像文件，并在响应头中添加URL
    return Response(
        content=result.image_data,
        media_type="image/png",
        headers={
            "Content-Disposition": f"attachment; filename=image_{int(time.time())}.png",
            "X-Image-Url": image_url  # 添加自定义响应头
        }
    )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=3000, reload=True) 