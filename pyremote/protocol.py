"""Şifreli kanal içindeki ileti türleri.

Her ileti: 1 bayt tür + gövde.
  MSG_JSON  : UTF-8 JSON kontrol iletisi ({"t": "..."} alanı türü belirtir)
  MSG_FRAME : FRAME_HDR + JPEG (ekranın değişen dikdörtgeni)
  MSG_CHUNK : CHUNK_HDR (aktarım no) + dosya verisi
"""
import struct

MSG_JSON = 1
MSG_FRAME = 2
MSG_CHUNK = 3

# seq, x, y, w, h, tam genişlik, tam yükseklik
FRAME_HDR = struct.Struct(">IHHHHHH")
CHUNK_HDR = struct.Struct(">I")

CHUNK_SIZE = 64 * 1024
MAX_WS_MESSAGE = 16 * 1024 * 1024
MAX_CLIPBOARD = 1024 * 1024
DRIVES = "::drives"  # Windows sürücü listesini temsil eden sanal yol
