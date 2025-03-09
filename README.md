# Comfy Sync Server

这是一个基于FastAPI的服务器，用于同步接收请求，转发至ComfyUI，并同步返回图片URL。

## 功能特点

- 接收包含workflow数据的请求
- 转发请求至ComfyUI服务器
- 实时监听WebSocket消息，获取生成进度
- 将生成的图片保存到本地并上传至阿里云OSS
- 返回图片的URL

## 安装

1. 克隆仓库：

```bash
git clone https://github.com/yourusername/comfy_sync_server.git
cd comfy_sync_server
```

2. 安装依赖：

```bash
pip install -r requirements.txt
```

3. 配置环境变量：

复制`.env.example`文件为`.env`，并填写相关配置：

```bash
cp .env.example .env
```

编辑`.env`文件，填写以下配置：

```bash
# ComfyUI服务器配置
COMFYUI_SERVER=your_comfyui_server_address

# 阿里云OSS配置
OSS_ACCESS_KEY_ID=your_access_key_id
OSS_ACCESS_KEY_SECRET=your_access_key_secret
OSS_ENDPOINT=your_endpoint
OSS_BUCKET_NAME=your_bucket_name
OSS_BASE_PATH=comfy_images/
```

## 运行

```bash
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

或者直接运行：

```bash
python main.py
```

服务器将在 http://localhost:8000 上启动。

## API文档

启动服务器后，可以访问 http://localhost:8000/docs 查看API文档。

### 生成图片

**请求**：

```
POST /api/generate
```

**请求体**：

```json
{
    "workflow_data": {
        // ComfyUI工作流数据
    },
    "output_node_id": 277
}
```

**响应**：

```json
{
    "url": "https://your-bucket.your-endpoint.com/comfy_images/image_1234567890.png"
}
```

## 注意事项

- 确保ComfyUI服务器已经启动并可访问
- 如果未配置阿里云OSS，将返回本地文件路径
- 生成的图片会保存在`output_images`目录下

## 许可证

MIT 