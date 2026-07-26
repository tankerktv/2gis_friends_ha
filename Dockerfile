FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Asia/Tomsk

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bridge/ ./bridge/

RUN useradd -m -u 1000 bridge && mkdir -p /data && chown -R bridge:bridge /data /app
USER bridge

VOLUME ["/data"]

CMD ["python", "-m", "bridge.main"]
