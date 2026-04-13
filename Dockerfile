FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    APP_HOST=0.0.0.0 \
    APP_PORT=8080 \
    FFDEC_BIN=/app/tools/ffdec_full/ffdec.sh \
    JAVA_TOOL_OPTIONS=-Djava.awt.headless=true

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        bash \
        ca-certificates \
        default-jre-headless \
        fontconfig \
        fonts-dejavu-core \
        tini \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

COPY . /app/

RUN mkdir -p /app/cache \
    && sed -i 's/\r$//' /app/docker-entrypoint.sh /app/tools/ffdec_full/ffdec.sh /app/tools/ffdec_full/ffdec \
    && chmod +x /app/docker-entrypoint.sh /app/tools/ffdec_full/ffdec.sh /app/tools/ffdec_full/ffdec

EXPOSE 8080

ENTRYPOINT ["tini", "--", "/app/docker-entrypoint.sh"]
