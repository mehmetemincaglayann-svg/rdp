"""Doğrudan (P2P) bağlantı kurma: WebRTC veri kanalı + STUN ile NAT delme."""
import asyncio
import logging
import secrets

from aiortc import (RTCConfiguration, RTCIceServer, RTCPeerConnection,
                    RTCSessionDescription)

from . import nat
from .signaling import Signaling, seal, topic_base, unseal
from .transport import DataChannelTransport

log = logging.getLogger("p2p")

ANSWER_TIMEOUT = 20      # ana makinenin yanıt vermesi için
OPEN_TIMEOUT = 30        # doğrudan bağlantının kurulması için


class P2PError(Exception):
    """Kod: no_signal | offline | p2p_failed. İki tarafın ağ türü de eklenir."""

    def __init__(self, code: str, viewer_nat: str = "unknown", host_nat: str = "unknown"):
        super().__init__(code)
        self.viewer_nat = viewer_nat
        self.host_nat = host_nat


def new_peer_connection(ice_servers: list[dict]) -> RTCPeerConnection:
    servers = [RTCIceServer(**s) for s in ice_servers]
    return RTCPeerConnection(RTCConfiguration(iceServers=servers))


async def viewer_connect(host_id: str, brokers: list[str],
                         ice_servers: list[dict]) -> DataChannelTransport:
    base = topic_base(host_id)
    sid = secrets.token_hex(8)
    loop = asyncio.get_running_loop()
    answer = loop.create_future()

    def on_message(topic, payload):
        msg = unseal(host_id, payload)
        if msg and msg.get("sid") == sid and msg.get("sdp") and not answer.done():
            answer.set_result(msg)

    sig = Signaling(brokers, on_message)
    sig.start()
    pc = new_peer_connection(ice_servers)
    nat_task = asyncio.create_task(nat.detect()) if ice_servers else None
    try:
        transport = DataChannelTransport(pc, pc.createDataChannel("pyremote"))
        # Yerel adres adayları toplanırken tanışma sunucularına da bağlanılır.
        gather = asyncio.create_task(pc.setLocalDescription(await pc.createOffer()))
        if not await sig.wait_connected(15):
            raise P2PError("no_signal")
        await sig.subscribe(f"{base}/answer/{sid}")
        await gather
        offer = seal(host_id, {"sid": sid, "sdp": pc.localDescription.sdp})
        end = loop.time() + ANSWER_TIMEOUT
        while not answer.done() and loop.time() < end:
            # Aynı teklif birkaç kez gönderilir (ana makine yinelenenleri eler).
            await sig.publish(f"{base}/offer", offer)
            await asyncio.wait([answer], timeout=4)
        if not answer.done():
            raise P2PError("offline")
        await pc.setRemoteDescription(RTCSessionDescription(answer.result()["sdp"], "answer"))
        if not await transport.wait_open(OPEN_TIMEOUT):
            mine = await nat_task if nat_task else "unknown"
            raise P2PError("p2p_failed", mine, str(answer.result().get("nat", "unknown")))
        return transport
    except BaseException:
        await pc.close()
        raise
    finally:
        if nat_task:
            nat_task.cancel()
        await sig.stop()


async def host_answer(sig: Signaling, host_id: str, sid: str, offer_sdp: str,
                      ice_servers: list[dict], nat_type: str = "unknown") -> DataChannelTransport:
    """Gelen teklifi yanıtlar ve veri kanalı açılınca taşıyıcıyı döndürür."""
    pc = new_peer_connection(ice_servers)
    opened = asyncio.get_running_loop().create_future()

    @pc.on("datachannel")
    def on_channel(channel):
        if not opened.done():
            opened.set_result(DataChannelTransport(pc, channel))

    try:
        await pc.setRemoteDescription(RTCSessionDescription(offer_sdp, "offer"))
        await pc.setLocalDescription(await pc.createAnswer())
        answer = seal(host_id, {"sid": sid, "sdp": pc.localDescription.sdp, "nat": nat_type})
        await sig.publish(f"{topic_base(host_id)}/answer/{sid}", answer)
        transport = await asyncio.wait_for(opened, OPEN_TIMEOUT)
        if not await transport.wait_open(5):
            raise P2PError("p2p_failed")
        return transport
    except BaseException:
        await pc.close()
        raise
