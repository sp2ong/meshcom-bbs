"""Usluga BBS: odbior, deduplikacja, ACK, odpowiedzi przez kolejke z pacingiem i ponawianiem."""
from __future__ import annotations

import collections
import logging
import time

from .commands import Commands
from .config import Config
from .models import Incoming
from .store import Store
from .transports.base import Transport
from .util import base_call, chunk_lines, norm_call, valid_callsign

log = logging.getLogger(__name__)


class BBS:
    def __init__(self, cfg: Config, store: Store, transport: Transport, clock=time.time) -> None:
        self.cfg, self.store, self.tr, self.clock = cfg, store, transport, clock
        self.started = clock()
        self.cmds = Commands(store, cfg, self.started)
        self._seen = {}                           # (nadawca, {nn) -> kiedy; deduplikacja powtorek
        self._replies = collections.defaultdict(collections.deque)
        self._urgent_times = collections.deque()
        self._fwd_times = collections.deque()
        self._last_send = 0.0
        self._ack_unsupported_logged = False
        self._ignore = {norm_call(cfg.call), norm_call(cfg.own_node_call)}   # echa wlasnych wysylek
        transport.on_message = lambda inc: self.handle_incoming(inc)
        transport.on_ack = self._on_ack
        transport.on_tx_result = self._on_tx_result
        transport.on_heard = lambda call: self._on_heard(call)
        transport.on_telemetry = lambda fields: self.store.set_telemetry(fields, self.clock())

    # --- zdarzenia z transportu ---------------------------------------------------
    def _on_heard(self, call: str) -> None:
        if norm_call(call) not in self._ignore:
            self.store.touch_heard(call, self.clock())

    def _on_ack(self, key) -> None:
        if self.store.mark_acked(key):
            log.info("ACK dla klucza %s", key)

    def _on_tx_result(self, key, ok: bool, info: str) -> None:
        if not ok:
            log.warning("node odrzucil ramke (klucz %s): %s", key, info)
            self.store.reject_key(key, self.clock(), self.cfg.retry_delays_s)

    def handle_incoming(self, inc: Incoming, now: float = None) -> None:
        now = self.clock() if now is None else now
        src, dst = norm_call(inc.src), norm_call(inc.dst)
        if not src or src in self._ignore:        # echa wlasnych wysylek i sam node
            return
        self.store.touch_heard(src, now)
        if dst != norm_call(self.cfg.call):       # broadcast, grupy, DM do kogos innego
            return
        key = (src, inc.msgno) if inc.msgno else None
        dup = key is not None and now - self._seen.get(key, -1e18) < self.cfg.dedupe_window_s
        if inc.msgno and self.cfg.send_acks:      # ACK takze na powtorke: poprzedni mogl sie zgubic
            self._send_ack(src, inc.msgno)
        if dup:
            log.info("powtorka %s {%s - pomijam", src, inc.msgno)
            return
        if key is not None:
            self._seen[key] = now
        if not self._allow_reply(src, now):
            log.warning("limit odpowiedzi dla %s", src)
            return
        if valid_callsign(src) and self.store.get_user(src) is not None:
            self.store.touch_user(src, src, now)
        lines = self.cmds.run(src, inc.text, now)
        if lines is None:
            return
        for frame in chunk_lines(lines, self.cfg.frame_bytes, self.cfg.line_sep):
            self.store.enqueue(src, frame, now, self.cfg.reply_attempts)

    def _send_ack(self, src: str, msgno: str) -> None:
        try:
            self.tr.send_ack(src, msgno)
        except NotImplementedError:
            if not self._ack_unsupported_logged:
                log.warning("ten transport nie umie wysylac ACK (ext-UDP); BBS na innym SSID niz node "
                            "wymaga transportu KISS")
                self._ack_unsupported_logged = True
        except Exception as e:   # noqa
            log.warning("nie wyslano ACK do %s: %s", src, e)

    def _allow_reply(self, src: str, now: float) -> bool:
        q = self._replies[src]
        while q and now - q[0] > 3600:
            q.popleft()
        if len(q) >= self.cfg.max_replies_per_hour:
            return False
        q.append(now)
        return True

    # --- cykliczne ----------------------------------------------------------------
    def tick(self, now: float = None) -> None:
        now = self.clock() if now is None else now
        self.store.purge(now)
        self._seen = {k: t for k, t in self._seen.items() if now - t < self.cfg.dedupe_window_s}
        self.store.expire_waiting(now, self.cfg.retry_delays_s)
        self._queue_notices(now)
        for sender, lines in self.cmds.expire_drafts(now):
            for frame in chunk_lines(lines, self.cfg.frame_bytes, self.cfg.line_sep):
                self.store.enqueue(sender, frame, now, self.cfg.reply_attempts)
        if now - self._last_send < self.cfg.pace_s or not self.tr.connected:
            return
        row = self.store.next_due(now)
        if row is None:
            return
        try:
            key = self.tr.send(row["to_addr"], row["text"])
        except Exception as e:   # noqa: brak polaczenia itp. - nie zuzywamy proby
            log.warning("wysylka nieudana: %s", e)
            self.store.postpone(row["id"], now + 10)
            return
        self._last_send = now
        self.store.mark_sent(row["id"], key, now, now + self.cfg.ack_timeout_s)

    def _queue_notices(self, now: float) -> None:
        for r in self.store.users_to_notice(now, self.cfg.notify_interval_h * 3600):
            text = "BBS %s: nowe wiadomosci: %d. Wyslij: l" % (norm_call(self.cfg.call), r["n"])
            for addr in self._notice_addrs(r["call"], r["addr"], now):
                self.store.enqueue(addr, text, now, 2)
            self.store.set_notified(r["call"], now)

    def _notice_addrs(self, call: str, last_addr, now: float):
        addrs = []
        if self.cfg.notify_all_ssids:           # polka + teren: wszystkie SSID widziane ostatnio
            addrs = self.store.recent_addrs(call, now - self.cfg.notify_addr_days * 86400,
                                            self.cfg.notify_max_addrs)
        return addrs or [last_addr or call]

    def urgent_notice(self, msg_id: int, sender: str, group: str, now: float = None) -> None:
        """Wywolywane przez bramke JS8Call po zapisaniu pilnego wpisu: DM do zapisanych (`urgent on`)."""
        now = self.clock() if now is None else now
        q, cap = self._urgent_times, int(self.cfg.js8.get("urgent_max_per_hour", 6))
        while q and now - q[0] > 3600:
            q.popleft()
        if len(q) >= cap:                    # ochrona LoRa przed zalewem z KF: wpis zostaje, powiadomienia nie
            log.warning("limit powiadomien PILNE (%d/h): wpis #%d bez DM", cap, msg_id)
            return
        q.append(now)
        text = "PILNE %s od %s. Czytaj: br %d" % (group, sender, msg_id)
        for u in self.store.urgent_subscribers():
            for addr in self._notice_addrs(u["call"], u["last_addr"], now):
                self.store.enqueue(addr, text, now, self.cfg.reply_attempts)

    def send_group(self, group: int, text: str, now: float = None) -> bool:
        """Wysyla tekst na grupe MeshCom (adresat numeryczny, bez ACK: jedna proba, opcjonalnie powtorzenia).
        Zwraca False, gdy przekroczono limit `urgent_max_per_hour` (nic nie wysylamy)."""
        now = self.clock() if now is None else now
        q, cap = self._fwd_times, int(self.cfg.js8.get("urgent_max_per_hour", 6))
        while q and now - q[0] > 3600:
            q.popleft()
        if len(q) >= cap:
            return False
        q.append(now)
        dst = str(group)
        limit = min(self.cfg.frame_bytes, 160 - 2 - len(dst))      # firmware: {ADRESAT}tekst <= 160 B
        frames = chunk_lines([text], limit, " ")
        repeats = max(1, int(self.cfg.js8.get("urgent_repeat", 1)))
        for i in range(repeats):
            for frame in frames:
                self.store.enqueue(dst, frame, now, 1, delay_s=i * 120, ack=False)
        return True

    def run_forever(self) -> None:
        self.tr.start()
        log.info("BBS %s dziala", self.cfg.call)
        try:
            while True:
                self.tick()
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        finally:
            self.tr.stop()
