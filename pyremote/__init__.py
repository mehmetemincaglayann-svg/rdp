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
