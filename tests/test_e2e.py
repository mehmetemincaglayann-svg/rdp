"""Uçtan uca test: ana makine (sahte ekran/girdi) + izleyici, gerçek P2P veri kanalı üzerinden.

Tanışma için yerel bir sahte MQTT sunucusu kullanılır; internet gerekmez.

Çalıştırma:  python tests/test_e2e.py
"""
import asyncio
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from PIL import Image, ImageDraw

from pyremote import fix_console
from pyremote.config import Config
from pyremote.host.backend import Backend
from pyremote.host.service import HostService
from pyremote.viewer.client import ConnectError, ViewerClient
from minibroker import MiniBroker

PORT = 18765


class FakeBackend(Backend):
    can_control = True

    def __init__(self):
        self.events = []
        self.clip = "ilk"
        self.tick = 0

    def monitors(self):
        return [{"i": 0, "x": 0, "y": 0, "w": 640, "h": 480, "primary": True},
                {"i": 1, "x": 640, "y": 0, "w": 320, "h": 240, "primary": False}]

    def grab(self, index):
        w, h = (640, 480) if index == 0 else (320, 240)
        img = Image.new("RGB", (w, h), (30, 60, 90) if index == 0 else (200, 0, 0))
        self.tick += 1
        ImageDraw.Draw(img).rectangle((100, 100, 120 + self.tick % 50, 120), fill=(255, 255, 0))
        return img

    def mouse_move(self, index, nx, ny): self.events.append(("move", index, nx, ny))
    def mouse_button(self, b, d): self.events.append(("button", b, d))
    def mouse_wheel(self, dx, dy): self.events.append(("wheel", dx, dy))
    def key(self, ev): self.events.append(("key", ev["sym"], ev["d"]))
    def release_all(self): self.events.append(("release_all",))
    def clipboard_get(self): return self.clip
    def clipboard_set(self, text): self.clip = text


class Collector:
    def __init__(self):
        self.events = []
        self.cond = asyncio.Condition()
        self.loop = asyncio.get_running_loop()

    def __call__(self, name, **kw):
        self.events.append((name, kw))
        self.loop.create_task(self._notify())

    async def _notify(self):
        async with self.cond:
            self.cond.notify_all()

    async def wait(self, pred, timeout=10):
        async def inner():
            async with self.cond:
                while True:
                    for name, kw in self.events:
                        if pred(name, kw):
                            return kw
                    await self.cond.wait()
        return await asyncio.wait_for(inner(), timeout)


async def main():
    tmp = Path(tempfile.mkdtemp()).resolve()
    broker = await MiniBroker().serve(PORT)

    cfg = Config(tmp / "config.json")
    # Ölü bir sunucu da listede: biri çalışmasa bile bağlantı kurulabilmeli.
    cfg.data["brokers"] = ["ws://127.0.0.1:1", f"ws://127.0.0.1:{PORT}"]
    cfg.data["direct_port"] = PORT + 1
    cfg.data["ice_servers"] = []      # test yerel ağda çalışır, STUN gerekmez
    cfg.save()
    backend = FakeBackend()
    host = HostService(cfg, backend)
    host.download_dir = tmp / "indirilenler"
    host_task = asyncio.create_task(host.run())
    while host.status != "online":
        await asyncio.sleep(0.05)
    print("ok  ana makine tanışma sunucusuna bağlandı, ID:", cfg.host_id)

    # 1) bilinmeyen ID (yanıt veren olmaz)
    from pyremote import p2p
    p2p.ANSWER_TIMEOUT = 3
    try:
        await ViewerClient(cfg, "111111111", "x", lambda *a, **k: None).connect()
        raise AssertionError("bilinmeyen ID kabul edildi")
    except ConnectError as e:
        assert "bulunamadı" in str(e), e
        print("ok  bilinmeyen ID:", e)
    p2p.ANSWER_TIMEOUT = 20

    # 2) yanlış parola
    try:
        await ViewerClient(cfg, cfg.host_id, "yanlis-parola", lambda *a, **k: None).connect()
        raise AssertionError("yanlış parola kabul edildi")
    except ConnectError as e:
        assert "Parola" in str(e), e
        print("ok  yanlış parola reddedildi")
    assert host.guard.fails == 1

    # 3) oturum parolası ile bağlan
    col = Collector()
    client = ViewerClient(cfg, cfg.host_id, host.session_password, col)
    await client.connect()
    assert client.chan.slot == "session"
    run_task = asyncio.create_task(client.run())
    info = await col.wait(lambda n, k: n == "info")
    assert len(info["monitors"]) == 2 and info["control"] and not info["unattended"]
    peer = next(iter(host.sessions.values()))
    assert peer != "?", "karşı tarafın adresi bulunamadı"
    print("ok  oturum parolasıyla P2P bağlandı; monitör:", len(info["monitors"]), "karşı adres:", peer)

    # 4) kareler: önce tam kare, sonra yalnızca değişen bölge
    first = await col.wait(lambda n, k: n == "frame")
    assert first["size"] == (640, 480) and first["image"].size == (640, 480)
    await client.send({"t": "ack"})
    delta = await col.wait(lambda n, k: n == "frame" and k["seq"] > first["seq"])
    assert delta["image"].size[0] < 640 and delta["y"] == 100, (delta["image"].size, delta["y"])
    print("ok  tam kare + fark karesi alındı:", delta["image"].size, "@", (delta["x"], delta["y"]))

    # akış denetimi: onay gönderilmezse en fazla 2 kare birikir
    await asyncio.sleep(0.5)
    n_frames = sum(1 for n, _ in col.events if n == "frame")
    assert n_frames <= 3, n_frames
    print("ok  akış denetimi çalışıyor (onaysız kare sayısı sınırlı)")

    # 5) monitör değiştirme
    await client.send({"t": "monitor", "i": 1})
    for _ in range(4):
        await client.send({"t": "ack"})
    f2 = await col.wait(lambda n, k: n == "frame" and k["size"] == (320, 240))
    assert f2["image"].getpixel((5, 5))[0] > 150
    print("ok  ikinci monitöre geçildi")

    # 6) girdi
    await client.send({"t": "move", "x": 0.5, "y": 0.25})
    await client.send({"t": "button", "b": "left", "d": 1, "x": 0.1, "y": 0.2})
    await client.send({"t": "button", "b": "left", "d": 0})
    await client.send({"t": "wheel", "dy": -1})
    await client.send({"t": "key", "d": 1, "sym": "a", "ch": "a", "code": 38})
    await client.send({"t": "key", "d": 0, "sym": "a", "ch": "a", "code": 38})
    for _ in range(100):
        if ("key", "a", 0) in backend.events:
            break
        await asyncio.sleep(0.02)
    assert ("move", 1, 0.5, 0.25) in backend.events
    assert ("button", "left", 1) in backend.events and ("button", "left", 0) in backend.events
    assert ("wheel", 0.0, -1.0) in backend.events
    assert ("key", "a", 1) in backend.events and ("key", "a", 0) in backend.events
    print("ok  fare ve klavye olayları ana makineye ulaştı")

    # 7) pano (iki yönde)
    await client.send({"t": "clip", "text": "izleyiciden"})
    for _ in range(100):
        if backend.clip == "izleyiciden":
            break
        await asyncio.sleep(0.02)
    assert backend.clip == "izleyiciden"
    backend.clip = "ana makineden ğüşiöç"
    got = await col.wait(lambda n, k: n == "clip")
    assert got["text"] == "ana makineden ğüşiöç"
    print("ok  pano iki yönde eşitlendi")

    # 8) dosya gönderme (izleyici -> ana makine), aynı adla ikinci kez
    src = tmp / "gönder.bin"
    data = os.urandom(3_000_000)
    src.write_bytes(data)
    await client.upload(str(src))
    done = await col.wait(lambda n, k: n == "file_done" and k["direction"] == "up")
    assert Path(done["path"]).read_bytes() == data
    assert Path(done["path"]).parent == host.download_dir
    await client.upload(str(src))
    await asyncio.sleep(0.2)
    names = sorted(p.name for p in host.download_dir.iterdir())
    assert names == ["gönder (1).bin", "gönder.bin"], names
    print("ok  dosya gönderildi; aynı adlı dosyanın üzerine yazılmadı:", names)

    # 9) uzak klasör listeleme + dosya alma
    await client.send({"t": "ls", "path": str(host.download_dir)})
    ls = await col.wait(lambda n, k: n == "ls_result")
    assert [e["name"] for e in ls["entries"]] == names and ls["parent"] == str(tmp)
    dst = tmp / "alınan.bin"
    await client.download(ls["entries"][1]["path"], str(dst))
    await col.wait(lambda n, k: n == "file_done" and k["direction"] == "down")
    assert dst.read_bytes() == data
    await client.download(str(tmp / "yok.bin"), str(tmp / "x.bin"))
    err = await col.wait(lambda n, k: n == "file_error")
    assert not (tmp / "x.bin").exists()
    print("ok  uzak klasör listelendi, dosya alındı; olmayan dosya hatası:", err["error"])

    # 10) bağlantı kapanınca ana makine tuşları bırakır
    assert len(host.sessions) == 1
    await client.close()
    await run_task
    for _ in range(100):
        if not host.sessions:
            break
        await asyncio.sleep(0.02)
    assert not host.sessions and backend.events[-1] == ("release_all",)
    print("ok  oturum kapandı, basılı tuşlar bırakıldı")

    # 11) kalıcı parola (gözetimsiz erişim)
    try:
        host.set_permanent_password("kısa")
        raise AssertionError
    except ValueError:
        pass
    host.set_permanent_password("çok-gizli-kalıcı-parola")
    assert "çok-gizli" not in (tmp / "config.json").read_text("utf-8")
    c2 = ViewerClient(cfg, cfg.host_id, "çok-gizli-kalıcı-parola", Collector())
    await c2.connect()
    assert c2.chan.slot == "permanent"
    await c2.close()
    host.set_permanent_password(None)
    try:
        await ViewerClient(cfg, cfg.host_id, "çok-gizli-kalıcı-parola", Collector()).connect()
        raise AssertionError("kaldırılan kalıcı parola kabul edildi")
    except ConnectError:
        pass
    print("ok  kalıcı parola çalışıyor; diskte düz metin yok; kaldırınca reddediliyor")

    # 12) kilitleme: art arda 5 hatalı denemeden sonra doğru parola da reddedilir
    host.guard.success()
    for _ in range(5):
        try:
            await ViewerClient(cfg, cfg.host_id, "hatali", Collector()).connect()
        except ConnectError:
            pass
    try:
        await ViewerClient(cfg, cfg.host_id, host.session_password, Collector()).connect()
        raise AssertionError("kilitliyken giriş kabul edildi")
    except ConnectError as e:
        assert "kilitledi" in str(e), e
    print("ok  5 hatalı denemeden sonra girişler kilitlendi")

    # 13) büyük kare: veri kanalı parçalama + akış denetimi
    from pyremote.transport import DataChannelTransport
    host.guard.success()
    c3 = ViewerClient(cfg, cfg.host_id, host.session_password, Collector())
    await c3.connect()
    big = os.urandom(2_500_000)
    session = next(iter(host.sessions))
    await session.chan.send(9, big)
    while True:
        mtype, payload = await asyncio.wait_for(c3.chan.recv(), 20)
        if mtype == 9:
            break
    assert payload == big
    await c3.close()
    print("ok  2,5 MB'lık tek ileti parçalanıp eksiksiz ulaştı")

    # 14) doğrudan IP:port bağlantısı (tanışma sunucusu ve ID kullanılmadan)
    from pyremote.config import parse_target
    assert parse_target("123 456 789") == ("id", "123456789")
    assert parse_target("192.168.1.20") == ("direct", ("192.168.1.20", 47600))
    assert parse_target("ev.ornek.com:5000") == ("direct", ("ev.ornek.com", 5000))
    assert parse_target("12345") is None and parse_target("a b") is None
    assert host.direct_port == PORT + 1
    try:
        await ViewerClient(cfg, "", "yanlis", Collector(), address=("127.0.0.1", PORT + 1)).connect()
        raise AssertionError("doğrudan bağlantıda yanlış parola kabul edildi")
    except ConnectError as e:
        assert "Parola" in str(e), e
    host.guard.success()
    col4 = Collector()
    c4 = ViewerClient(cfg, "", host.session_password, col4, address=("127.0.0.1", PORT + 1))
    await c4.connect()
    r4 = asyncio.create_task(c4.run())
    f = await col4.wait(lambda n, k: n == "frame")
    assert f["size"] == (640, 480)
    backend.events.clear()
    await c4.send({"t": "move", "x": 0.3, "y": 0.7})
    for _ in range(100):
        if backend.events:
            break
        await asyncio.sleep(0.02)
    assert ("move", 0, 0.3, 0.7) in backend.events
    await c4.close()
    await r4
    try:
        await ViewerClient(cfg, "", "x", Collector(), address=("127.0.0.1", 9)).connect()
        raise AssertionError
    except ConnectError as e:
        assert "ulaşılamadı" in str(e)
    print("ok  doğrudan IP:port bağlantısı: parola denetimi, ekran ve fare çalıştı")

    host_task.cancel()
    await asyncio.gather(host_task, return_exceptions=True)
    broker.close()
    print("\nTÜM TESTLER GEÇTİ")


if __name__ == "__main__":
    fix_console()
    asyncio.run(main())
