FROM m.daocloud.io/docker.io/library/python:3.11-slim

ARG BBDOWN_VERSION=1.6.3
ARG BBDOWN_DOWNLOAD_URL=https://github.com/nilaoda/BBDown/releases/download/1.6.3/BBDown_1.6.3_20240814_linux-x64.zip

RUN rm -f /etc/apt/sources.list && \
    rm -rf /etc/apt/sources.list.d/* && \
    echo "deb https://mirrors.tuna.tsinghua.edu.cn/debian bookworm main contrib non-free non-free-firmware" > /etc/apt/sources.list && \
    echo "deb https://mirrors.tuna.tsinghua.edu.cn/debian bookworm-updates main contrib non-free non-free-firmware" >> /etc/apt/sources.list && \
    echo "deb https://mirrors.tuna.tsinghua.edu.cn/debian-security bookworm-security main contrib non-free non-free-firmware" >> /etc/apt/sources.list && \
    apt-get update && \
    apt-get install -y ffmpeg curl ca-certificates unzip && \
    rm -rf /var/lib/apt/lists/*

RUN set -eux; \
    bbdown_url="${BBDOWN_DOWNLOAD_URL}"; \
    test -n "${bbdown_url}"; \
    curl -fL "${bbdown_url}" -o /tmp/bbdown.zip; \
    unzip -o /tmp/bbdown.zip -d /tmp/bbdown; \
    bbdown_bin="$(find /tmp/bbdown -type f -name BBDown | head -n1)"; \
    test -n "${bbdown_bin}"; \
    install -m 0755 "${bbdown_bin}" /usr/local/bin/BBDown; \
    rm -rf /tmp/bbdown /tmp/bbdown.zip; \
    test -x /usr/local/bin/BBDown

ENV PATH="/usr/local/bin:/usr/bin:${PATH}"

# 设置 Hugging Face 镜像源环境变量
ENV HF_ENDPOINT=https://hf-mirror.com
ENV BBDOWN_BIN=/usr/local/bin/BBDown
ENV BBDOWN_WORK_DIR=data/bbdown
ENV BBDOWN_TIMEOUT_SECONDS=120
ENV BILIBILI_SUBTITLE_PROVIDER=bbdown

WORKDIR /app
COPY ./backend /app
RUN pip install --no-cache-dir -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt

CMD ["python", "main.py"]
