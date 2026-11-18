"""Aktarma (relay) sunucusu.

Ana makineler ID'leriyle kaydolur; izleyiciler bir ID'ye bağlanmak ister.
Sunucu iki tarafı eşleştirir ve aralarındaki şifreli trafiği olduğu gibi
iletir. Trafik uçtan uca şifreli olduğundan sunucu içeriği göremez.

Çalıştırma:  python -m pyremote.relay --host 0.0.0.0 --port 8765
"""
import argparse
import asyncio
import hashlib
import hmac
import json
import logging
import secrets
import ssl
import time
from pathlib import Path

from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed

from . import fix_console
from .protocol import MAX_WS_MESSAGE

log = logging.getLogger("relay")

MAX_SESSIONS_PER_HOST = 4
JOIN_LIMIT = 20          # IP başına dakikadaki bağlanma isteği
PAIR_TIMEOUT = 15


class Relay:
    def __init__(self, registry_path: Path | None = None):
        self.hosts = {}       # id -> {"ws", "sessions"}
        self.pending = {}     # oturum no -> {"id", "future"}
        self.joins = {}       # ip -> [zaman damgaları]
        self.registry_path = registry_path
        self.registry = {}    # id -> sha256(gizli anahtar): ID'yi ilk alan sahiplenir
        if registry_path and registry_path.exists():
            try:
                self.registry = json.loads(registry_path.read_text("utf-8"))
            except (OSError, ValueError):
                log.warning("kayıt dosyası okunamadı: %s", registry_path)

    def _save_registry(self):
        if self.registry_path:
            self.registry_path.write_text(json.dumps(self.registry), "utf-8")

    def _check_owner(self, host_id: str, secret: str) -> bool:
        digest = hashlib.sha256(secret.encode()).hexdigest()
        known = self.registry.get(host_id)
        if known is None:
            self.registry[host_id] = digest
            self._save_registry()
            return True
        return hmac.compare_digest(known, digest)

    def _rate_ok(self, ip: str) -> bool:
        now = time.monotonic()
        stamps = [t for t in self.joins.get(ip, []) if now - t < 60]
        stamps.append(now)
        self.joins[ip] = stamps
        if len(self.joins) > 10000:
            self.joins.clear()
        return len(stamps) <= JOIN_LIMIT

    async def handle(self, ws):
        ip = ws.remote_address[0] if ws.remote_address else "?"
        try:
            hello = json.loads(await asyncio.wait_for(ws.recv(), 10))
            role = hello.get("role")
            if role == "host":
                await self._host(ws, hello, ip)
            elif role == "host_data":
                await self._host_data(ws, hello)
            elif role == "viewer":
                await self._viewer(ws, hello, ip)
        except (ConnectionClosed, asyncio.TimeoutError, ValueError, AttributeError, TypeError):
            pass

    @staticmethod
    async def _error(ws, code: str):
        await ws.send(json.dumps({"type": "error", "error": code}))

    async def _host(self, ws, hello, ip):
        host_id, secret = str(hello.get("id", "")), str(hello.get("secret", ""))
        if not (host_id.isdigit() and len(host_id) == 9 and len(secret) >= 32):
            return await self._error(ws, "bad_request")
        if not self._check_owner(host_id, secret):
            return await self._error(ws, "id_taken")
        old = self.hosts.get(host_id)
        if old:
            await old["ws"].close()
        entry = {"ws": ws, "sessions": 0}
        self.hosts[host_id] = entry
        await ws.send(json.dumps({"type": "registered"}))
        log.info("ana makine bağlandı: %s (%s)", host_id, ip)
        try:
            async for _ in ws:
                pass
        finally:
            if self.hosts.get(host_id) is entry:
                del self.hosts[host_id]
            log.info("ana makine ayrıldı: %s", host_id)

    async def _viewer(self, ws, hello, ip):
        host_id = str(hello.get("id", ""))
        if not self._rate_ok(ip):
            return await self._error(ws, "rate_limited")
        entry = self.hosts.get(host_id)
        if entry is None:
            return await self._error(ws, "offline")
        if entry["sessions"] >= MAX_SESSIONS_PER_HOST:
            return await self._error(ws, "busy")
        sid = secrets.token_urlsafe(18)
        fut = asyncio.get_running_loop().create_future()
        self.pending[sid] = {"id": host_id, "future": fut}
        entry["sessions"] += 1
        done = None
        try:
            await entry["ws"].send(json.dumps({"type": "connect", "session": sid, "peer": ip}))
            host_ws, done = await asyncio.wait_for(fut, PAIR_TIMEOUT)
            await ws.send(json.dumps({"type": "ready"}))
            await host_ws.send(json.dumps({"type": "ready"}))
            log.info("oturum başladı: %s <- %s", host_id, ip)
            await self._pipe(ws, host_ws)
            log.info("oturum bitti: %s <- %s", host_id, ip)
        except (asyncio.TimeoutError, ConnectionClosed):
            try:
                await self._error(ws, "offline")
            except ConnectionClosed:
                pass
        finally:
            self.pending.pop(sid, None)
            entry["sessions"] -= 1
            if done:
                done.set()

    async def _host_data(self, ws, hello):
        p = self.pending.pop(str(hello.get("session", "")), None)
        if p is None or p["future"].done():
            return await self._error(ws, "bad_session")
        if not self._check_owner(p["id"], str(hello.get("secret", ""))):
            return await self._error(ws, "bad_request")
        done = asyncio.Event()
        p["future"].set_result((ws, done))
        await done.wait()

    @staticmethod
    async def _pipe(a, b):
        async def forward(src, dst):
            try:
                async for msg in src:
                    await dst.send(msg)
            except ConnectionClosed:
                pass

        tasks = [asyncio.create_task(forward(a, b)), asyncio.create_task(forward(b, a))]
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for t in tasks:
                t.cancel()
            await asyncio.gather(a.close(), b.close(), return_exceptions=True)


async def run_relay(host: str, port: int, registry: Path | None = None,
                    ssl_context=None, ready: asyncio.Event | None = None):
    relay = Relay(registry)
    async with serve(relay.handle, host, port, max_size=MAX_WS_MESSAGE,
                     ssl=ssl_context, ping_interval=20, ping_timeout=30):
        log.info("aktarma sunucusu dinliyor: %s:%d", host, port)
        if ready:
            ready.set()
        await asyncio.Future()


def main():
    ap = argparse.ArgumentParser(description="PyRemote aktarma sunucusu")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--data", default="relay_registry.json", help="ID kayıt dosyası")
    ap.add_argument("--cert", help="TLS sertifikası (wss:// için)")
    ap.add_argument("--key", help="TLS özel anahtarı")
    args = ap.parse_args()
    fix_console()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    ctx = None
    if args.cert:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(args.cert, args.key)
    try:
        asyncio.run(run_relay(args.host, args.port, Path(args.data), ctx))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
