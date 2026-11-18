"""Ana makine servisi: bağlantı tekliflerini yanıtlar, oturumları yürütür."""
import asyncio
import io
import json
import logging
import os
import platform
import string
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PIL import Image, ImageChops

from .. import crypto, nat, p2p
from ..config import Config
from ..protocol import (CHUNK_HDR, CHUNK_SIZE, DRIVES, FRAME_HDR, MAX_CLIPBOARD,
                        MSG_CHUNK, MSG_FRAME, MSG_JSON)
from ..signaling import Signaling, topic_base, unseal
from ..transport import Closed
from .backend import Backend

log = logging.getLogger("host")

MAX_FPS = 20
MAX_UNACKED = 2
MAX_UPLOADS = 4
MAX_PENDING = 6             # aynı anda kurulmakta/açık olan bağlantı
MAX_OFFERS_PER_2MIN = 30    # teklif seli koruması
BAD_NAME_CHARS = set('<>:"/\\|?*')


def safe_filename(name: str) -> str:
    name = str(name).replace("\\", "/").rsplit("/", 1)[-1]
    name = "".join(c for c in name if c not in BAD_NAME_CHARS and ord(c) >= 32).strip(" .")
    return name[:200] or "dosya"


def unique_path(folder: Path, name: str) -> Path:
    target = folder / name
    stem, suffix, n = target.stem, target.suffix, 1
    while target.exists():
        target = folder / f"{stem} ({n}){suffix}"
        n += 1
    return target


def list_dir(path: str) -> dict:
    if path == DRIVES:
        drives = [f"{c}:\\" for c in string.ascii_uppercase if os.path.exists(f"{c}:\\")]
        return {"path": DRIVES, "parent": None,
                "entries": [{"name": d, "dir": True, "size": 0, "path": d} for d in drives]}
    p = Path(path).expanduser() if path else Path.home()
    p = p.resolve()
    entries = []
    with os.scandir(p) as it:
        for e in it:
            try:
                is_dir = e.is_dir()
                size = 0 if is_dir else e.stat().st_size
            except OSError:
                continue
            entries.append({"name": e.name, "dir": is_dir, "size": size, "path": str(p / e.name)})
    entries.sort(key=lambda e: (not e["dir"], e["name"].lower()))
    if p.parent != p:
        parent = str(p.parent)
    else:
        parent = DRIVES if sys.platform == "win32" else None
    return {"path": str(p), "parent": parent, "entries": entries}


class Session:
    def __init__(self, service: "HostService", chan: crypto.SecureChannel, peer: str):
        self.service = service
        self.backend = service.backend
        self.chan = chan
        self.peer = peer
        self.monitor = 0
        self.quality = 60
        self.scale = 1.0
        self.seq = 0
        self.unacked = 0
        self.ack_event = asyncio.Event()
        self.prev = None          # son gönderilen kare (fark almak için)
        self.force_full = True
        self.last_clip = None
        self.uploads = {}         # aktarım no -> (dosya, yol)
        self.tasks = set()

    async def run(self):
        mons = await self.service.in_capture_thread(self.backend.monitors)
        for i, m in enumerate(mons):
            if m.get("primary"):
                self.monitor = i
        await self.chan.send_json({
            "t": "info", "monitors": mons, "monitor": self.monitor,
            "host": platform.node(), "os": platform.system(),
            "control": self.backend.can_control, "unattended": self.chan.slot == "permanent",
        })
        loops = [asyncio.create_task(c) for c in
                 (self.recv_loop(), self.frame_loop(), self.clipboard_loop())]
        try:
            done, _ = await asyncio.wait(loops, return_when=asyncio.FIRST_COMPLETED)
            for t in done:
                if t.exception() and not isinstance(t.exception(), Closed):
                    log.warning("oturum hatası: %r", t.exception())
        finally:
            for t in loops + list(self.tasks):
                t.cancel()
            for f, path in self.uploads.values():   # yarım kalan yüklemeleri sil
                f.close()
                path.unlink(missing_ok=True)
            self.backend.release_all()
            await self.chan.close()

    # --- ekran ---------------------------------------------------------
    def _grab_and_encode(self):
        img = self.backend.grab(self.monitor)
        if self.scale < 0.99:
            img = img.resize((max(1, int(img.width * self.scale)),
                              max(1, int(img.height * self.scale))), Image.BILINEAR)
        if self.force_full or self.prev is None or self.prev.size != img.size:
            box = (0, 0, img.width, img.height)
            self.force_full = False
        else:
            box = ImageChops.difference(img, self.prev).getbbox()
            if box is None:
                return None
        self.prev = img
        buf = io.BytesIO()
        img.crop(box).save(buf, "JPEG", quality=self.quality)
        self.seq += 1
        hdr = FRAME_HDR.pack(self.seq, box[0], box[1], box[2] - box[0], box[3] - box[1],
                             img.width, img.height)
        return hdr + buf.getvalue()

    async def frame_loop(self):
        loop = asyncio.get_running_loop()
        interval = 1.0 / MAX_FPS
        while True:
            # İzleyici yetişemiyorsa kare biriktirme: onay gelene kadar bekle.
            while self.unacked >= MAX_UNACKED:
                self.ack_event.clear()
                await self.ack_event.wait()
            start = loop.time()
            try:
                data = await self.service.in_capture_thread(self._grab_and_encode)
            except Exception as e:
                log.warning("ekran yakalanamadı: %r", e)
                data = None
                await asyncio.sleep(1)
            if data:
                self.unacked += 1
                await self.chan.send(MSG_FRAME, data)
            await asyncio.sleep(max(0.0, interval - (loop.time() - start)))

    # --- pano ----------------------------------------------------------
    async def clipboard_loop(self):
        self.last_clip = await asyncio.to_thread(self.backend.clipboard_get)
        while True:
            await asyncio.sleep(0.7)
            text = await asyncio.to_thread(self.backend.clipboard_get)
            if text and text != self.last_clip and len(text) <= MAX_CLIPBOARD:
                self.last_clip = text
                await self.chan.send_json({"t": "clip", "text": text})

    # --- gelen iletiler ------------------------------------------------
    async def recv_loop(self):
        while True:
            mtype, payload = await self.chan.recv()
            if mtype == MSG_JSON:
                msg = json.loads(payload)
                handler = getattr(self, "on_" + str(msg.get("t")), None)
                if handler:
                    await handler(msg)
            elif mtype == MSG_CHUNK:
                (tid,) = CHUNK_HDR.unpack_from(payload)
                up = self.uploads.get(tid)
                if up:
                    try:
                        up[0].write(payload[CHUNK_HDR.size:])
                    except OSError as e:
                        await self._abort_upload(tid, str(e))

    async def on_ack(self, msg):
        self.unacked = max(0, self.unacked - 1)
        self.ack_event.set()

    async def on_monitor(self, msg):
        self.monitor = int(msg.get("i", 0))
        self.force_full = True

    async def on_quality(self, msg):
        self.quality = min(max(int(msg.get("q", self.quality)), 10), 95)
        self.scale = min(max(float(msg.get("scale", self.scale)), 0.25), 1.0)
        self.force_full = True

    async def on_refresh(self, msg):
        self.force_full = True

    async def on_move(self, msg):
        self.backend.mouse_move(self.monitor, float(msg["x"]), float(msg["y"]))

    async def on_button(self, msg):
        if "x" in msg:
            self.backend.mouse_move(self.monitor, float(msg["x"]), float(msg["y"]))
        self.backend.mouse_button(str(msg["b"]), bool(msg["d"]))

    async def on_wheel(self, msg):
        self.backend.mouse_wheel(float(msg.get("dx", 0)), float(msg.get("dy", 0)))

    async def on_key(self, msg):
        self.backend.key(msg)

    async def on_clip(self, msg):
        text = str(msg.get("text", ""))
        if text and len(text) <= MAX_CLIPBOARD:
            self.last_clip = text
            await asyncio.to_thread(self.backend.clipboard_set, text)

    # --- dosyalar ------------------------------------------------------
    async def on_ls(self, msg):
        try:
            result = await asyncio.to_thread(list_dir, str(msg.get("path") or ""))
            await self.chan.send_json({"t": "ls_result", **result})
        except OSError as e:
            await self.chan.send_json({"t": "ls_result", "path": msg.get("path"),
                                       "error": e.strerror or str(e)})

    async def on_put_begin(self, msg):
        tid = int(msg["id"])
        if len(self.uploads) >= MAX_UPLOADS:
            return await self.chan.send_json({"t": "file_error", "id": tid,
                                              "error": "çok fazla eşzamanlı aktarım"})
        try:
            folder = self.service.download_dir
            folder.mkdir(parents=True, exist_ok=True)
            path = unique_path(folder, safe_filename(msg.get("name", "")))
            self.uploads[tid] = (open(path, "wb"), path)
        except OSError as e:
            await self.chan.send_json({"t": "file_error", "id": tid, "error": str(e)})

    async def _abort_upload(self, tid, error):
        f, path = self.uploads.pop(tid)
        f.close()
        path.unlink(missing_ok=True)
        await self.chan.send_json({"t": "file_error", "id": tid, "error": error})

    async def on_put_end(self, msg):
        tid = int(msg["id"])
        up = self.uploads.pop(tid, None)
        if up:
            up[0].close()
            log.info("dosya alındı: %s", up[1])
            await self.chan.send_json({"t": "put_done", "id": tid, "path": str(up[1])})

    async def on_put_cancel(self, msg):
        up = self.uploads.pop(int(msg["id"]), None)
        if up:
            up[0].close()
            up[1].unlink(missing_ok=True)

    async def on_get(self, msg):
        task = asyncio.create_task(self._send_file(int(msg["id"]), str(msg.get("path", ""))))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def _send_file(self, tid, path):
        try:
            p = Path(path)
            size = p.stat().st_size
            with open(p, "rb") as f:
                await self.chan.send_json({"t": "get_begin", "id": tid, "size": size,
                                           "name": p.name})
                while True:
                    chunk = await asyncio.to_thread(f.read, CHUNK_SIZE)
                    if not chunk:
                        break
                    await self.chan.send(MSG_CHUNK, CHUNK_HDR.pack(tid) + chunk)
            log.info("dosya gönderildi: %s", p)
            await self.chan.send_json({"t": "get_end", "id": tid})
        except OSError as e:
            await self.chan.send_json({"t": "file_error", "id": tid,
                                       "error": e.strerror or str(e)})


class HostService:
    def __init__(self, config: Config, backend: Backend, on_change=None):
        self.config = config
        self.backend = backend
        self.on_change = on_change or (lambda: None)   # durum değişince çağrılır
        self.guard = crypto.AuthGuard()
        self.session_password = ""
        self._session_key = b""
        self.new_session_password()
        self.status = "connecting"     # connecting | online | offline
        self.sessions = {}             # Session -> eş IP
        self.download_dir = Path.home() / "Downloads"
        # Ekran yakalama tek bir iş parçacığında yapılır (mss iş parçacığına bağlıdır).
        self._capture = ThreadPoolExecutor(max_workers=1, thread_name_prefix="capture")
        self._sig = None
        self.nat = "unknown"           # ağ türü: ok | symmetric | blocked | unknown
        self._seen = {}                # işlenen teklif no -> zaman
        self._tasks = set()

    def in_capture_thread(self, fn, *args):
        return asyncio.get_running_loop().run_in_executor(self._capture, fn, *args)

    def new_session_password(self):
        self.session_password = crypto.new_session_password()
        self._session_key = crypto.stretch_password(self.session_password, self.config.host_id)
        self.on_change()

    def set_permanent_password(self, password: str | None):
        """Gözetimsiz erişim parolası. None verilirse kaldırılır."""
        if password is None:
            self.config.permanent_key = None
        else:
            if len(password) < 8:
                raise ValueError("Kalıcı parola en az 8 karakter olmalı")
            self.config.permanent_key = crypto.stretch_password(password, self.config.host_id)
        self.on_change()

    def _set_status(self, status):
        if self.status != status:
            self.status = status
            self.on_change()

    async def disconnect_all(self):
        for s in list(self.sessions):
            await s.chan.close()

    async def run(self):
        """Tanışma sunucularını dinler; gelen her bağlantı teklifini yanıtlar."""
        host_id = self.config.host_id

        def on_change():
            self._set_status("online" if self._sig.connected else "offline")

        def on_message(topic, payload):
            msg = unseal(host_id, payload)
            if not msg or not isinstance(msg.get("sid"), str) or not isinstance(msg.get("sdp"), str):
                return
            now = asyncio.get_running_loop().time()
            for k in [k for k, t in self._seen.items() if now - t > 120]:
                del self._seen[k]
            # Aynı teklif birden çok sunucudan ve yinelenerek gelir; bir kez işlenir.
            if msg["sid"] in self._seen or len(self._seen) >= MAX_OFFERS_PER_2MIN \
                    or len(self._tasks) >= MAX_PENDING:
                return
            self._seen[msg["sid"]] = now
            t = asyncio.create_task(self._accept(msg["sid"], msg["sdp"]))
            self._tasks.add(t)
            t.add_done_callback(self._tasks.discard)

        self._sig = Signaling(self.config.brokers, on_message, on_change)
        await self._sig.subscribe(topic_base(host_id) + "/offer")
        self._sig.start()
        try:
            while True:   # ağ değişebilir (Wi-Fi/mobil); ağ türü düzenli denetlenir
                if self.config.ice_servers:
                    result = await nat.detect()
                    if result != self.nat:
                        self.nat = result
                        log.info("ağ türü: %s", nat.NAT_TEXT[result])
                        self.on_change()
                await asyncio.sleep(300 if self.nat == "ok" else 60)
        finally:
            await self._sig.stop()

    async def _accept(self, sid: str, offer_sdp: str):
        session = None
        try:
            transport = await p2p.host_answer(self._sig, self.config.host_id, sid, offer_sdp,
                                              self.config.ice_servers, self.nat)
        except (p2p.P2PError, asyncio.TimeoutError):
            return log.info("bir bağlantı isteği geldi ancak doğrudan bağlantı kurulamadı")
        except Exception as e:
            return log.warning("bağlantı teklifi işlenemedi: %r", e)
        peer = transport.peer_address()
        try:
            passwords = {"session": self._session_key, "permanent": self.config.permanent_key}
            chan = await crypto.host_handshake(transport, passwords, self.guard)
            log.info("oturum açıldı: %s (%s parola)", peer, chan.slot)
            session = Session(self, chan, peer)
            self.sessions[session] = peer
            self.on_change()
            await session.run()
        except crypto.AuthError as e:
            log.warning("kimlik doğrulama başarısız (%s): %s", peer, e)
        except (Closed, asyncio.TimeoutError, ValueError):
            pass
        finally:
            await transport.close()
            if session:
                self.sessions.pop(session, None)
                log.info("oturum kapandı: %s", peer)
                self.on_change()
