"""Kodowanie KISS i AX.25 UI zgodne z docs/kiss_tcp_protocol.md firmware MeshCom."""
from __future__ import annotations

import re
from typing import List, Optional, Tuple

FEND, FESC, TFEND, TFESC = 0xC0, 0xDB, 0xDC, 0xDD


def kiss_encode(payload: bytes, ftype: int = 0x00) -> bytes:
    out = bytearray([FEND, ftype])
    for x in payload:
        if x == FEND:
            out += bytes([FESC, TFEND])
        elif x == FESC:
            out += bytes([FESC, TFESC])
        else:
            out.append(x)
    out.append(FEND)
    return bytes(out)


def kiss_deslip(b: bytes) -> bytes:
    out = bytearray()
    esc = False
    for x in b:
        if esc:
            out.append(FEND if x == TFEND else FESC if x == TFESC else x)
            esc = False
        elif x == FESC:
            esc = True
        else:
            out.append(x)
    return bytes(out)


class Deframer:
    """Sklada ramki ze strumienia TCP (ramka = bajty miedzy FEND)."""

    def __init__(self) -> None:
        self.buf = bytearray()

    def feed(self, data: bytes) -> List[bytes]:
        frames = []
        for b in data:
            if b == FEND:
                if self.buf:
                    frames.append(bytes(self.buf))
                    self.buf.clear()
            else:
                self.buf.append(b)
        return frames


def enc_addr(call: str, last: bool, top: int = 0x00) -> bytes:
    base, _, ssid_s = call.upper().partition("-")
    ssid = int(ssid_s or 0)
    if len(base) > 6 or not 0 <= ssid <= 15:
        raise ValueError("znak %r nie miesci sie w AX.25 (<=6 znakow, SSID 0-15)" % call)
    base = (base + "      ")[:6]
    return bytes((ord(c) << 1) & 0xFF for c in base) + bytes(
        [top | 0x60 | (ssid << 1) | (1 if last else 0)])


def dec_addr(a: bytes) -> str:
    call = "".join(chr(x >> 1) for x in a[:6]).strip()
    ssid = (a[6] >> 1) & 0x0F
    return "%s-%d" % (call, ssid) if ssid else call


def build_ax25(src: str, dst: str, info: str) -> bytes:
    return (enc_addr(dst, False, 0x80) + enc_addr(src, True) +
            bytes([0x03, 0xF0]) + info.encode("utf-8"))


def parse_ax25(b: bytes) -> Tuple[str, str, List[str], str]:
    """b = dane ramki typu 0x00 (bez bajtu type). Zwraca (src, dst, digipeaterzy, info)."""
    addrs, p = [], 0
    while p + 7 <= len(b):
        addrs.append(b[p:p + 7])
        last = b[p + 6] & 1
        p += 7
        if last:
            break
    else:
        raise ValueError("pole adresowe nie konczy sie bitem ext")
    if len(addrs) < 2:
        raise ValueError("za malo adresow")
    return (dec_addr(addrs[1]), dec_addr(addrs[0]),
            [dec_addr(a) for a in addrs[2:]], b[p + 2:].decode("utf-8", "replace"))


def parse_aprs_message(info: str) -> Optional[dict]:
    """':ADRESAT  :tekst{nn' -> dict albo None (np. dla pozycji)."""
    if not info.startswith(":") or len(info) < 11 or info[10] != ":":
        return None
    addressee = info[1:10].strip()
    text = info[11:]
    msgno = None
    if "{" in text:
        head, _, tail = text.rpartition("{")
        if 1 <= len(tail) <= 5:
            text, msgno = head, tail
    is_ack = text[:3] in ("ack", "rej") and text[3:].isdigit()
    return {"addressee": addressee, "text": text, "msgno": msgno, "is_ack": is_ack}


WX_KEYS = {"T": "temp1", "H": "hum", "P": "qfe", "Q": "qnh"}


def parse_wx_comment(info: str) -> dict:
    """Pozycja APRS wlasnego noda niesie telemetrie w komentarzu: .../P=1022.7/H=47.9/T=18.5/Q=1031.1/...
    Przez KISS nie odroznimy braku czujnika od zera, wiec pola rowne 0 pomijamy."""
    out = {}
    if not info or info[0] not in "!=@/":
        return out
    for key, val in re.findall(r"/([A-Z])=(-?\d+(?:\.\d+)?)", info):
        if key in WX_KEYS and float(val) != 0.0:
            out[WX_KEYS[key]] = float(val)
    return out
