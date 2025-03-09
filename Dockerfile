FROM python:3.9-slim

# 设置工作目录
WORKDIR /app

# 安装系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# 复制项目文件
COPY requirements.txt .
COPY main.py .
COPY .env .
COPY README.md .

# 创建输出目录
RUN mkdir -p output_images

# 安装Python依赖
RUN pip install --no-cache-dir -r requirements.txt

# 暴露端口
EXPOSE 3000

# 设置环境变量
ENV PYTHONUNBUFFERED=1

# 启动应用
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "3000"] 