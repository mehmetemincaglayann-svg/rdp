"""İzleyicinin ağ tarafı (arayüzden bağımsız, asyncio)."""
import asyncio
import io
import json
import os
from pathlib import Path

from PIL import Image

from .. import crypto, p2p
from ..protocol import CHUNK_HDR, CHUNK_SIZE, FRAME_HDR, MSG_CHUNK, MSG_FRAME, MSG_JSON
from ..transport import Closed

ERRORS = {
    "no_signal": "Tanışma sunucularına ulaşılamadı. İnternet bağlantınızı kontrol edin.",
    "offline": "Bu ID ile çevrimiçi bir bilgisayar bulunamadı.",
    "p2p_failed": "Karşı bilgisayar bulundu ancak doğrudan bağlantı kurulamadı. "
                  "İki taraftan birinin ağı (ör. mobil internet / CGNAT) doğrudan "
                  "bağlantıya izin vermiyor olabilir.",
    "wrong_password": "Parola hatalı.",
    "locked": "Çok fazla hatalı deneme yapıldı; karşı bilgisayar girişleri geçici olarak kilitledi.",
}


class ConnectError(Exception):
    pass


class ViewerClient:
    """on_event(ad, **bilgi) çağrıları ağ iş parçacığından yapılır.

    Olaylar: info, frame, clip, ls_result, progress, file_done, file_error
    """

    def __init__(self, config, host_id: str, password: str, on_event):
        self.config = config
        self.host_id = host_id
        self.password = password
        self.on_event = on_event
        self.chan = None
        self._next_id = 0
        self._downloads = {}   # aktarım no -> {"f", "path", "size", "got"}
        self._uploads = {}     # aktarım no -> Future (ana makinenin onayı)

    async def connect(self):
        try:
            transport = await p2p.viewer_connect(self.host_id, self.config.brokers,
                                                 self.config.ice_servers)
        except p2p.P2PError as e:
            raise ConnectError(ERRORS.get(str(e), str(e))) from e
        try:
            self.chan = await crypto.viewer_handshake(transport, self.password, self.host_id)
        except crypto.AuthError as e:
            await transport.close()
            raise ConnectError(ERRORS.get(str(e), f"Kimlik doğrulama başarısız: {e}")) from e
        except (Closed, asyncio.TimeoutError, ValueError) as e:
            await transport.close()
            raise ConnectError("Bağlantı kurulurken kesildi.") from e
        except BaseException:
            await transport.close()
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
        except (Closed, crypto.AuthError):
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
        except Closed:
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
        except (OSError, asyncio.TimeoutError, Closed) as e:
            self._uploads.pop(tid, None)
            self.on_event("file_error", id=tid, error=str(e) or "zaman aşımı")
        return tid
