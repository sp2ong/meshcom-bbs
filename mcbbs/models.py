from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class Incoming:
    """Wiadomosc tekstowa odebrana z noda (niezaleznie od transportu)."""

    src: str                      # pelny znak nadawcy z SSID (prawdziwy, nie zaciskany do -15)
    dst: str                      # adresat: znak, numer grupy albo '' dla broadcastu
    text: str                     # tresc BEZ koncowki {nn
    msgno: Optional[str] = None   # numer {nn nadany przez nadawce (potrzebny do ACK)
    msg_id: Optional[str] = None  # msg_id z ext-UDP, jesli jest
