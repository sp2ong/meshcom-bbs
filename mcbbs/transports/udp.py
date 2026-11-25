"""Transport ext-UDP: node wysyla JSON na port 1799 tego hosta, BBS odpowiada na port 1799 noda.

Na nodzie: --extudp on  oraz  --extudpip <IP_tego_hosta>
UDP nie zwraca msg_id po wyslaniu, wiec klucz ACK korelujemy z echem 'src_type':'node'
(to samo msg_id wraca potem w ramce {"type":"ack",...}).
"""
from __future__ import annotations

import json
import logging
import re
import socket
import threading
import time

from ..models import Incoming
from ..util import norm_call
from .base import Transport

log = logging.getLogger(__name__)

MSGNO = re.compile(r"^(.*)\{(\d{1,5})$", re.S)
# Node dubluje ACK jako zwykla wiadomosc z trescia 'SP2ONG-1 :ack305' - ignorujemy,
# bo wlasciwy ACK przychodzi osobnym JSON-em type=ack.
PSEUDO_ACK = re.compile(r"^\S+\s+:(?:ack|rej)\d{1,5}$")


TELE_FIELDS = ("temp1", "temp2", "hum", "qfe", "qnh", "gas", "co2")


def split_msgno(msg: str):
    m = MSGNO.match(msg)
    return (m.group(1), m.group(2)) if m else (msg, None)


class UdpTransport(Transport):
    def __init__(self, node_host: str, node_port: int = 1799, listen_port: int = 1799,
                 node_call: str = "", ack_statuses=(2,)) -> None:
        super().__init__()
        self.node_host, self.node_port, self.listen_port = node_host, node_port, listen_port
        self.node_call = norm_call(node_call)
        self.ack_statuses = tuple(ack_statuses)   # w logach widzielismy tylko status 2
        self._sock = None
        self._stop = threading.Event()
        self._thread = None
        self._lock = threading.Lock()
        self._n = 0
        self._pending = []        # [(dst, text, key, ts)] wyslane, czekaja na echo z msg_id
        self._by_msg_id = {}      # msg_id -> key

    connected = True              # UDP nie ma polaczenia

    # --- JSON z noda (osobno od gniazda, zeby dalo sie testowac) -------------------
    def handle_json(self, obj: dict) -> None:
        t = obj.get("type")
        if t == "tele":
            own = obj.get("src_type", "node") == "node" and (
                not self.node_call or not obj.get("src") or norm_call(obj["src"]) == self.node_call)
            if own and self.on_telemetry:
                fields = {k: obj[k] for k in TELE_FIELDS if isinstance(obj.get(k), (int, float))}
                if fields:
                    self.on_telemetry(fields)          # int 0 = brak czujnika, liczba z przecinkiem = odczyt
            return
        if t == "pos":
            if self.on_heard and obj.get("src"):
                self.on_heard(obj["src"])
            return
        if t == "ack":
            if obj.get("status") in self.ack_statuses:
                with self._lock:
                    key = self._by_msg_id.pop(str(obj.get("msg_id", "")).upper(), None)
                if key is not None and self.on_ack:
                    self.on_ack(key)
            return
        if t != "msg":
            return
        src, dst, raw = obj.get("src", ""), obj.get("dst", ""), obj.get("msg", "")
        if PSEUDO_ACK.match(raw):
            return
        text, msgno = split_msgno(raw)
        if self._match_echo(src, dst, text, obj.get("msg_id")):
            return
        if self.on_heard and src:
            self.on_heard(src)
        if self.on_message:
            self.on_message(Incoming(src=src, dst="" if dst == "*" else dst, text=text,
                                     msgno=msgno, msg_id=obj.get("msg_id")))

    def _match_echo(self, src, dst, text, msg_id) -> bool:
        if not msg_id or (self.node_call and norm_call(src) != self.node_call):
            return False
        with self._lock:
            now = time.time()
            self._pending = [p for p in self._pending if now - p[3] < 120]
            for p in self._pending:
                if p[0] == norm_call(dst) and p[1] == text:
                    self._pending.remove(p)
                    self._by_msg_id[str(msg_id).upper()] = p[2]
                    return True
        return False

    # --- wysylanie ----------------------------------------------------------------
    def send(self, dst: str, text: str) -> str:
        payload = json.dumps({"type": "msg", "dst": dst, "msg": text}, ensure_ascii=False)
        with self._lock:
            self._n += 1
            key = "u%d" % self._n
            self._pending.append((norm_call(dst), text, key, time.time()))
        self._sock.sendto(payload.encode("utf-8"), (self.node_host, self.node_port))
        return key

    def send_ack(self, dst: str, msgno: str) -> None:
        # Format ACK-a wysylanego przez ext-UDP nie jest opisany w dokumentacji.
        raise NotImplementedError("ext-UDP nie ma udokumentowanego wysylania ACK")

    # --- gniazdo ------------------------------------------------------------------
    def start(self) -> None:
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("0.0.0.0", self.listen_port))
        self._sock.settimeout(1.0)
        self._thread = threading.Thread(target=self._run, name="udp", daemon=True)
        self._thread.start()
        log.info("UDP: nasluch na porcie %d, node %s:%d", self.listen_port, self.node_host, self.node_port)

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                data, _addr = self._sock.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                self.handle_json(json.loads(data.decode("utf-8", "replace")))
            except ValueError:
                log.debug("UDP: nie-JSON: %r", data[:80])
            except Exception:   # noqa
                log.exception("UDP: blad obslugi pakietu")
