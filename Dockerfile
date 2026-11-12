FROM python:3.12-slim

WORKDIR /app

# 安装依赖（利用 Docker 缓存，先拷贝 requirements）
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 拷贝项目源码
COPY . .

# Hugging Face Spaces Docker 容器固定监听 7860 端口
ENV PORT=7860
EXPOSE 7860

# 启动命令：读取 PORT 环境变量（HF 注入 7860）
CMD ["sh", "-c", "uvicorn server.main:app --host 0.0.0.0 --port ${PORT:-7860}"]
