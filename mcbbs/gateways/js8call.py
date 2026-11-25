"""Bramka JS8Call -> BBS (etap 1: odbior; BBS niczego nie nadaje na KF).

Laczy sie z API JS8Call (TCP, JSON `{"params":{...},"type":"...","value":"..."}`), slucha `RX.DIRECTED`
i zapisuje do bazy BBS. O rodzaju wpisu decyduje ADRESAT (pole TO), tresc ma postac `Tytul : Tresc`:

  * `@GRM Tytul : Tresc`         (groups)  -> wpis na tablicy ogloszen,
  * `@EMC Tytul : Tresc`         (urgent)  -> wpis PILNY, temat z przedrostkiem `EMC:`; przy urgent_send=true
                                              dodatkowo wysylany na grupe MeshCom `urgent_group` (domyslnie 9),
  * `@EMC26024 Tytul : Tresc`              -> jak wyzej, ale na wskazana grupe MeshCom; tylko jesli numer jest
                                              na liscie `urgent_groups`, inaczej wiadomosc jest ignorowana,
  * `@PM ZNAK Tytul : Tresc`     (private) -> poczta prywatna dla uzytkownika BBS o znaku ZNAK.

Wszystko inne jest ignorowane. Nadawca z KF nie jest uwierzytelniony (to znak zdekodowany przez JS8Call),
wiec wpisy sa oznaczone `ZNAK@JS8`.
Format opisu API: https://js8call.com/JS8Call-improved/d7/d15/md_docs_2API.html
"""
from __future__ import annotations

import codecs
import hashlib
import json
import logging
import re
import socket
import threading
import time
from typing import Callable, Optional

from ..commands import _split_subject
from ..config import Config
from ..store import Store
from ..util import base_call, norm_call, valid_callsign

log = logging.getLogger("mcbbs.js8")

END_MARK = re.compile(r"\s*[♢♦◊]\s*$")      # ♢ - znak konca wiadomosci JS8Call
CALL_LIKE = re.compile(r"^[A-Z0-9/]{3,12}$")
TEXT_COMMANDS = {"", "MSG"}                                  # reszta (HEARTBEAT, SNR, GRID, ACK...) to ruch techniczny


class Js8Gateway:
    def __init__(self, cfg: Config, store: Store,
                 urgent_cb: Optional[Callable[[int, str, str, float], None]] = None,
                 forward_cb: Optional[Callable[[int, str, float], bool]] = None,
                 clock=time.time) -> None:
        js8 = cfg.js8
        self.cfg, self.store, self.clock = cfg, store, clock
        self.urgent_cb, self.forward_cb = urgent_cb, forward_cb
        self.host = js8.get("host", "")
        self.port = int(js8.get("port", 2442))
        self.groups = set(js8.get("groups", ()))
        self.urgent = set(js8.get("urgent", ()))
        self.private = set(js8.get("private", ()))
        self.urgent_send = bool(js8.get("urgent_send", False))
        self.urgent_dm = bool(js8.get("urgent_dm", False))
        self.urgent_group = int(js8.get("urgent_group", 9))
        self.allowed_groups = {self.urgent_group} | {int(g) for g in js8.get("urgent_groups", ())}
        self.urgent_re = (re.compile(r"^(?:%s)(\d{1,5})?$" % "|".join(re.escape(t) for t in sorted(self.urgent)))
                          if self.urgent else None)
        self.connected = False
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # ---- logika (bez sieci, latwa do testowania) ------------------------------------
    def handle_message(self, msg: dict, now: Optional[float] = None) -> Optional[str]:
        """Przetwarza jeden obiekt JSON z API. Zwraca krotki opis wyniku (do logu i testow)."""
        now = self.clock() if now is None else now
        typ = msg.get("type", "")
        if typ != "RX.DIRECTED":
            return None
        params = msg.get("params") or {}
        value = str(msg.get("value") or params.get("TEXT") or "")
        frm = str(params.get("FROM") or "").strip().upper()
        to = str(params.get("TO") or "").strip().upper()
        if not frm or not to:
            m = re.match(r"^\s*(\S+?):\s+(\S+)", value)
            if not m:
                return "ignored: brak FROM/TO"
            frm, to = frm or m.group(1).upper(), to or m.group(2).upper()
        cmd = str(params.get("CMD") or "").strip().upper()
        if cmd not in TEXT_COMMANDS:
            return "ignored: komenda %s" % cmd
        if not CALL_LIKE.match(frm):
            return "ignored: nadawca %r" % frm
        body = self._body(value, frm, to, cmd)
        if not body:
            return "ignored: pusta tresc"
        body = body[: self.cfg.max_body_chars]
        ext_id = hashlib.sha1(("%s|%s|%s|%s" % (frm, to, body, params.get("UTC", ""))).encode()).hexdigest()
        if to in self.groups:
            return self._store_group(frm, body, now, ext_id)
        if to in self.private:
            return self._store_private(frm, body, now, ext_id)
        m = self.urgent_re.match(to) if self.urgent_re else None
        if m:
            return self._store_urgent(frm, m.group(1), body, now, ext_id)
        return "ignored: adresat %s" % to

    @staticmethod
    def _body(value: str, frm: str, to: str, cmd: str) -> str:
        text = value.strip()
        for token in (frm + ":", to, cmd):
            if token and text.upper().startswith(token):
                text = text[len(token):].lstrip()
        return END_MARK.sub("", text).strip()

    def _add(self, frm: str, subject: str, text: str, now: float, ext_id: str, urgent: bool = False):
        return self.store.add_message("bulletin", frm, None, subject[:30], text, now,
                                      self.cfg.retention_bulletin_hours * 3600, norm_call(self.cfg.call),
                                      via="js8", urgent=urgent, ext_id=ext_id)

    @staticmethod
    def _title_text(body: str):
        parsed = _split_subject(body)
        return parsed if parsed else ("", body)

    def _store_group(self, frm: str, body: str, now: float, ext_id: str) -> str:
        title, text = self._title_text(body)
        mid = self._add(frm, title, text, now, ext_id)
        if mid is None:
            return "duplicate"
        log.info("JS8 -> tablica #%d", mid)
        return "bulletin:%d" % mid

    def _store_urgent(self, frm: str, digits: Optional[str], body: str, now: float, ext_id: str) -> str:
        group = int(digits) if digits else self.urgent_group
        if group not in self.allowed_groups:
            log.info("JS8 %s: grupa %s spoza urgent_groups - ignoruje", frm, digits)
            return "ignored: grupa %s" % digits
        title, text = self._title_text(body)
        mid = self._add(frm, ("EMC: " + title).strip(), text, now, ext_id, urgent=True)
        if mid is None:
            return "duplicate"
        log.info("JS8 EMC -> tablica #%d (PILNE)", mid)
        if self.urgent_send and self.forward_cb:
            msg = "EMC: %s@JS8 %s" % (frm, ("%s: %s" % (title, text)) if title else text)
            if not self.forward_cb(group, msg, now):
                log.warning("EMC #%d: limit wysylek na grupe %d - wpis zostaje tylko na tablicy", mid, group)
        if self.urgent_dm and self.urgent_cb:
            self.urgent_cb(mid, frm, "EMC", now)
        return "urgent:%d" % mid

    def _store_private(self, frm: str, body: str, now: float, ext_id: str) -> str:
        target, _, rest = body.partition(" ")
        target = target.upper()
        rest = rest.strip()
        if not valid_callsign(target) or not rest:
            return "ignored: nie 'ZNAK tresc'"
        user = self.store.get_user(target)
        if user is None:
            log.info("JS8 %s -> %s: brak takiego znaku w BBS (odpowiedz przez JS8 w etapie 2)", frm, target)
            return "unknown-user"
        if self.store.count_private(target, now) >= self.cfg.max_private:
            log.info("JS8 %s -> %s: skrzynka pelna", frm, target)
            return "mailbox-full"
        parsed = _split_subject(rest)
        subject, text = parsed if parsed else ("", rest)
        mid = self.store.add_message("private", frm, target, subject, text, now,
                                     self.cfg.retention_private_hours * 3600, norm_call(self.cfg.call),
                                     via="js8", ext_id=ext_id)
        if mid is None:
            return "duplicate"
        self.store.request_notice(target, now, self.cfg.notify_min_gap_s)
        log.info("JS8 %s -> poczta prywatna dla %s (#%d)", frm, base_call(target), mid)
        return "private:%d" % mid

    # ---- strumien: rozcinanie obiektow JSON niezaleznie od granic pakietow TCP -----
    @staticmethod
    def drain(buf: str):
        """Wyciaga z bufora kolejne kompletne obiekty JSON. Zwraca (lista_obiektow, reszta)."""
        dec, out, i = json.JSONDecoder(), [], 0
        while True:
            while i < len(buf) and buf[i] in " \r\n\t":
                i += 1
            if i >= len(buf):
                return out, ""
            try:
                obj, end = dec.raw_decode(buf, i)
            except ValueError:
                if buf[i] != "{" or len(buf) - i > 1_000_000:   # smiec zamiast JSON: porzuc do nastepnej linii
                    nl = buf.find("\n", i)
                    if nl < 0:
                        return out, ""
                    i = nl + 1
                    continue
                return out, buf[i:]                              # niekompletny obiekt: czekaj na reszte
            out.append(obj)
            i = end

    # ---- polaczenie ------------------------------------------------------------------
    def start(self) -> None:
        if not self.host:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="js8call", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        backoff = 1.0
        while not self._stop.is_set():
            try:
                with socket.create_connection((self.host, self.port), timeout=10) as sock:
                    sock.settimeout(1.0)
                    self.connected, backoff = True, 1.0
                    log.info("polaczono z JS8Call %s:%d", self.host, self.port)
                    self._read(sock)
            except OSError as e:
                log.warning("JS8Call %s:%d: %s (ponowie za %.0f s)", self.host, self.port, e, backoff)
            finally:
                self.connected = False
            self._stop.wait(backoff)
            backoff = min(backoff * 2, 60.0)

    def _read(self, sock) -> None:
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")   # znak 3-bajtowy moze byc rozciety
        buf = ""
        while not self._stop.is_set():
            try:
                data = sock.recv(65536)
            except socket.timeout:
                continue
            if not data:
                log.warning("JS8Call zamknal polaczenie")
                return
            buf += decoder.decode(data)
            objs, buf = self.drain(buf)
            for obj in objs:
                try:
                    res = self.handle_message(obj)
                    if res and not res.startswith("ignored"):
                        log.debug("JS8: %s", res)
                except Exception:   # noqa: jedna zla wiadomosc nie moze zabic bramki
                    log.exception("blad przetwarzania wiadomosci JS8Call")
