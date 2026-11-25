"""Parser i wykonanie komend BBS. Czysta logika: bez radia, bez gniazd (latwa do testowania).

Zestaw komend zgodny z BBS DB0SEP-12 (add br bs h mh l u r e p t s). Prefiks przed komenda
(w DB0SEP 'db0sep ') jest TUTAJ OPCJONALNY - kazdy bajt liczy sie przy limicie ok. 149 znakow.
Odpowiedzi sa ASCII (bez polskich znakow) dla zgodnosci z malymi wyswietlaczami.
"""
from __future__ import annotations

import re
import time
from typing import List, Optional

from .config import Config
from .store import Store
from .util import base_call, norm_call, valid_callsign

HELP = "Komendy (opc. prefiks !): l [pN] r N d N|* s ZNAK Temat:Tresc bl [pN] br N bs Temat:Tresc u mh p t wx urgent on|off. Tresc konczy END"
ALIASES = {"help": "h", "?": "h", "list": "l", "inbox": "l", "read": "r", "delete": "d", "del": "d", "e": "d",
           "erase": "d", "send": "s", "ping": "p", "uptime": "t", "users": "u", "heard": "mh",
           "register": "add", "board": "bl", "bulletins": "bl", "post": "bs", "emcom": "urgent",
           "weather": "wx", "pogoda": "wx", "tele": "wx"}
NO_AUTO_REGISTER = {"p", "t", "h", "add", "wx"}   # diagnostyka/pomoc nie zapisuje nikogo; add robi to sam


def _age(seconds: float) -> str:
    s = int(max(seconds, 0))
    if s < 90:
        return "%ds" % s
    if s < 5400:
        return "%dm" % (s // 60)
    if s < 172800:
        return "%dh" % (s // 3600)
    return "%dd" % (s // 86400)


def _when(ts: int) -> str:
    return time.strftime("%d.%m %H:%M", time.localtime(ts))


END_RE = re.compile(r"(?:^|\s)end\s*$", re.I)


def _split_subject(rest: str):
    if ":" not in rest:
        return None
    subject, body = rest.split(":", 1)
    return subject.strip()[:30], body.strip()


def _who(r) -> str:
    """Nadawca do wyswietlenia; wiadomosci z KF (JS8Call) maja dopisek @JS8 (nadawca nieuwierzytelniony)."""
    return r["from_call"] + ("@" + r["via"].upper() if r["via"] else "")


def _span(hours: float) -> str:
    return "%d dni" % round(hours / 24) if hours >= 48 else "%dh" % round(hours)


def _strip_end(body: str):
    """Zdejmuje koncowe END (znacznik konca tresci). Zwraca (tresc, byl_END)."""
    if END_RE.search(body):
        return END_RE.sub("", body).strip(), True
    return body, False


class Commands:
    def __init__(self, store: Store, cfg: Config, started_at: float) -> None:
        self.store, self.cfg, self.started = store, cfg, started_at
        pref = {p.lower() for p in cfg.prefixes}
        pref |= {norm_call(cfg.call).lower(), base_call(cfg.call).lower()}
        self.prefixes = pref
        self.drafts = {}   # nadawca -> {"kind","target","subject","parts","last"}

    def run(self, sender: str, text: str, now: float) -> Optional[List[str]]:
        """Zwraca linie odpowiedzi albo None, gdy BBS ma milczec (to nie jest komenda do nas)."""
        note = []
        draft = self.drafts.get(sender)
        if draft is not None:
            if now - draft["last"] > self.cfg.draft_autosave_s:     # zapomniany END: zapisz, co jest
                del self.drafts[sender]
                note = self._finish_draft(sender, draft, now, auto=True)
            else:
                return self._draft_input(sender, draft, text.strip(), now)
        reply = self._dispatch(sender, text, now)
        return note + (reply or []) if (note or reply is not None) else None

    def expire_drafts(self, now: float):
        """Wywolywane z ticka: szkice bez END po draft_autosave_s sa zapisywane. Zwraca [(nadawca, linie)]."""
        out = []
        for sender, draft in list(self.drafts.items()):
            if now - draft["last"] > self.cfg.draft_autosave_s:
                del self.drafts[sender]
                out.append((sender, self._finish_draft(sender, draft, now, auto=True)))
        return out

    def _dispatch(self, sender: str, text: str, now: float) -> Optional[List[str]]:
        rest = text.strip()
        bang = rest.startswith("!")
        if bang:
            rest = rest[1:].lstrip()
        head, _, tail = rest.partition(" ")
        had_prefix = bang or head.lower() in self.prefixes
        if not bang and head.lower() in self.prefixes:
            rest = tail.strip()
        if self.cfg.require_prefix and not had_prefix:
            return None
        cmd, _, args = rest.partition(" ")
        cmd = ALIASES.get(cmd.lower(), cmd.lower())
        args = args.strip()
        if not cmd:
            return [HELP] if had_prefix else None
        handler = getattr(self, "cmd_" + cmd, None) if cmd.isalpha() else None
        registered = self.store.get_user(sender) is not None
        if handler is None:
            # wspoldzielony node: zwykla rozmowa z czlowiekiem nie moze dostawac 'nieznana komenda'
            return ["Nieznana komenda. h = pomoc"] if (registered or had_prefix) else None
        welcome: List[str] = []
        if not registered and cmd not in NO_AUTO_REGISTER:
            # pierwszy kontakt: konto zakladane samo (znak bazowy, bez SSID), z krotkim powitaniem
            if not valid_callsign(sender):
                return ["Nieprawidlowy znak wywolawczy."]
            self.store.add_user(base_call(sender), sender, now)
            welcome = self._welcome(sender)
        out = handler(sender, args, now)
        return welcome + out if out is not None else (welcome or None)

    def _welcome(self, sender: str) -> List[str]:
        return ["Witaj %s! Dodano Cie do uzytkownikow BBS. %s" % (base_call(sender), self.cfg.rules_text),
                "Poczta %s, tablica %s. h = pomoc" % (
                    _span(self.cfg.retention_private_hours), _span(self.cfg.retention_bulletin_hours))]

    # --- otwarte ------------------------------------------------------------------
    def cmd_add(self, sender, args, now):
        if not valid_callsign(sender):
            return ["Nieprawidlowy znak wywolawczy."]
        base = base_call(sender)
        if self.store.add_user(base, sender, now):
            return self._welcome(sender)
        return ["%s juz zarejestrowany." % base]

    def cmd_h(self, sender, args, now):
        return [HELP]

    def cmd_p(self, sender, args, now):
        return ["pong"]

    def cmd_t(self, sender, args, now):
        return ["uptime %s" % _age(now - self.started)]

    def cmd_wx(self, sender, args, now):
        """Telemetria wlasnego noda. Liczba calkowita 0 = brak czujnika (pomijana), z przecinkiem = odczyt."""
        got = self.store.get_telemetry()
        if got is None:
            return ["TELEMETRIA: brak danych"]
        ts, d = got

        def real(k):
            v = d.get(k)
            return v if isinstance(v, float) or (isinstance(v, int) and v != 0) else None
        spec = (("temp1", "T %.1fC"), ("temp2", "T2 %.1fC"), ("hum", "H %.0f%%"), ("qfe", "QFE %.1f"),
                ("qnh", "QNH %.1f hPa"), ("gas", "Gaz %.1f kOhm"), ("co2", "eCO2 %.0f ppm"))
        parts = [fmt % real(k) for k, fmt in spec if real(k) is not None]
        if not parts:
            return ["TELEMETRIA: brak danych"]
        age = now - ts
        head = "TELEMETRIA (%s temu)" % (_age(age) if age >= 60 else "<1m")
        if age > 7200:
            head += " NIEAKTUALNE"
        return [head + ": " + " | ".join(parts)]

    # --- wiadomosci prywatne ------------------------------------------------------
    def cmd_l(self, sender, args, now):
        m = re.fullmatch(r"p(\d+)", args, re.I)
        if args and not m:
            return ["Uzycie: l [pN]"]
        rows = self.store.private_for(sender, now)
        if not rows:
            return ["Brak wiadomosci."]
        per, page = self.cfg.max_bulletins_shown, (max(int(m.group(1)), 1) if m else 1)
        start = (page - 1) * per
        shown = rows[start:start + per]
        if not shown:
            return ["Brak dalszych wiadomosci."]
        out = ["%s[%d] %s: %s (%s)" % ("" if r["read_at"] else "*", i, _who(r), r["subject"] or "-",
                                        _when(r["created_at"]))
               for i, r in enumerate(shown, start + 1)]
        more = len(rows) - start - len(shown)
        return out + (["l p%d = dalsze (%d)" % (page + 1, more)] if more > 0 else [])

    def cmd_r(self, sender, args, now):
        if not args.isdigit():
            return ["Uzycie: r N"]
        rows = self.store.private_for(sender, now)
        n = int(args)
        if not 1 <= n <= len(rows):
            return ["Brak wiadomosci nr %s." % args]
        r = rows[n - 1]
        self.store.mark_read(r["id"], now)
        head = "[%d] od %s" % (n, _who(r))
        return ["%s %s: %s" % (head, r["subject"], r["body"]) if r["subject"] else "%s: %s" % (head, r["body"])]

    def cmd_d(self, sender, args, now):
        if args == "*":
            return ["Usunieto: %d" % self.store.delete_private(sender, now)]
        if args.isdigit():
            n = self.store.delete_private(sender, now, int(args))
            return ["Usunieto." if n else "Brak wiadomosci nr %s." % args]
        return ["Uzycie: d N albo d *"]

    def cmd_s(self, sender, args, now):
        target, _, rest = args.partition(" ")
        parsed = _split_subject(rest)
        if not target or parsed is None:
            return ["Uzycie: s ZNAK Temat:Tresc (dluga: s ZNAK Temat: i tekst, END)"]
        subject, body = parsed
        problem = self._check_target(target, now)
        if problem:
            return [problem]
        body, had_end = _strip_end(body)
        if not body:
            if had_end:
                return ["Pusta tresc."]
            return self._start_draft(sender, "private", target, subject, now)
        if not had_end:
            return self._start_draft(sender, "private", target, subject, now, first=body)
        return self._store_private(sender, target, subject, body, now)

    def _check_target(self, target, now):
        if not valid_callsign(target) or self.store.get_user(target) is None:
            return "%s nie jest zarejestrowany." % target.upper()
        if self.store.count_private(target, now) >= self.cfg.max_private:
            return "Skrzynka %s pelna." % base_call(target)
        return None

    def _store_private(self, sender, target, subject, body, now):
        if len(body) > self.cfg.max_body_chars:
            return ["Za dluga tresc (max %d znakow)." % self.cfg.max_body_chars]
        problem = self._check_target(target, now)
        if problem:
            return [problem]
        ttl = self.cfg.retention_private_hours * 3600
        self.store.add_message("private", sender, target, subject, body, now, ttl, norm_call(self.cfg.call))
        self.store.request_notice(target, now, self.cfg.notify_min_gap_s)
        return ["Wyslano do %s." % base_call(target)]

    # --- tresc wieloczesciowa (jak END w BBS Meshtastic) -------------------------------
    def _start_draft(self, sender, kind, target, subject, now, first=None):
        self.drafts[sender] = {"kind": kind, "target": target, "subject": subject,
                               "parts": [first] if first else [], "last": now}
        if first:
            return []                       # cisza: kazda odpowiedz to dodatkowy airtime
        return ["Pisz tresc w kolejnych wiadomosciach, na koniec END (anuluj: !cancel)"]

    def _draft_input(self, sender, draft, text, now):
        low = text.lower()
        if low in ("cancel", "anuluj", "!cancel", "!anuluj"):
            del self.drafts[sender]
            return ["Anulowano."]
        done = bool(END_RE.search(text)) or low in ("!end", "! end")
        if done:
            text = "" if low.startswith("!") else END_RE.sub("", text).strip()
        if text:
            draft["parts"].append(text)
        draft["last"] = now
        if not done:
            return []
        del self.drafts[sender]
        return self._finish_draft(sender, draft, now)

    def _finish_draft(self, sender, draft, now, auto=False):
        body = " ".join(draft["parts"]).strip()
        if not body:
            return ["Pusta tresc, anulowano."]
        if draft["kind"] == "private":
            out = self._store_private(sender, draft["target"], draft["subject"], body, now)
        else:
            out = self._store_bulletin(sender, draft["subject"], body, now)
        if auto and out:
            out = [out[0] + " (autozapis: brak END)"] + out[1:]
        return out

    # --- tablica ogolna -----------------------------------------------------------
    def cmd_bl(self, sender, args, now):
        m = re.fullmatch(r"p(\d+)", args, re.I)
        if args and not m:
            return ["Uzycie: bl [pN]"]
        page = max(int(m.group(1)), 1) if m else 1
        per = self.cfg.max_bulletins_shown
        rows = self.store.bulletins(per, now, (page - 1) * per)
        if not rows:
            return ["Tablica pusta." if page == 1 else "Brak starszych wpisow."]
        older = self.store.count_bulletins(now) - page * per
        foot = "br N = czytaj" + (", bl p%d = starsze (%d)" % (page + 1, older) if older > 0 else "")
        return ["%s[%d] %s: %s (%s)" % ("!" if r["urgent"] else "", r["id"], _who(r), r["subject"] or "-",
                                         _age(now - r["created_at"])) for r in rows] + [foot]

    def cmd_br(self, sender, args, now):
        if not args or re.fullmatch(r"p\d+", args, re.I):     # zgodnosc z DB0SEP: samo `br` = lista
            return self.cmd_bl(sender, args, now)
        n = args.lstrip("#")
        if not n.isdigit():
            return ["Uzycie: br N (czytaj), bl [pN] (lista)"]
        r = self.store.bulletin_by_id(int(n), now)
        if r is None:
            return ["Brak wpisu #%s." % n]
        return ["%s[%d] %s %s%s" % ("!" if r["urgent"] else "", r["id"], _who(r),
                                    r["subject"] + ": " if r["subject"] else "", r["body"])]

    def cmd_bs(self, sender, args, now):
        parsed = _split_subject(args)
        if parsed is None:
            return ["Uzycie: bs Temat:Tresc (dluga: bs Temat: i tekst, END)"]
        subject, body = parsed
        body, had_end = _strip_end(body)
        if not body and not had_end:
            return self._start_draft(sender, "bulletin", None, subject, now)
        if not body:
            return ["Pusta tresc."]
        if not had_end:
            return self._start_draft(sender, "bulletin", None, subject, now, first=body)
        return self._store_bulletin(sender, subject, body, now)

    def _store_bulletin(self, sender, subject, body, now):
        if len(body) > self.cfg.max_body_chars:
            return ["Za dluga tresc (max %d znakow)." % self.cfg.max_body_chars]
        ttl = self.cfg.retention_bulletin_hours * 3600
        mid = self.store.add_message("bulletin", sender, None, subject, body, now, ttl, norm_call(self.cfg.call))
        return ["Dodano #%d." % mid]

    # --- informacje ---------------------------------------------------------------
    def cmd_urgent(self, sender, args, now):
        """Powiadomienia o pilnych wpisach z KF (JS8Call, grupy EmCom) - domyslnie wylaczone."""
        arg = args.strip().lower()
        if arg in ("on", "off"):
            self.store.set_urgent_sub(sender, arg == "on")
            return ["Powiadomienia PILNE: %s." % ("wlaczone" if arg == "on" else "wylaczone")]
        user = self.store.get_user(sender)
        state = "wlaczone" if user is not None and user["urgent_sub"] else "wylaczone"
        return ["Powiadomienia PILNE (EmCom z JS8Call): %s. urgent on|off" % state]

    def cmd_u(self, sender, args, now):
        users = self.store.list_users()
        return [" ".join(users)] if users else ["Brak uzytkownikow."]

    def cmd_mh(self, sender, args, now):
        rows = self.store.recent_heard(8)
        return [" ".join("%s %s" % (r["call"], _age(now - r["last_seen"])) for r in rows)] if rows \
            else ["Nikogo nie slyszano."]
