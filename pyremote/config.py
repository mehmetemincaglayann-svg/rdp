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
