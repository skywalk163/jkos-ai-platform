FROM python:3.11-slim

WORKDIR /app

# 安装系统依赖
RUN apt-get update && apt-get install -y \
    curl \
    && rm -rf /var/lib/apt/lists/*

# 复制依赖文件
COPY pyproject.toml .

# 安装 Python 依赖
RUN pip install --no-cache-dir -e ".[prod]"

# 复制代码
COPY . .

# 创建目录
RUN mkdir -p /app/data /app/logs

# 暴露端口
EXPOSE 8000 3000

# 启动脚本
CMD ["python", "server.py"]
