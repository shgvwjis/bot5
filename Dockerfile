FROM python:3.11-slim

WORKDIR /app

# 安装必要的系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# 复制并安装 Python 依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制所有代码
COPY . .

# 创建程序需要的目录
RUN mkdir -p /app/sessions /app/history_sessions /app/export_sessions

# 暴露 Flask 网页端口
EXPOSE 39999

# 启动命令
CMD ["python", "bot.py"]