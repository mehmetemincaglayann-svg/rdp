"""WebRTC veri kanalını, şifreli kanalın beklediği basit ileti arayüzüne uyarlar."""
import asyncio

FRAGMENT = 16000                 # veri kanalı iletileri küçük parçalara bölünür
HIGH_WATER = 1024 * 1024         # gönderim kuyruğu bunu aşarsa beklenir
LOW_WATER = 256 * 1024


class Closed(Exception):
    """Bağlantı kapandı."""


class DataChannelTransport:
    def __init__(self, pc, channel):
        self.pc = pc
        self.ch = channel
        self._q = asyncio.Queue()
        self._parts = []
        self._closed = False
        self._open = asyncio.Event()
        self._low = asyncio.Event()
        channel.bufferedAmountLowThreshold = LOW_WATER
        if channel.readyState == "open":
            self._open.set()
        channel.on("open", self._open.set)
        channel.on("message", self._on_message)
        channel.on("close", self._on_close)
        channel.on("bufferedamountlow", self._low.set)
        pc.on("connectionstatechange", self._on_state)

    def _on_state(self):
        if self.pc.connectionState in ("failed", "closed"):
            self._on_close()

    def _on_message(self, data):
        if isinstance(data, str):
            data = data.encode("utf-8")
        self._parts.append(data[1:])
        if data[:1] == b"\x01":          # son parça
            self._q.put_nowait(b"".join(self._parts))
            self._parts = []

    def _on_close(self):
        if not self._closed:
            self._closed = True
            self._q.put_nowait(None)
            self._low.set()
            self._open.set()

    async def wait_open(self, timeout: float) -> bool:
        try:
            await asyncio.wait_for(self._open.wait(), timeout)
        except asyncio.TimeoutError:
            return False
        return not self._closed and self.ch.readyState == "open"

    async def send(self, data):
        if isinstance(data, str):
            data = data.encode("utf-8")
        for i in range(0, max(len(data), 1), FRAGMENT):
            if self._closed or self.ch.readyState != "open":
                raise Closed()
            last = i + FRAGMENT >= len(data)
            try:
                self.ch.send((b"\x01" if last else b"\x00") + data[i:i + FRAGMENT])
            except Exception as e:
                raise Closed() from e
            # Karşı taraf yetişemiyorsa belleği doldurma: kuyruk boşalana kadar bekle.
            while self.ch.bufferedAmount > HIGH_WATER and not self._closed:
                self._low.clear()
                try:
                    await asyncio.wait_for(self._low.wait(), 2)
                except asyncio.TimeoutError:
                    pass

    async def recv(self) -> bytes:
        item = await self._q.get()
        if item is None:
            self._q.put_nowait(None)
            raise Closed()
        return item

    async def close(self):
        # Son iletiler (ör. "parola hatalı" yanıtı) yola çıkmadan bağlantıyı koparma.
        if not self._closed and self.ch.readyState == "open":
            for _ in range(40):
                if self.ch.bufferedAmount == 0:
                    break
                await asyncio.sleep(0.05)
            await asyncio.sleep(0.15)
        self._on_close()
        try:
            self.ch.close()
        except Exception:
            pass
        try:
            await self.pc.close()
        except Exception:
            pass

    def peer_address(self) -> str:
        """Karşı tarafın IP adresi (bulunamazsa '?')."""
        try:
            conn = self.pc.sctp.transport.transport._connection
            return str(conn._nominated[1].remote_addr[0])
        except Exception:
            return "?"
