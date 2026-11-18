"""Ağ türü denetimi: bu ağdan doğrudan (P2P) bağlantı kurulabilir mi?

Aynı UDP soketinden iki farklı STUN sunucusuna sorulur. İkisi de aynı genel
bağlantı noktasını görüyorsa modem her hedef için aynı eşlemeyi kullanıyordur
ve NAT delme çalışır ("ok"). Farklı görüyorlarsa ağ "simetrik NAT"tır; karşı
taraf bu bilgisayara hangi bağlantı noktasından ulaşacağını bilemez.
"""
import asyncio
import os
import socket
import struct

STUN_SERVERS = [("stun.l.google.com", 19302), ("stun.cloudflare.com", 3478),
                ("stun1.l.google.com", 19302)]
MAGIC = 0x2112A442

NAT_TEXT = {
    "ok": "doğrudan bağlantıya uygun",
    "symmetric": "simetrik NAT (doğrudan bağlantıya uygun değil)",
    "blocked": "UDP engelli (doğrudan bağlantı kurulamaz)",
    "unknown": "belirlenemedi",
}


def parse_mapped(data: bytes) -> tuple[bytes, tuple[str, int]] | None:
    """STUN yanıtından (işlem no, (genel ip, bağlantı noktası)) çıkarır."""
    if len(data) < 20:
        return None
    mtype, length, magic = struct.unpack_from(">HHI", data)
    if mtype != 0x0101 or magic != MAGIC:
        return None
    txid, pos, end = data[8:20], 20, min(20 + length, len(data))
    while pos + 4 <= end:
        atype, alen = struct.unpack_from(">HH", data, pos)
        value = data[pos + 4:pos + 4 + alen]
        if atype in (0x0020, 0x0001) and len(value) >= 8 and value[1] == 1:   # IPv4
            port, addr = struct.unpack_from(">H", value, 2)[0], value[4:8]
            if atype == 0x0020:   # XOR-MAPPED-ADDRESS
                port ^= MAGIC >> 16
                addr = bytes(a ^ b for a, b in zip(addr, struct.pack(">I", MAGIC)))
            return txid, (socket.inet_ntoa(addr), port)
        pos += 4 + alen + (-alen % 4)
    return None


class _Proto(asyncio.DatagramProtocol):
    def __init__(self):
        self.results = {}   # işlem no -> (ip, bağlantı noktası)

    def datagram_received(self, data, addr):
        parsed = parse_mapped(data)
        if parsed:
            self.results[parsed[0]] = parsed[1]

    def error_received(self, exc):
        pass


async def detect(servers=None, timeout: float = 3.0) -> str:
    """'ok' | 'symmetric' | 'blocked' | 'unknown' döndürür."""
    loop = asyncio.get_running_loop()
    targets = []
    for host, port in servers or STUN_SERVERS:
        try:
            info = await loop.getaddrinfo(host, port, family=socket.AF_INET,
                                          type=socket.SOCK_DGRAM)
            if info[0][4] not in [t for t in targets]:
                targets.append(info[0][4])
        except OSError:
            pass
    if not targets:
        return "unknown"
    try:
        transport, proto = await loop.create_datagram_endpoint(
            _Proto, local_addr=("0.0.0.0", 0), family=socket.AF_INET)
    except OSError:
        return "unknown"
    try:
        txids = [os.urandom(12) for _ in targets]
        end = loop.time() + timeout
        while loop.time() < end and len(proto.results) < len(targets):
            for txid, target in zip(txids, targets):
                if txid not in proto.results:
                    transport.sendto(struct.pack(">HHI", 0x0001, 0, MAGIC) + txid, target)
            await asyncio.sleep(0.4)
        mapped = [proto.results[t] for t in txids if t in proto.results]
    finally:
        transport.close()
    if not mapped:
        return "blocked"
    if len(mapped) < 2:
        return "unknown"
    return "ok" if len(set(mapped)) == 1 else "symmetric"
