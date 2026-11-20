"""Ayar dosyası: kimlik (ID), aktarma sunucusu adresi, kalıcı parola anahtarı."""
import json
import os
import secrets
import sys
from pathlib import Path

# Tanışma için kullanılan herkese açık ücretsiz MQTT sunucuları (WebSocket üzerinden).
DEFAULT_BROKERS = [
    "wss://broker.hivemq.com:8884/mqtt",
    "wss://broker.emqx.io:8084/mqtt",
    "wss://test.mosquitto.org:8081/mqtt",
]
# Genel IP adresini öğrenmek için STUN sunucuları. İsteğe bağlı olarak buraya
# {"urls": "turn:...", "username": "...", "credential": "..."} biçiminde bir TURN
# sunucusu da eklenebilir (doğrudan bağlantının kurulamadığı ağlar için).
DEFAULT_DIRECT_PORT = 47600   # doğrudan (IP:port) bağlantı için dinlenen TCP bağlantı noktası
DEFAULT_ICE_SERVERS = [
    {"urls": ["stun:stun.l.google.com:19302", "stun:stun1.l.google.com:19302"]},
    {"urls": ["stun:stun.cloudflare.com:3478"]},
]


def config_dir() -> Path:
    if os.environ.get("PYREMOTE_CONFIG_DIR"):
        d = Path(os.environ["PYREMOTE_CONFIG_DIR"])
    elif sys.platform == "win32":
        d = Path(os.environ.get("APPDATA", Path.home())) / "PyRemote"
    else:
        d = Path.home() / ".config" / "pyremote"
    d.mkdir(parents=True, exist_ok=True)
    return d


def new_host_id() -> str:
    return str(secrets.randbelow(900_000_000) + 100_000_000)


def format_id(host_id: str) -> str:
    return " ".join(host_id[i:i + 3] for i in range(0, len(host_id), 3))


def normalize_id(text: str) -> str:
    return "".join(c for c in text if c.isdigit())


class Config:
    def __init__(self, path: Path | None = None):
        self.path = path or (config_dir() / "config.json")
        self.data = {}
        if self.path.exists():
            try:
                self.data = json.loads(self.path.read_text("utf-8"))
            except (OSError, ValueError):
                self.data = {}
        if not self.data.get("host_id"):
            self.data["host_id"] = new_host_id()
            self.save()

    def save(self):
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=2), "utf-8")
        os.replace(tmp, self.path)

    @property
    def brokers(self) -> list[str]:
        return self.data.get("brokers") or DEFAULT_BROKERS

    @property
    def ice_servers(self) -> list[dict]:
        v = self.data.get("ice_servers")
        return v if isinstance(v, list) else DEFAULT_ICE_SERVERS

    @property
    def direct_port(self) -> int:
        """0 verilirse doğrudan bağlantı dinleme kapatılır."""
        v = self.data.get("direct_port")
        return v if isinstance(v, int) else DEFAULT_DIRECT_PORT

    @property
    def host_id(self) -> str:
        return self.data["host_id"]

    @property
    def permanent_key(self) -> bytes | None:
        v = self.data.get("permanent_key")
        return bytes.fromhex(v) if v else None

    @permanent_key.setter
    def permanent_key(self, key: bytes | None):
        if key is None:
            self.data.pop("permanent_key", None)
        else:
            self.data["permanent_key"] = key.hex()
        self.save()


def parse_target(text: str):
    """Kullanıcının yazdığı hedefi çözer.

    9 haneli ID -> ("id", "123456789")
    IP ya da alan adı, isteğe bağlı :port -> ("direct", ("192.168.1.20", 47600))
    Geçersizse None.
    """
    text = text.strip()
    digits = normalize_id(text)
    if digits and all(c.isdigit() or c.isspace() or c == "-" for c in text):
        return ("id", digits) if len(digits) == 9 else None
    if not text or any(c.isspace() for c in text) or "/" in text:
        return None
    host, sep, port = text.rpartition(":")
    if not sep:
        return ("direct", (text, DEFAULT_DIRECT_PORT))
    if not host or not port.isdigit() or not 0 < int(port) < 65536:
        return None
    return ("direct", (host, int(port)))


def local_ip() -> str | None:
    """Bu bilgisayarın yerel ağdaki IP adresi (paket gönderilmez)."""
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("192.0.2.1", 9))
        return s.getsockname()[0]
    except OSError:
        return None
    finally:
        s.close()
