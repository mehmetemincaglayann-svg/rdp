"""Ayar dosyası: kimlik (ID), aktarma sunucusu adresi, kalıcı parola anahtarı."""
import json
import os
import secrets
import sys
from pathlib import Path

DEFAULT_RELAY = os.environ.get("PYREMOTE_RELAY", "ws://127.0.0.1:8765")


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
        changed = False
        if not self.data.get("host_id"):
            self.data["host_id"] = new_host_id()
            changed = True
        if not self.data.get("host_secret"):
            self.data["host_secret"] = secrets.token_hex(32)
            changed = True
        if changed:
            self.save()

    def save(self):
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=2), "utf-8")
        os.replace(tmp, self.path)

    @property
    def relay(self) -> str:
        return self.data.get("relay") or DEFAULT_RELAY

    @relay.setter
    def relay(self, value: str):
        self.data["relay"] = value
        self.save()

    @property
    def host_id(self) -> str:
        return self.data["host_id"]

    @property
    def host_secret(self) -> str:
        return self.data["host_secret"]

    def regenerate_id(self):
        self.data["host_id"] = new_host_id()
        self.data["host_secret"] = secrets.token_hex(32)
        # Kalıcı parola anahtarı ID'ye bağlıdır; ID değişince geçersiz olur.
        self.data.pop("permanent_key", None)
        self.save()

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
