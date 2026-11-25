"""Narzedzia: znaki wywolawcze i dzielenie odpowiedzi na ramki."""
from __future__ import annotations

import re
from typing import Iterable, List

CALL_RE = re.compile(r"^[A-Z0-9]{1,3}\d[A-Z]{1,4}(-\d{1,2})?$")


def norm_call(call: str) -> str:
    return (call or "").strip().upper()


def base_call(call: str) -> str:
    return norm_call(call).split("-", 1)[0]


def valid_callsign(call: str) -> bool:
    return bool(CALL_RE.match(norm_call(call)))


def blen(s: str) -> int:
    """Dlugosc w bajtach UTF-8 (limit pakietu LoRa liczy sie w bajtach, nie znakach)."""
    return len(s.encode("utf-8"))


def _split_long(text: str, limit: int) -> List[str]:
    """Dzieli jedna dluga linie po spacjach; za dlugie slowa tnie twardo (bez lamania UTF-8)."""
    out: List[str] = []
    cur = ""
    for word in text.split(" "):
        cand = word if not cur else cur + " " + word
        if blen(cand) <= limit:
            cur = cand
            continue
        if cur:
            out.append(cur)
            cur = ""
        while blen(word) > limit:
            head = ""
            for ch in word:
                if blen(head + ch) > limit:
                    break
                head += ch
            out.append(head)
            word = word[len(head):]
        cur = word
    if cur:
        out.append(cur)
    return out


def chunk_lines(lines: Iterable[str], limit: int, sep: str = " | ",
                suffix_reserve: int = 8) -> List[str]:
    """Pakuje linie w ramki <= limit bajtow, laczac je separatorem.

    Gdy trzeba kilku ramek, dopisuje ' (n/m)' (stad suffix_reserve).
    """
    if limit < 16:
        raise ValueError("limit ramki za maly")
    lines = [ln.strip() for ln in lines if ln and ln.strip()]

    def pack(lim: int) -> List[str]:
        frames: List[str] = []
        cur = ""
        for line in lines:
            parts = [line] if blen(line) <= lim else _split_long(line, lim)
            for part in parts:
                cand = part if not cur else cur + sep + part
                if blen(cand) <= lim:
                    cur = cand
                else:
                    frames.append(cur)
                    cur = part
        if cur:
            frames.append(cur)
        return frames

    frames = pack(limit)
    if len(frames) > 1:
        frames = pack(limit - suffix_reserve)
        n = len(frames)
        frames = ["%s (%d/%d)" % (f, i, n) for i, f in enumerate(frames, 1)]
    return frames
