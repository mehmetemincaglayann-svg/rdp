"""PyRemote - Python ile yazılmış uzak masaüstü kontrol programı."""
__version__ = "0.1.0"


def fix_console():
    """Windows konsolu Türkçe karakterleri yazamayınca program çökmesin."""
    import sys
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass


def selftest(path: str) -> int:
    """Paketlenmiş .exe içinde bütün bileşenlerin yüklenebildiğini denetler."""
    import traceback
    try:
        import asyncio
        import tkinter  # noqa: F401
        import mss  # noqa: F401
        from PIL import ImageTk  # noqa: F401
        from . import crypto, p2p, signaling  # noqa: F401
        from .host import gui as _h, service  # noqa: F401
        from .viewer import gui as _v  # noqa: F401

        async def offer():
            pc = p2p.new_peer_connection([])
            pc.createDataChannel("x")
            await pc.setLocalDescription(await pc.createOffer())
            sdp = pc.localDescription.sdp
            await pc.close()
            return sdp

        sdp = asyncio.run(offer())
        crypto.stretch_password("a", "1")
        result, code = ("ok" if "webrtc-datachannel" in sdp else "bozuk sdp"), 0
    except Exception:
        result, code = traceback.format_exc(), 1
    with open(path, "w", encoding="utf-8") as f:
        f.write(result)
    return code
