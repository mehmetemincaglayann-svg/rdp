"""İzleyicinin ağ tarafı (arayüzden bağımsız, asyncio)."""
import asyncio
import io
import json
import os
from pathlib import Path

from PIL import Image
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from .. import crypto
from ..protocol import (CHUNK_HDR, CHUNK_SIZE, FRAME_HDR, MAX_WS_MESSAGE, MSG_CHUNK,
                        MSG_FRAME, MSG_JSON)

ERRORS = {
    "offline": "Bu ID ile çevrimiçi bir bilgisayar yok.",
    "busy": "Karşı bilgisayarda çok fazla açık oturum var.",
    "rate_limited": "Çok fazla bağlantı denemesi yapıldı. Biraz sonra tekrar deneyin.",
    "wrong_password": "Parola hatalı.",
    "locked": "Çok fazla hatalı deneme yapıldı; karşı bilgisayar girişleri geçici olarak kilitledi.",
}


class ConnectError(Exception):
    pass


class ViewerClient:
    """on_event(ad, **bilgi) çağrıları ağ iş parçacığından yapılır.

    Olaylar: info, frame, clip, ls_result, progress, file_done, file_error
    """

    def __init__(self, relay: str, host_id: str, password: str, on_event):
        self.relay = relay
        self.host_id = host_id
        self.password = password
        self.on_event = on_event
        self.chan = None
        self._next_id = 0
        self._downloads = {}   # aktarım no -> {"f", "path", "size", "got"}
        self._uploads = {}     # aktarım no -> Future (ana makinenin onayı)

    async def connect(self):
        try:
            ws = await connect(self.relay, max_size=MAX_WS_MESSAGE)
        except Exception as e:
            raise ConnectError(f"Sunucuya bağlanılamadı: {e}") from e
        try:
            await ws.send(json.dumps({"role": "viewer", "id": self.host_id}))
            reply = json.loads(await asyncio.wait_for(ws.recv(), 25))
            if reply.get("type") != "ready":
                code = reply.get("error", "?")
                raise ConnectError(ERRORS.get(code, f"Bağlantı reddedildi ({code})"))
            self.chan = await crypto.viewer_handshake(ws, self.password, self.host_id)
        except crypto.AuthError as e:
            await ws.close()
            raise ConnectError(ERRORS.get(str(e), f"Kimlik doğrulama başarısız: {e}")) from e
        except (ConnectionClosed, asyncio.TimeoutError, ValueError) as e:
            await ws.close()
            raise ConnectError("Bağlantı kurulurken kesildi.") from e
        except BaseException:
            await ws.close()
            raise

    async def run(self):
        """Bağlantı kapanana kadar gelen iletileri işler."""
        try:
            while True:
                mtype, payload = await self.chan.recv()
                if mtype == MSG_FRAME:
                    seq, x, y, w, h, fw, fh = FRAME_HDR.unpack_from(payload)
                    img = Image.open(io.BytesIO(payload[FRAME_HDR.size:]))
                    img.load()
                    self.on_event("frame", seq=seq, x=x, y=y, image=img, size=(fw, fh))
                elif mtype == MSG_CHUNK:
                    (tid,) = CHUNK_HDR.unpack_from(payload)
                    d = self._downloads.get(tid)
                    if d:
                        d["f"].write(payload[CHUNK_HDR.size:])
                        d["got"] += len(payload) - CHUNK_HDR.size
                        self.on_event("progress", id=tid, done=d["got"], total=d["size"],
                                      filename=d["path"].name)
                elif mtype == MSG_JSON:
                    self._on_json(json.loads(payload))
        except (ConnectionClosed, crypto.AuthError):
            pass
        finally:
            for d in self._downloads.values():
                if d["f"]:
                    d["f"].close()
                    d["path"].unlink(missing_ok=True)
            for fut in self._uploads.values():
                if not fut.done():
                    fut.cancel()
            await self.chan.close()

    def _on_json(self, msg):
        t = msg.get("t")
        if t in ("info", "clip", "ls_result"):
            self.on_event(t, **{k: v for k, v in msg.items() if k != "t"})
        elif t == "get_begin":
            d = self._downloads.get(msg["id"])
            if d:
                d["size"] = msg["size"]
                d["f"] = open(d["path"], "wb")
        elif t == "get_end":
            d = self._downloads.pop(msg["id"], None)
            if d:
                d["f"].close()
                self.on_event("file_done", id=msg["id"], path=str(d["path"]), direction="down")
        elif t == "put_done":
            fut = self._uploads.pop(msg["id"], None)
            if fut and not fut.done():
                fut.set_result(msg.get("path"))
        elif t == "file_error":
            tid = msg.get("id")
            d = self._downloads.pop(tid, None)
            if d and d["f"]:
                d["f"].close()
                d["path"].unlink(missing_ok=True)
            fut = self._uploads.pop(tid, None)
            if fut and not fut.done():
                fut.set_exception(OSError(msg.get("error", "hata")))
            elif d:
                self.on_event("file_error", id=tid, error=msg.get("error", "hata"))

    async def send(self, obj: dict):
        try:
            await self.chan.send_json(obj)
        except ConnectionClosed:
            pass

    async def close(self):
        if self.chan:
            await self.chan.close()

    def _new_id(self) -> int:
        self._next_id += 1
        return self._next_id

    async def download(self, remote_path: str, local_path: str) -> int:
        tid = self._new_id()
        self._downloads[tid] = {"f": None, "path": Path(local_path), "size": 0, "got": 0}
        await self.send({"t": "get", "id": tid, "path": remote_path})
        return tid

    async def upload(self, local_path: str) -> int:
        tid = self._new_id()
        name = os.path.basename(local_path)
        try:
            size = os.path.getsize(local_path)
            fut = asyncio.get_running_loop().create_future()
            self._uploads[tid] = fut
            with open(local_path, "rb") as f:
                await self.chan.send_json({"t": "put_begin", "id": tid, "name": name,
                                           "size": size})
                sent = 0
                while not fut.done():   # ana makine hata bildirirse dur
                    chunk = await asyncio.to_thread(f.read, CHUNK_SIZE)
                    if not chunk:
                        break
                    await self.chan.send(MSG_CHUNK, CHUNK_HDR.pack(tid) + chunk)
                    sent += len(chunk)
                    self.on_event("progress", id=tid, done=sent, total=size, filename=name)
            if not fut.done():
                await self.chan.send_json({"t": "put_end", "id": tid})
            remote = await asyncio.wait_for(fut, 60)
            self.on_event("file_done", id=tid, path=remote, direction="up")
        except (OSError, asyncio.TimeoutError, ConnectionClosed) as e:
            self._uploads.pop(tid, None)
            self.on_event("file_error", id=tid, error=str(e) or "zaman aşımı")
        return tid
