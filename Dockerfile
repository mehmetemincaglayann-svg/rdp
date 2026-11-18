# PyRemote aktarma sunucusu (Render, Fly.io, Koyeb, Railway ya da herhangi bir VPS için)
FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir "websockets>=13"
COPY pyremote/__init__.py pyremote/protocol.py pyremote/relay.py pyremote/
# Barındırma hizmeti PORT değişkenini verir; yoksa 8765 kullanılır.
CMD ["python", "-m", "pyremote.relay", "--host", "0.0.0.0"]
