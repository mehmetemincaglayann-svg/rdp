"""Uçtan uca şifreleme.

Kimlik doğrulama SPAKE2 (parola doğrulamalı anahtar değişimi) ile yapılır:
aktarma sunucusu ne parolayı ne de oturum anahtarını öğrenebilir, araya
giren biri de parolayı çevrimdışı deneme-yanılma ile kıramaz. Oturum
trafiği ChaCha20-Poly1305 ile şifrelenir.

Ana makinenin iki parola "yuvası" vardır: tek kullanımlık oturum parolası
ve (ayarlanmışsa) gözetimsiz erişim için kalıcı parola. İzleyici girdiği
parolayı ikisine karşı aynı anda dener.
"""
import asyncio
import hashlib
import hmac
import json
import secrets
import struct

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from spake2 import SPAKE2_A, SPAKE2_B

SLOTS = ("session", "permanent")
HANDSHAKE_TIMEOUT = 20
PASSWORD_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"


class AuthError(Exception):
    pass


def new_session_password(length: int = 8) -> str:
    return "".join(secrets.choice(PASSWORD_ALPHABET) for _ in range(length))


def stretch_password(password: str, host_id: str) -> bytes:
    """Parolayı ID ile tuzlayıp güçlendirir. Diskte yalnızca bu değer saklanır."""
    return hashlib.scrypt(password.encode("utf-8"),
                          salt=b"pyremote-v1:" + host_id.encode(),
                          n=2 ** 14, r=8, p=1, dklen=32)


def _confirm(key: bytes, label: bytes) -> str:
    return hmac.new(key, label, hashlib.sha256).hexdigest()


def _derive(key: bytes, label: bytes) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=label).derive(key)


async def _recv_json(ws) -> dict:
    raw = await asyncio.wait_for(ws.recv(), HANDSHAKE_TIMEOUT)
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    obj = json.loads(raw)
    if not isinstance(obj, dict):
        raise AuthError("geçersiz el sıkışma iletisi")
    return obj


async def viewer_handshake(ws, password: str, host_id: str) -> "SecureChannel":
    pw = stretch_password(password, host_id)
    sides = {s: SPAKE2_A(pw, idA=b"viewer", idB=b"host:" + s.encode()) for s in SLOTS}
    await ws.send(json.dumps({"v": 1, "msgs": {s: sides[s].start().hex() for s in SLOTS}}))
    reply = await _recv_json(ws)
    if not reply.get("ok", True):
        raise AuthError(reply.get("error", "reddedildi"))
    keys = {}
    try:
        for s in SLOTS:
            keys[s] = sides[s].finish(bytes.fromhex(reply["msgs"][s]))
    except Exception as e:  # bozuk ileti
        raise AuthError("geçersiz el sıkışma iletisi") from e
    await ws.send(json.dumps({"confirm": {s: _confirm(keys[s], b"viewer-confirm") for s in SLOTS}}))
    result = await _recv_json(ws)
    if not result.get("ok"):
        raise AuthError(result.get("error", "wrong_password"))
    slot = result.get("slot")
    if slot not in keys or not hmac.compare_digest(
            _confirm(keys[slot], b"host-confirm"), str(result.get("confirm"))):
        raise AuthError("ana makine doğrulanamadı")
    return SecureChannel(ws, keys[slot], is_host=False, slot=slot)


async def host_handshake(ws, passwords: dict, guard: "AuthGuard") -> "SecureChannel":
    """passwords: {"session": bytes, "permanent": bytes | None} (güçlendirilmiş)."""
    hello = await _recv_json(ws)
    if guard.locked():
        await ws.send(json.dumps({"ok": False, "error": "locked"}))
        raise AuthError("locked")
    keys, out = {}, {}
    try:
        for s in SLOTS:
            # Ayarlanmamış yuva için rastgele parola: hiçbir giriş eşleşmez.
            pw = passwords.get(s) or secrets.token_bytes(32)
            side = SPAKE2_B(pw, idA=b"viewer", idB=b"host:" + s.encode())
            out[s] = side.start().hex()
            keys[s] = side.finish(bytes.fromhex(hello["msgs"][s]))
    except Exception as e:
        raise AuthError("geçersiz el sıkışma iletisi") from e
    await ws.send(json.dumps({"ok": True, "msgs": out}))
    confirm = (await _recv_json(ws)).get("confirm") or {}
    for s in SLOTS:
        if passwords.get(s) and hmac.compare_digest(
                _confirm(keys[s], b"viewer-confirm"), str(confirm.get(s))):
            guard.success()
            await ws.send(json.dumps({"ok": True, "slot": s,
                                      "confirm": _confirm(keys[s], b"host-confirm")}))
            return SecureChannel(ws, keys[s], is_host=True, slot=s)
    guard.fail()
    await ws.send(json.dumps({"ok": False, "error": "wrong_password"}))
    raise AuthError("wrong_password")


class AuthGuard:
    """Art arda hatalı parola denemelerinde girişleri geçici olarak kilitler."""

    def __init__(self, max_fails: int = 5, base_lock: float = 300.0, clock=None):
        import time
        self._clock = clock or time.monotonic
        self.max_fails = max_fails
        self.base_lock = base_lock
        self.fails = 0
        self.locked_until = 0.0

    def locked(self) -> bool:
        return self._clock() < self.locked_until

    def fail(self):
        self.fails += 1
        if self.fails >= self.max_fails:
            extra = self.fails - self.max_fails
            self.locked_until = self._clock() + min(self.base_lock * (2 ** extra), 3600.0)

    def success(self):
        self.fails = 0
        self.locked_until = 0.0


class SecureChannel:
    """Sıralı, kimliği doğrulanmış şifreli ileti kanalı (websocket üzerinde)."""

    def __init__(self, ws, key: bytes, is_host: bool, slot: str = "session"):
        self.ws = ws
        self.slot = slot
        h2v, v2h = _derive(key, b"pyremote h2v"), _derive(key, b"pyremote v2h")
        self._tx = ChaCha20Poly1305(h2v if is_host else v2h)
        self._rx = ChaCha20Poly1305(v2h if is_host else h2v)
        self._tx_n = 0
        self._rx_n = 0
        self._lock = asyncio.Lock()

    async def send(self, mtype: int, payload: bytes = b""):
        async with self._lock:  # sayaç sırası ile gönderim sırası aynı kalmalı
            nonce = struct.pack(">IQ", 0, self._tx_n)
            self._tx_n += 1
            await self.ws.send(self._tx.encrypt(nonce, bytes([mtype]) + payload, None))

    async def send_json(self, obj: dict):
        await self.send(1, json.dumps(obj, separators=(",", ":")).encode("utf-8"))

    async def recv(self) -> tuple[int, bytes]:
        data = await self.ws.recv()
        if not isinstance(data, bytes):
            raise AuthError("beklenmeyen ileti")
        nonce = struct.pack(">IQ", 0, self._rx_n)
        self._rx_n += 1
        try:
            plain = self._rx.decrypt(nonce, data, None)
        except InvalidTag as e:
            raise AuthError("şifre çözme hatası") from e
        return plain[0], plain[1:]

    async def close(self):
        try:
            await self.ws.close()
        except Exception:
            pass
