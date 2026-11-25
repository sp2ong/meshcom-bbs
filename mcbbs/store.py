"""Warstwa SQLite: uzytkownicy, wiadomosci, slyszane stacje, kolejka nadawcza."""
from __future__ import annotations

import json
import sqlite3
import threading
from typing import List, Optional

from .util import base_call, norm_call

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
  call TEXT PRIMARY KEY, registered_at INTEGER NOT NULL,
  last_addr TEXT, last_seen INTEGER, last_notified INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS user_addrs(
  call TEXT NOT NULL, addr TEXT NOT NULL, last_seen INTEGER NOT NULL, PRIMARY KEY(call, addr));
CREATE TABLE IF NOT EXISTS messages(
  id INTEGER PRIMARY KEY AUTOINCREMENT, origin TEXT NOT NULL,
  kind TEXT NOT NULL CHECK(kind IN ('bulletin','private')),
  from_call TEXT NOT NULL, to_call TEXT, subject TEXT NOT NULL DEFAULT '',
  body TEXT NOT NULL, created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL, read_at INTEGER);
CREATE INDEX IF NOT EXISTS idx_msg_to ON messages(kind, to_call);
CREATE TABLE IF NOT EXISTS heard(
  call TEXT PRIMARY KEY, last_seen INTEGER NOT NULL, count INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS telemetry(
  id INTEGER PRIMARY KEY CHECK(id=1), ts INTEGER NOT NULL, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS outbox(
  id INTEGER PRIMARY KEY AUTOINCREMENT, to_addr TEXT NOT NULL, text TEXT NOT NULL,
  created_at INTEGER NOT NULL, next_try REAL NOT NULL DEFAULT 0,
  attempts INTEGER NOT NULL DEFAULT 0, max_attempts INTEGER NOT NULL,
  key TEXT, state TEXT NOT NULL DEFAULT 'pending', sent_at INTEGER);
"""


class Store:
    def __init__(self, path: str = ":memory:", reset_waiting: bool = True) -> None:
        """reset_waiting=False dla drugiego procesu (shell admina) na tej samej bazie, zeby nie ruszac
        wiadomosci 'waiting' dzialajacego BBS-a."""
        self.db = sqlite3.connect(path, timeout=10, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        with self.lock:
            if path != ":memory:":
                self.db.execute("PRAGMA journal_mode=WAL")   # demon BBS i shell moga dzialac rownolegle
            self.db.executescript(SCHEMA)
            self._migrate()
            if reset_waiting:
                # po restarcie klucze ACK sa niewazne - wiadomosci 'waiting' wracaja do kolejki
                self.db.execute("UPDATE outbox SET state='pending', next_try=0 WHERE state='waiting'")
            self.db.commit()

    def _run(self, sql, args=()):
        with self.lock:
            cur = self.db.execute(sql, args)
            self.db.commit()
            return cur

    def _all(self, sql, args=()):
        with self.lock:
            return self.db.execute(sql, args).fetchall()

    def _migrate(self) -> None:
        """Dopisuje kolumny dodane w nowszych wersjach (stare bazy dzialaja dalej)."""
        def cols(table):
            return {r[1] for r in self.db.execute("PRAGMA table_info(%s)" % table)}
        for table, col, ddl in (("messages", "via", "TEXT"), ("messages", "urgent", "INTEGER NOT NULL DEFAULT 0"),
                                ("messages", "ext_id", "TEXT"), ("users", "urgent_sub", "INTEGER NOT NULL DEFAULT 0"),
                                ("outbox", "want_ack", "INTEGER NOT NULL DEFAULT 1")):
            if col not in cols(table):
                self.db.execute("ALTER TABLE %s ADD COLUMN %s %s" % (table, col, ddl))
        self.db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_msg_ext ON messages(ext_id) WHERE ext_id IS NOT NULL")

    # --- uzytkownicy --------------------------------------------------------------
    def add_user(self, call: str, addr: str, now: float) -> bool:
        cur = self._run("INSERT OR IGNORE INTO users(call, registered_at, last_addr, last_seen) "
                        "VALUES(?,?,?,?)", (base_call(call), int(now), norm_call(addr), int(now)))
        self._note_addr(call, addr, now)
        return cur.rowcount == 1

    def _note_addr(self, call: str, addr: str, now: float) -> None:
        self._run("INSERT INTO user_addrs(call, addr, last_seen) VALUES(?,?,?) "
                  "ON CONFLICT(call, addr) DO UPDATE SET last_seen=excluded.last_seen",
                  (base_call(call), norm_call(addr), int(now)))

    def recent_addrs(self, call: str, since: float, limit: int):
        """Pelne adresy (z SSID), z ktorych uzytkownik pisal od 'since', najswiezsze pierwsze."""
        rows = self._all("SELECT addr FROM user_addrs WHERE call=? AND last_seen>=? "
                         "ORDER BY last_seen DESC, addr LIMIT ?", (base_call(call), int(since), limit))
        return [r["addr"] for r in rows]

    def get_user(self, call: str):
        rows = self._all("SELECT * FROM users WHERE call=?", (base_call(call),))
        return rows[0] if rows else None

    def touch_user(self, call: str, addr: str, now: float) -> None:
        self._run("UPDATE users SET last_addr=?, last_seen=? WHERE call=?",
                  (norm_call(addr), int(now), base_call(call)))
        self._note_addr(call, addr, now)

    def list_users(self) -> List[str]:
        return [r["call"] for r in self._all("SELECT call FROM users ORDER BY call")]

    # --- wiadomosci ---------------------------------------------------------------
    def add_message(self, kind: str, from_call: str, to_call: Optional[str], subject: str,
                    body: str, now: float, ttl_s: float, origin: str, via: Optional[str] = None,
                    urgent: bool = False, ext_id: Optional[str] = None) -> Optional[int]:
        """Zwraca id nowej wiadomosci albo None, gdy ext_id juz istnieje (duplikat z zewnetrznego zrodla)."""
        with self.lock:
            if ext_id is not None and self._all("SELECT 1 FROM messages WHERE ext_id=?", (ext_id,)):
                return None                     # duplikat: sprawdzamy przed INSERT, zeby nie zjadac numerow wpisow
            cur = self._run(
                "INSERT OR IGNORE INTO messages(origin,kind,from_call,to_call,subject,body,created_at,expires_at,"
                "via,urgent,ext_id) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (origin, kind, norm_call(from_call), base_call(to_call) if to_call else None,
                 subject, body, int(now), int(now + ttl_s), via, int(urgent), ext_id))
            return cur.lastrowid if cur.rowcount == 1 else None

    def set_urgent_sub(self, call: str, on: bool) -> None:
        self._run("UPDATE users SET urgent_sub=? WHERE call=?", (int(on), base_call(call)))

    def urgent_subscribers(self):
        return self._all("SELECT call, last_addr FROM users WHERE urgent_sub=1")

    def private_for(self, to_call: str, now: float):
        return self._all("SELECT * FROM messages WHERE kind='private' AND to_call=? AND expires_at>? "
                         "ORDER BY id", (base_call(to_call), int(now)))

    def count_private(self, to_call: str, now: float) -> int:
        return len(self.private_for(to_call, now))

    def bulletins(self, limit: int, now: float, offset: int = 0):
        rows = self._all("SELECT * FROM messages WHERE kind='bulletin' AND expires_at>? "
                         "ORDER BY id DESC LIMIT ? OFFSET ?", (int(now), limit, offset))
        return list(reversed(rows))

    def count_bulletins(self, now: float) -> int:
        return self._all("SELECT COUNT(*) AS n FROM messages WHERE kind='bulletin' AND expires_at>?",
                         (int(now),))[0]["n"]

    def bulletin_by_id(self, msg_id: int, now: float):
        rows = self._all("SELECT * FROM messages WHERE kind='bulletin' AND id=? AND expires_at>?",
                         (msg_id, int(now)))
        return rows[0] if rows else None

    def mark_read(self, msg_id: int, now: float) -> None:
        self._run("UPDATE messages SET read_at=? WHERE id=? AND read_at IS NULL", (int(now), msg_id))

    def delete_private(self, to_call: str, now: float, index: Optional[int] = None) -> int:
        rows = self.private_for(to_call, now)
        if index is not None:
            rows = rows[index - 1:index] if 1 <= index <= len(rows) else []
        for r in rows:
            self._run("DELETE FROM messages WHERE id=?", (r["id"],))
        return len(rows)

    # --- telemetria wlasnego noda (komenda wx) ---------------------------------------
    def set_telemetry(self, fields: dict, now: float) -> None:
        """Skleja nowe pola z poprzednimi (ramki 'tele' i 'pos' niosa rozne pola)."""
        with self.lock:
            row = self._all("SELECT data FROM telemetry WHERE id=1")
            data = json.loads(row[0]["data"]) if row else {}
            data.update(fields)
            self._run("INSERT INTO telemetry(id, ts, data) VALUES(1,?,?) "
                      "ON CONFLICT(id) DO UPDATE SET ts=excluded.ts, data=excluded.data",
                      (int(now), json.dumps(data)))

    def get_telemetry(self):
        row = self._all("SELECT ts, data FROM telemetry WHERE id=1")
        return (row[0]["ts"], json.loads(row[0]["data"])) if row else None

    # --- administracja (shell) ----------------------------------------------------
    def stats(self, now: float) -> dict:
        one = lambda sql, a=(): self._all(sql, a)[0][0]
        return {"users": one("SELECT COUNT(*) FROM users"),
                "bulletins": one("SELECT COUNT(*) FROM messages WHERE kind='bulletin' AND expires_at>?", (int(now),)),
                "private": one("SELECT COUNT(*) FROM messages WHERE kind='private' AND expires_at>?", (int(now),)),
                "heard": one("SELECT COUNT(*) FROM heard"), "outbox": self.outbox_counts()}

    def users_detail(self):
        return self._all("SELECT call, registered_at, last_addr, last_seen FROM users ORDER BY call")

    def delete_user(self, call: str) -> int:
        """Usuwa uzytkownika i jego skrzynke prywatna. Zwraca liczbe usunietych wiadomosci."""
        b = base_call(call)
        n = self._run("DELETE FROM messages WHERE kind='private' AND to_call=?", (b,)).rowcount
        gone = self._run("DELETE FROM users WHERE call=?", (b,)).rowcount
        return n if gone else -1

    def delete_bulletin(self, msg_id: int) -> bool:
        return self._run("DELETE FROM messages WHERE kind='bulletin' AND id=?", (msg_id,)).rowcount == 1

    def purge_older(self, cutoff: float, include_private: bool = False) -> int:
        sql = "DELETE FROM messages WHERE created_at<?"
        if not include_private:
            sql += " AND kind='bulletin'"
        return self._run(sql, (int(cutoff),)).rowcount

    def outbox_rows(self, limit: int = 20):
        return self._all("SELECT id, to_addr, text, state, attempts, max_attempts FROM outbox "
                         "WHERE state IN ('pending','waiting') ORDER BY id LIMIT ?", (limit,))

    # --- slyszane stacje ----------------------------------------------------------
    def touch_heard(self, call: str, now: float) -> None:
        self._run("INSERT INTO heard(call,last_seen) VALUES(?,?) ON CONFLICT(call) "
                  "DO UPDATE SET last_seen=excluded.last_seen, count=count+1", (norm_call(call), int(now)))

    def recent_heard(self, limit: int):
        return self._all("SELECT call, last_seen FROM heard ORDER BY last_seen DESC LIMIT ?", (limit,))

    # --- powiadomienia o poczcie --------------------------------------------------
    def request_notice(self, to_call: str, now: float, min_gap_s: float) -> None:
        """Nowa wiadomosc -> powiadom przy najblizszym ticku, ale nie czesciej niz co min_gap_s."""
        self._run("UPDATE users SET last_notified=0 WHERE call=? AND last_notified<=?",
                  (base_call(to_call), int(now - min_gap_s)))

    def users_to_notice(self, now: float, interval_s: float):
        return self._all(
            "SELECT u.call AS call, u.last_addr AS addr, COUNT(m.id) AS n FROM users u "
            "JOIN messages m ON m.kind='private' AND m.to_call=u.call AND m.read_at IS NULL "
            "AND m.expires_at>? WHERE u.last_notified<=? GROUP BY u.call",
            (int(now), int(now - interval_s)))

    def set_notified(self, call: str, now: float) -> None:
        self._run("UPDATE users SET last_notified=? WHERE call=?", (int(now), base_call(call)))

    # --- kolejka nadawcza ---------------------------------------------------------
    def enqueue(self, to_addr: str, text: str, now: float, max_attempts: int, delay_s: float = 0,
                ack: bool = True) -> int:
        """ack=False: ramka bez potwierdzenia (grupy MeshCom) - po wyslaniu stan 'sent', bez ponawiania."""
        cur = self._run("INSERT INTO outbox(to_addr,text,created_at,next_try,max_attempts,want_ack) "
                        "VALUES(?,?,?,?,?,?)",
                        (norm_call(to_addr), text, int(now), now + delay_s, max_attempts, int(ack)))
        return cur.lastrowid

    def next_due(self, now: float):
        rows = self._all("SELECT * FROM outbox WHERE state='pending' AND next_try<=? ORDER BY id LIMIT 1",
                         (now,))
        return rows[0] if rows else None

    def mark_sent(self, row_id: int, key, now: float, deadline: float) -> None:
        self._run("UPDATE outbox SET state=CASE WHEN want_ack=1 THEN 'waiting' ELSE 'sent' END, "
                  "attempts=attempts+1, key=?, sent_at=?, next_try=? WHERE id=?",
                  (str(key), int(now), deadline, row_id))

    def postpone(self, row_id: int, until: float) -> None:
        self._run("UPDATE outbox SET next_try=? WHERE id=?", (until, row_id))

    def mark_acked(self, key) -> bool:
        return self._run("UPDATE outbox SET state='acked' WHERE key=? AND state='waiting'",
                         (str(key),)).rowcount > 0

    def _retry_or_fail(self, row, now: float, delays) -> str:
        if row["attempts"] >= row["max_attempts"]:
            self._run("UPDATE outbox SET state='failed' WHERE id=?", (row["id"],))
            return "failed"
        delay = delays[min(max(row["attempts"] - 1, 0), len(delays) - 1)]
        self._run("UPDATE outbox SET state='pending', next_try=? WHERE id=?", (now + delay, row["id"]))
        return "pending"

    def reject_key(self, key, now: float, delays) -> None:
        for row in self._all("SELECT * FROM outbox WHERE key=? AND state='waiting'", (str(key),)):
            self._retry_or_fail(row, now, delays)

    def expire_waiting(self, now: float, delays) -> None:
        for row in self._all("SELECT * FROM outbox WHERE state='waiting' AND next_try<=?", (now,)):
            self._retry_or_fail(row, now, delays)

    def outbox_counts(self) -> dict:
        return {r["state"]: r["n"] for r in self._all("SELECT state, COUNT(*) AS n FROM outbox GROUP BY state")}

    def purge(self, now: float) -> None:
        self._run("DELETE FROM messages WHERE expires_at<=?", (int(now),))
        self._run("DELETE FROM outbox WHERE state IN ('acked','failed','sent') AND created_at<?", (int(now - 86400),))
