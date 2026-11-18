"""Tanışma (sinyalleşme): iki tarafın bağlantı bilgilerini değiş tokuş etmesi.

Kendi sunucumuz yoktur. Bağlantı bilgileri, herkese açık ücretsiz MQTT
sunucuları üzerinden (WebSocket ile, 443 benzeri bağlantı noktalarından)
iletilir. Bu sunucular yalnızca birkaç KB'lık tanışma iletisini taşır; ekran
verisi iki bilgisayar arasında doğrudan akar.

Tanışma iletileri ID'den türetilen bir anahtarla şifrelenir. Bu, konuyu
dinleyen meraklılara karşı bir perdedir; asıl güvenlik, veri kanalı içindeki
parola doğrulamalı el sıkışmadan (crypto.py) gelir.
"""
import asyncio
import hashlib
import json
import logging
import os
import secrets
import struct

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from websockets.asyncio.client import connect

log = logging.getLogger("signaling")


def topic_base(host_id: str) -> str:
    digest = hashlib.sha256(b"pyremote-topic-v1:" + host_id.encode()).hexdigest()
    return "pyremote/v1/" + digest[:32]


def _key(host_id: str) -> ChaCha20Poly1305:
    return ChaCha20Poly1305(hashlib.sha256(b"pyremote-signal-v1:" + host_id.encode()).digest())


def seal(host_id: str, obj: dict) -> bytes:
    nonce = os.urandom(12)
    return nonce + _key(host_id).encrypt(nonce, json.dumps(obj).encode("utf-8"), None)


def unseal(host_id: str, data: bytes) -> dict | None:
    try:
        obj = json.loads(_key(host_id).decrypt(data[:12], data[12:], None))
        return obj if isinstance(obj, dict) else None
    except (InvalidTag, ValueError):
        return None


# --- en küçük MQTT 3.1.1 istemcisi (yalnızca QoS 0) --------------------------
def _varint(n: int) -> bytes:
    out = bytearray()
    while True:
        b, n = n % 128, n // 128
        out.append(b | (128 if n else 0))
        if not n:
            return bytes(out)


def _str(s: str) -> bytes:
    b = s.encode("utf-8")
    return struct.pack(">H", len(b)) + b


def _packet(head: int, body: bytes) -> bytes:
    return bytes([head]) + _varint(len(body)) + body


def _packets(buf: bytearray):
    """Tampondaki tamamlanmış MQTT paketlerini (başlık, gövde) olarak verir."""
    while len(buf) >= 2:
        n, mult, i = 0, 1, 1
        while True:
            if i >= len(buf):
                return
            b = buf[i]
            n += (b & 127) * mult
            mult *= 128
            i += 1
            if not b & 128:
                break
            if i > 4:
                raise ValueError("bozuk MQTT paketi")
        if len(buf) < i + n:
            return
        head, body = buf[0], bytes(buf[i:i + n])
        del buf[:i + n]
        yield head, body


class _Link:
    """Tek bir MQTT sunucusuna bağlantı; koparsa yeniden bağlanır."""

    def __init__(self, url: str, hub: "Signaling"):
        self.url = url
        self.hub = hub
        self.ws = None

    async def run(self):
        delay = 1
        while True:
            try:
                async with connect(self.url, subprotocols=["mqtt"], max_size=2 ** 20,
                                   open_timeout=10, ping_interval=None) as ws:
                    client_id = "pr" + secrets.token_hex(8)
                    await ws.send(_packet(0x10, _str("MQTT") + bytes([4, 0x02]) +
                                          struct.pack(">H", 60) + _str(client_id)))
                    buf = bytearray()
                    pinger = None
                    try:
                        async for data in ws:
                            buf += data if isinstance(data, bytes) else data.encode()
                            for head, body in _packets(buf):
                                kind = head >> 4
                                if kind == 2:                       # CONNACK
                                    if len(body) < 2 or body[1] != 0:
                                        raise ConnectionError("MQTT bağlantısı reddedildi")
                                    self.ws = ws
                                    delay = 1
                                    for t in self.hub.topics:
                                        await self._subscribe(t)
                                    pinger = asyncio.create_task(self._ping(ws))
                                    self.hub._changed()
                                elif kind == 3:                     # PUBLISH
                                    (tlen,) = struct.unpack_from(">H", body)
                                    topic = body[2:2 + tlen].decode("utf-8", "replace")
                                    off = 2 + tlen + (2 if (head >> 1) & 3 else 0)
                                    self.hub._deliver(topic, body[off:])
                    finally:
                        if pinger:
                            pinger.cancel()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.debug("%s: %r", self.url, e)
            finally:
                if self.ws is not None:
                    self.ws = None
                    self.hub._changed()
            await asyncio.sleep(delay)
            delay = min(delay * 2, 30)

    @staticmethod
    async def _ping(ws):
        while True:
            await asyncio.sleep(30)
            await ws.send(_packet(0xC0, b""))

    async def _subscribe(self, topic: str):
        await self.ws.send(_packet(0x82, struct.pack(">H", 1) + _str(topic) + b"\x00"))

    async def subscribe(self, topic: str):
        if self.ws is not None:
            try:
                await self._subscribe(topic)
            except Exception:
                pass

    async def publish(self, topic: str, payload: bytes) -> bool:
        if self.ws is None:
            return False
        try:
            await self.ws.send(_packet(0x30, _str(topic) + payload))
            return True
        except Exception:
            return False


class Signaling:
    """Birden çok MQTT sunucusunu aynı anda kullanır (biri çalışmasa da diğeri taşır)."""

    def __init__(self, brokers: list[str], on_message=None, on_change=None):
        self.links = [_Link(url, self) for url in brokers]
        self.on_message = on_message or (lambda topic, payload: None)
        self.on_change = on_change or (lambda: None)
        self.topics = set()
        self._tasks = []
        self._event = asyncio.Event()

    @property
    def connected(self) -> int:
        return sum(1 for l in self.links if l.ws is not None)

    def _changed(self):
        self._event.set()
        self.on_change()

    def _deliver(self, topic, payload):
        try:
            self.on_message(topic, payload)
        except Exception:
            log.exception("tanışma iletisi işlenemedi")

    def start(self):
        self._tasks = [asyncio.create_task(l.run()) for l in self.links]

    async def wait_connected(self, timeout: float) -> bool:
        loop = asyncio.get_running_loop()
        end = loop.time() + timeout
        while not self.connected:
            self._event.clear()
            left = end - loop.time()
            if left <= 0:
                return False
            try:
                await asyncio.wait_for(self._event.wait(), left)
            except asyncio.TimeoutError:
                return False
        return True

    async def subscribe(self, topic: str):
        self.topics.add(topic)
        for l in self.links:
            await l.subscribe(topic)

    async def publish(self, topic: str, payload: bytes) -> int:
        results = await asyncio.gather(*(l.publish(topic, payload) for l in self.links))
        return sum(results)

    async def stop(self):
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks = []
