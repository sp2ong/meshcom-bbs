from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field, fields
from typing import Optional, Tuple


@dataclass
class Config:
    call: str = "N0CALL-12"          # znak, na ktory uzytkownicy wysylaja DM do BBS
    node_call: str = ""              # OPCJONALNY: znak noda, gdy rozni sie od call (tylko KISS); domyslnie = call
    prefixes: Tuple[str, ...] = ()   # opcjonalne slowa przed komenda (np. "bbs"); znak BBS tez dziala
    require_prefix: bool = False     # true = reaguj tylko na wiadomosci z prefiksem (wspoldzielony node)
    db: str = "mcbbs.sqlite3"
    retention_bulletin_hours: float = 720   # tablica ogolna: 30 dni
    retention_private_hours: float = 168    # poczta prywatna (tez nieprzeczytana): 7 dni
    max_private: int = 5             # max wiadomosci prywatnych w skrzynce uzytkownika
    max_bulletins_shown: int = 5
    max_body_chars: int = 600
    draft_autosave_s: float = 180.0   # cisza w trybie pisania tyle sekund -> zapisz, co jest (brak END)
    frame_bytes: int = 147           # max bajtow tekstu w ramce; firmware: {ADRESAT}tekst <= 160, wiec 147 pasuje do kazdego znaku
    line_sep: str = " | "
    pace_s: float = 6.0              # min. odstep miedzy ramkami nadawanymi przez BBS
    ack_timeout_s: float = 90.0
    reply_attempts: int = 3
    retry_delays_s: Tuple[float, ...] = (60, 300, 1800)
    ack_incoming: Optional[bool] = None   # None = auto (patrz send_acks): czy BBS sam wysyla :ackNN
    dedupe_window_s: float = 900
    max_replies_per_hour: int = 30
    notify_interval_h: float = 8
    notify_min_gap_s: float = 600
    notify_all_ssids: bool = True        # powiadomienie na wszystkie SSID widziane ostatnio (polka + teren)
    notify_addr_days: float = 7          # ...z ostatnich tylu dni
    notify_max_addrs: int = 3            # ...ale najwyzej tyle adresow
    js8: dict = field(default_factory=dict)   # sekcja [js8call] (bramka JS8Call), pusta = wylaczona
    rules_text: str = "Tylko lacznosc amatorska, bez szyfrowania i reklam."

    @property
    def own_node_call(self) -> str:
        """Znak noda: gdy nie podano, BBS dziala pod znakiem noda."""
        return self.node_call or self.call

    @property
    def send_acks(self) -> bool:
        """Firmware sam potwierdza (ACK) DM adresowane dokladnie do wlasnego znaku noda (RF i serwer),
        wiec BBS pod znakiem noda nie moze potwierdzac drugi raz. Pod innym SSID - musi sam."""
        if self.ack_incoming is not None:
            return self.ack_incoming
        return self.call.strip().upper() != self.own_node_call.strip().upper()


def load(path: str):
    with open(path, "rb") as f:
        raw = tomllib.load(f)
    bbs = dict(raw.get("bbs", {}))
    known = {f.name for f in fields(Config)}
    unknown = set(bbs) - known
    if unknown:
        raise ValueError("nieznane opcje w [bbs]: %s" % ", ".join(sorted(unknown)))
    for key in ("prefixes", "retry_delays_s"):
        if key in bbs:
            bbs[key] = tuple(bbs[key])
    js8 = dict(raw.get("js8call", {}))
    bad = set(js8) - {"host", "port", "groups", "urgent", "private", "urgent_send", "urgent_dm",
                          "urgent_group", "urgent_groups", "urgent_repeat", "urgent_max_per_hour"}
    if bad:
        raise ValueError("nieznane opcje w [js8call]: %s" % ", ".join(sorted(bad)))
    for key in ("groups", "urgent", "private"):
        js8[key] = tuple(str(g).strip().upper() for g in js8.get(key, ()))
    nums = [js8.get("urgent_group", 9)] + list(js8.get("urgent_groups", ()))
    for n in nums:
        if not re.fullmatch(r"\d{1,5}", str(n)):
            raise ValueError("[js8call] numer grupy MeshCom ma 1-5 cyfr, jest: %r" % (n,))
    js8["urgent_group"] = int(js8.get("urgent_group", 9))
    js8["urgent_groups"] = tuple(int(g) for g in js8.get("urgent_groups", ()))
    cfg = Config(**bbs)
    cfg.js8 = js8
    return cfg, dict(raw.get("transport", {}))
