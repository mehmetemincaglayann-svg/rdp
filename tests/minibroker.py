"""Testler için en küçük MQTT (WebSocket) sunucusu: abone ol, yayınla, ilet."""
import struct

from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed

from pyremote.signaling import _packet, _packets


class MiniBroker:
    def __init__(self):
        self.subs = {}   # bağlantı -> konu kümesi

    async def handle(self, ws):
        self.subs[ws] = set()
        buf = bytearray()
        try:
            async for data in ws:
                buf += data
                for head, body in _packets(buf):
                    kind = head >> 4
                    if kind == 1:
                        await ws.send(_packet(0x20, b"\x00\x00"))
                    elif kind == 8:
                        (tlen,) = struct.unpack_from(">H", body, 2)
                        self.subs[ws].add(body[4:4 + tlen].decode())
                        await ws.send(_packet(0x90, body[:2] + b"\x00"))
                    elif kind == 3:
                        (tlen,) = struct.unpack_from(">H", body)
                        topic = body[2:2 + tlen].decode()
                        for other, topics in list(self.subs.items()):
                            if topic in topics:
                                try:
                                    await other.send(_packet(0x30, body))
                                except Exception:
                                    pass
                    elif kind == 12:
                        await ws.send(_packet(0xD0, b""))
        except ConnectionClosed:
            pass
        finally:
            del self.subs[ws]

    def serve(self, port):
        return serve(self.handle, "127.0.0.1", port, subprotocols=["mqtt"])
