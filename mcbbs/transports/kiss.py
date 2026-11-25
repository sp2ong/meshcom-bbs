"""Transport KISS/TCP: BBS laczy sie DO noda (port 8001, node nasluchuje, 1 klient naraz)."""
from __future__ import annotations

import collections
import logging
import socket
import threading

from ..models import Incoming
from ..util import norm_call
from .base import Transport
from .kisscodec import (Deframer, build_ax25, kiss_deslip, kiss_encode,
                        parse_aprs_message, parse_ax25, parse_wx_comment)

log = logging.getLogger(__name__)

TX_RESULTS = {
    2: "odrzucona: znak bazowy inny niz znak noda",
    3: "odrzucona: na nodzie --kiss tx off",
    4: "odrzucona: zla ramka albo za dlugi tekst",
    5: "odrzucona: limit 8 ramek/s",
}


class KissTransport(Transport):
    def __init__(self, host: str, port: int, mycall: str, node_call: str = "") -> None:
        super().__init__()
        self.host, self.port = host, port
        self.mycall = norm_call(mycall)
        self.node_call = norm_call(node_call)
        self._sock = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self._nn = 0
        self._pending_tx = collections.deque()   # klucze czekajace na ramke wyniku 0xF0 (FIFO)
        self._real_src = None                    # z ramki 0x20 SrcInfo dla nastepnej ramki 0x00

    @property
    def connected(self) -> bool:
        return self._sock is not None

    # --- obsluga ramek (osobno od gniazda, zeby dalo sie testowac) ---------------
    def handle_frame(self, f: bytes) -> None:
        """f = ramka po zdjeciu escapowania: [type][dane]."""
        t, d = f[0], f[1:]
        if t == 0x20:                         # SrcInfo: prawdziwy znak zrodla (SSID -16..-99)
            self._real_src = d.decode("latin1", "replace").strip()
            return
        if t == 0xF0:                         # wynik nadania naszej ramki
            self._tx_result(d)
            return
        if t != 0x00:                         # 0x10 RxMeta i reszta: ignorujemy
            return
        override, self._real_src = self._real_src, None
        try:
            src, _dst, _digis, info = parse_ax25(d)
        except ValueError as e:
            log.debug("KISS: zla ramka AX.25: %s", e)
            return
        src = override or src
        if self.on_heard:
            self.on_heard(src)
        if self.on_telemetry and norm_call(src) == (self.node_call or self.mycall):
            wx = parse_wx_comment(info)
            if wx:
                self.on_telemetry(wx)
        msg = parse_aprs_message(info)
        if not msg:
            return
        if msg["is_ack"]:
            # node przepisuje ackNN na nasz oryginalny numer {nn; adresat = znak, pod ktorym nadawalismy
            if msg["text"].startswith("ack") and msg["addressee"].upper() == self.mycall and self.on_ack:
                self.on_ack(int(msg["text"][3:]))
            return
        if self.on_message:
            self.on_message(Incoming(src=src, dst=msg["addressee"], text=msg["text"],
                                     msgno=msg["msgno"]))

    def _tx_result(self, d: bytes) -> None:
        status = d[0] if d else 0
        key = self._pending_tx.popleft() if self._pending_tx else None
        ok = status == 1
        info = "przyjeta do kolejki TX" if ok else TX_RESULTS.get(status, "status %d" % status)
        if key is not None and self.on_tx_result:
            self.on_tx_result(key, ok, info)

    # --- wysylanie ----------------------------------------------------------------
    def _send_raw(self, ax25: bytes) -> None:
        if self._sock is None:
            raise ConnectionError("KISS niepolaczony")
        self._sock.sendall(kiss_encode(ax25))

    def send(self, dst: str, text: str) -> int:
        dst = norm_call(dst)
        if len(dst) > 9:
            raise ValueError("adresat dluzszy niz 9 znakow")
        with self._lock:
            nn = self._nn % 99999 + 1
            info = ":%-9s:%s{%d" % (dst, text, nn)
            self._send_raw(build_ax25(self.mycall, "APRS", info))
            self._nn = nn
            self._pending_tx.append(nn)
        return nn

    def send_ack(self, dst: str, msgno: str) -> None:
        with self._lock:
            self._send_raw(build_ax25(self.mycall, "APRS", ":%-9s:ack%s" % (norm_call(dst), msgno)))
            self._pending_tx.append(None)     # node odpowie 0xF0 takze na to

    # --- polaczenie ---------------------------------------------------------------
    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="kiss", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        with self._lock:
            if self._sock is not None:
                try:
                    self._sock.close()
                except OSError:
                    pass

    def _run(self) -> None:
        backoff = 1
        while not self._stop.is_set():
            try:
                s = socket.create_connection((self.host, self.port), timeout=10)
                s.settimeout(None)
                with self._lock:
                    self._sock = s
                    self._pending_tx.clear()
                    self._real_src = None
                log.info("KISS: polaczono z %s:%s", self.host, self.port)
                backoff = 1
                deframer = Deframer()
                while True:
                    data = s.recv(4096)
                    if not data:
                        break
                    for raw in deframer.feed(data):
                        f = kiss_deslip(raw)
                        if f:
                            try:
                                self.handle_frame(f)
                            except Exception:   # noqa: blad obslugi nie moze zabic polaczenia
                                log.exception("KISS: blad obslugi ramki")
                log.warning("KISS: node zamknal polaczenie")
            except OSError as e:
                log.warning("KISS: %s (ponowie za %ss)", e, backoff)
            finally:
                with self._lock:
                    if self._sock is not None:
                        try:
                            self._sock.close()
                        except OSError:
                            pass
                        self._sock = None
            if self._stop.wait(backoff):
                break
            backoff = min(backoff * 2, 30)
