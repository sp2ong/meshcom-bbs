"""Lokalny shell administracyjny: `mcbbs -c config.toml --shell [--as ZNAK]`.

Otwiera ta sama baze SQLite co dzialajacy BBS (WAL pozwala na rownolegla prace) i pozwala:
  * uzywac BBS tak jak przez radio (te same komendy, aliasy, END, prefiks !) jako wybrany znak,
  * wykonywac operacje administracyjne (stats, users, del-user, purge-posts, del-bulletin, queue, dm).
Wysylka (komenda dm, powiadomienia o nowej poczcie) idzie przez kolejke nadawcza w bazie: nadaje demon BBS,
wiec node nie dostaje drugiego klienta KISS. Dostep do pliku bazy = uwierzytelnienie administratora
(po radiu zadnych komend admina nie ma).
"""
from __future__ import annotations

import shlex
import time
from typing import List, Optional

from .commands import Commands
from .config import Config
from .store import Store
from .util import base_call, chunk_lines, norm_call, valid_callsign

ADMIN_HELP = [
    "Komendy BBS jak przez radio (l, r N, s ZNAK Temat:Tresc END, bl, br N, bs, d N, ... takze !read, !list).",
    "Admin:",
    "  stats                         liczniki",
    "  users                         uzytkownicy (rejestracja, ostatnio widziany)",
    "  del-user ZNAK                 usun uzytkownika i jego skrzynke prywatna",
    "  del-bulletin N                usun wpis z tablicy",
    "  purge-posts --days N [--all]  usun wpisy tablicy starsze niz N dni (--all: tez prywatne)",
    "  queue                         kolejka nadawcza (pending/waiting)",
    "  dm ZNAK tekst                 wyslij DM z BBS (nadaje demon)",
    "  as ZNAK                       zmien znak, jako ktory dzialasz",
    "  admin-help / quit",
]


class Quit(Exception):
    pass


def _ago(ts: Optional[int], now: float) -> str:
    if not ts:
        return "-"
    s = int(max(now - ts, 0))
    return "%dd" % (s // 86400) if s >= 172800 else "%dh" % (s // 3600) if s >= 5400 else "%dm" % (s // 60)


class AdminShell:
    def __init__(self, cfg: Config, store: Store, as_call: str = "", clock=time.time) -> None:
        self.cfg, self.store, self.clock = cfg, store, clock
        self.cmds = Commands(store, cfg, clock())
        self.as_call = ""
        self.set_user(as_call or base_call(cfg.call))

    def set_user(self, call: str) -> List[str]:
        call = norm_call(call)
        if not valid_callsign(call):
            return ["Nieprawidlowy znak: %s" % call]
        self.as_call = call
        if self.store.add_user(call, call, self.clock()):
            return ["Zarejestrowano %s w BBS (tryb shell)." % base_call(call)]
        return []

    def prompt(self) -> str:
        return "%s@bbs> " % self.as_call

    def execute(self, line: str) -> List[str]:
        line = line.strip()
        if not line:
            return []
        now = self.clock()
        try:
            words = shlex.split(line)
        except ValueError:
            words = line.split()
        name, args = words[0].lower(), words[1:]
        if name in ("quit", "exit"):
            raise Quit
        handler = getattr(self, "adm_" + name.replace("-", "_"), None)
        if handler is not None and not line.startswith("!"):
            return handler(args, now)
        out = self.cmds.run(self.as_call, line, now)
        return out if out is not None else ["(BBS milczy: to nie jest komenda)"]

    # --- komendy administracyjne ---------------------------------------------------
    def adm_admin_help(self, args, now):
        return ADMIN_HELP

    def adm_help(self, args, now):
        return ADMIN_HELP

    def adm_stats(self, args, now):
        st = self.store.stats(now)
        out = st["outbox"]
        return ["Users    : %d" % st["users"], "Bulletins: %d (waznych)" % st["bulletins"],
                "Private  : %d (waznych)" % st["private"], "Heard    : %d" % st["heard"],
                "Outbox   : %s" % (", ".join("%s=%d" % kv for kv in sorted(out.items())) or "pusty")]

    def adm_users(self, args, now):
        rows = self.store.users_detail()
        if not rows:
            return ["Brak uzytkownikow."]
        return ["%-9s zarejestr. %s temu, widziany %s temu (%s)" % (
            r["call"], _ago(r["registered_at"], now), _ago(r["last_seen"], now), r["last_addr"] or "-")
            for r in rows]

    def adm_del_user(self, args, now):
        if len(args) != 1:
            return ["Uzycie: del-user ZNAK"]
        n = self.store.delete_user(args[0])
        return ["Nie ma takiego uzytkownika."] if n < 0 else ["Usunieto %s i %d wiad. prywatnych." % (
            base_call(args[0]), n)]

    def adm_del_bulletin(self, args, now):
        if len(args) != 1 or not args[0].lstrip("#").isdigit():
            return ["Uzycie: del-bulletin N"]
        ok = self.store.delete_bulletin(int(args[0].lstrip("#")))
        return ["Usunieto." if ok else "Brak wpisu #%s." % args[0].lstrip("#")]

    def adm_purge_posts(self, args, now):
        flags = [a for a in args if a.startswith("--")]
        days = None
        if "--days" in args:
            i = args.index("--days")
            if i + 1 < len(args) and args[i + 1].isdigit():
                days = int(args[i + 1])
        if days is None or any(f not in ("--days", "--all") for f in flags):
            return ["Uzycie: purge-posts --days N [--all]"]
        n = self.store.purge_older(now - days * 86400, include_private="--all" in flags)
        return ["Usunieto %d wpis(ow) starszych niz %d dni." % (n, days)]

    def adm_queue(self, args, now):
        rows = self.store.outbox_rows(20)
        if not rows:
            return ["Kolejka pusta."]
        return ["#%d -> %s [%s %d/%d] %s" % (r["id"], r["to_addr"], r["state"], r["attempts"], r["max_attempts"],
                                              r["text"][:60]) for r in rows]

    def adm_dm(self, args, now):
        if len(args) < 2 or not valid_callsign(args[0]):
            return ["Uzycie: dm ZNAK tekst"]
        text = " ".join(args[1:])
        frames = chunk_lines([text], self.cfg.frame_bytes, self.cfg.line_sep)
        for frame in frames:
            self.store.enqueue(norm_call(args[0]), frame, now, self.cfg.reply_attempts)
        return ["W kolejce: %d ramek(a) do %s (nada demon BBS)." % (len(frames), norm_call(args[0]))]

    def adm_as(self, args, now):
        if len(args) != 1:
            return ["Uzycie: as ZNAK  (teraz: %s)" % self.as_call]
        return self.set_user(args[0]) or ["Dzialasz jako %s." % self.as_call]


def run_shell(cfg: Config, store: Store, as_call: str = "") -> None:
    sh = AdminShell(cfg, store, as_call)
    print("MeshCom BBS Admin (%s)  -  'admin-help' = komendy admina, 'quit' = wyjscie" % cfg.call)
    for line in sh.set_user(sh.as_call):
        print(line)
    while True:
        try:
            line = input(sh.prompt())
        except (EOFError, KeyboardInterrupt):
            print()
            return
        try:
            for out in sh.execute(line):
                print(out)
        except Quit:
            if sh.cmds.drafts.get(sh.as_call):
                print("Niedokonczony szkic zostal zapisany tylko jesli dopisales END - wpisz END albo !cancel.")
                continue
            return
