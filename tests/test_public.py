"""Gerçek ücretsiz servislerle deneme (internet gerekir).

Varsayılan tanışma sunucularının her birine bağlanıp ileti gidip geldiğini,
STUN ile genel adresin öğrenilebildiğini ve varsayılan ayarlarla baştan sona
bir bağlantı kurulabildiğini denetler.

Çalıştırma:  python tests/test_public.py
"""
import asyncio
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from pyremote import fix_console, p2p
from pyremote.config import DEFAULT_BROKERS, DEFAULT_ICE_SERVERS, Config
from pyremote.host.service import HostService
from pyremote.signaling import Signaling
from pyremote.viewer.client import ViewerClient
from test_e2e import Collector, FakeBackend


async def check_broker(url: str) -> bool:
    got = asyncio.get_running_loop().create_future()
    topic = "pyremote/test/" + os.urandom(8).hex()
    sig = Signaling([url], lambda t, p: (t == topic and not got.done()) and got.set_result(p))
    sig.start()
    try:
        if not await sig.wait_connected(15):
            print("HATA", url, "bağlanılamadı")
            return False
        await sig.subscribe(topic)
        await asyncio.sleep(1)
        await sig.publish(topic, b"merhaba")
        ok = await asyncio.wait_for(got, 10) == b"merhaba"
        print("ok  " if ok else "HATA", url)
        return ok
    except asyncio.TimeoutError:
        print("HATA", url, "ileti geri gelmedi")
        return False
    finally:
        await sig.stop()


async def main() -> int:
    results = [await check_broker(u) for u in DEFAULT_BROKERS]
    brokers_ok = sum(results)

    pc = p2p.new_peer_connection(DEFAULT_ICE_SERVERS)
    pc.createDataChannel("x")
    await pc.setLocalDescription(await pc.createOffer())
    stun_ok = "typ srflx" in pc.localDescription.sdp
    await pc.close()
    print("ok  " if stun_ok else "HATA", "STUN ile genel adres öğrenme")

    cfg = Config(Path(tempfile.mkdtemp()) / "config.json")   # varsayılan servisler
    host = HostService(cfg, FakeBackend())
    task = asyncio.create_task(host.run())
    e2e_ok = False
    try:
        for _ in range(300):
            if host.status == "online":
                break
            await asyncio.sleep(0.1)
        await asyncio.sleep(2)
        col = Collector()
        client = ViewerClient(cfg, cfg.host_id, host.session_password, col)
        await client.connect()
        run = asyncio.create_task(client.run())
        await col.wait(lambda n, k: n == "frame", 20)
        e2e_ok = True
        await client.close()
        await run
    except Exception as e:
        print("HATA baştan sona bağlantı:", repr(e))
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    print("ok  " if e2e_ok else "HATA", "varsayılan servislerle baştan sona bağlantı ve ilk kare")
    print(f"\nÖZET: tanışma sunucusu {brokers_ok}/{len(results)}, STUN {stun_ok}, bağlantı {e2e_ok}")
    return 0 if (brokers_ok >= 1 and stun_ok and e2e_ok) else 1


if __name__ == "__main__":
    fix_console()
    sys.exit(asyncio.run(main()))
