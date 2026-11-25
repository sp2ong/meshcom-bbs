from __future__ import annotations

from typing import Any, Callable, Optional


class Transport:
    """Wspolny interfejs transportow. Reszta BBS nie wie, czy to KISS czy UDP.

    Wywolania zwrotne (ustawia je BBS):
      on_message(Incoming)         - przyszla wiadomosc tekstowa
      on_ack(key)                  - potwierdzono dostarczenie wiadomosci wyslanej pod kluczem key
      on_tx_result(key, ok, info)  - node przyjal/odrzucil ramke do nadania (tylko KISS)
      on_heard(call)               - slyszano stacje (dowolna ramka, takze pozycja)
      on_telemetry(dict)           - telemetria wlasnego noda (temp1, hum, qfe, qnh, temp2, gas, co2)
    """

    connected: bool = False

    def __init__(self) -> None:
        self.on_message: Optional[Callable[[Any], None]] = None
        self.on_ack: Optional[Callable[[Any], None]] = None
        self.on_tx_result: Optional[Callable[[Any, bool, str], None]] = None
        self.on_heard: Optional[Callable[[str], None]] = None
        self.on_telemetry: Optional[Callable[[dict], None]] = None   # pola telemetrii WLASNEGO noda

    def start(self) -> None:
        raise NotImplementedError

    def stop(self) -> None:
        pass

    def send(self, dst: str, text: str) -> Any:
        """Wysyla DM; zwraca klucz do korelacji z on_ack."""
        raise NotImplementedError

    def send_ack(self, dst: str, msgno: str) -> None:
        """Wysyla APRS-ack dla odebranej wiadomosci. Transport moze tego nie umiec."""
        raise NotImplementedError
