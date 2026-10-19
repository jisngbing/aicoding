ARG PYTHON_IMAGE=python:3.12-slim
FROM ${PYTHON_IMAGE}
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONUTF8=1
WORKDIR /app
RUN sed -i 's|http://deb.debian.org/debian-security|https://mirrors.tuna.tsinghua.edu.cn/debian-security|g; s|http://deb.debian.org/debian|https://mirrors.tuna.tsinghua.edu.cn/debian|g' /etc/apt/sources.list.d/debian.sources \
    && apt-get -o Acquire::Retries=2 -o Acquire::https::Timeout=30 update \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.lock.txt ./
RUN pip install --no-cache-dir --timeout 60 --index-url https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.lock.txt
RUN useradd --uid 10001 --create-home app
COPY agent ./agent
COPY scripts ./scripts
COPY deploy/entrypoint.sh /entrypoint.sh
RUN mkdir -p data logs ai_workspace && chown -R app:app /app \
    && chmod 755 /entrypoint.sh
USER app
EXPOSE 12024
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:12024/health', timeout=3)"
ENTRYPOINT ["/entrypoint.sh"]
