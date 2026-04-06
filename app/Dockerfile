FROM python:3.12-slim

RUN apt-get update && apt-get install -y \
    gcc \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Firefox のシステムライブラリをインストール
# Chromium は ARM Docker 環境でクラッシュしやすいため Firefox を使用
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libnss3 \
        libnspr4 \
        libdbus-1-3 \
        libatk1.0-0 \
        libatk-bridge2.0-0 \
        libcups2 \
        libdrm2 \
        libxkbcommon0 \
        libxcomposite1 \
        libxdamage1 \
        libxfixes3 \
        libxrandr2 \
        libgbm1 \
        libasound2 \
        libgtk-3-0 \
        libx11-xcb1 \
        libxt6 \
    && rm -rf /var/lib/apt/lists/*

RUN playwright install firefox
