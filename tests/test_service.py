import unittest

from mcbbs.commands import Commands
from mcbbs.config import Config
from mcbbs.models import Incoming
from mcbbs.service import BBS
from mcbbs.store import Store
from tests.fake import FakeTransport

T0 = 1_000_000.0


class ServiceBase(unittest.TestCase):
    def make(self, **kw):
        cfg = Config(call="SP2ONG-1", node_call="SP2ONG-1", pace_s=0, ack_timeout_s=60,
                     retry_delays_s=(10, 20), prefixes=("bbs",), **kw)
        self.store, self.tr = Store(), FakeTransport()
        self.bbs = BBS(cfg, self.store, self.tr, clock=lambda: T0)
        return cfg

    def dm(self, text, src="SQ9GIN-6", msgno="7", now=T0, dst="SP2ONG-1"):
        self.bbs.handle_incoming(Incoming(src=src, dst=dst, text=text, msgno=msgno), now)

    def pump(self, now):
        """Wysyla wszystko, co jest do wyslania w chwili now; zwraca nowe wysylki."""
        before = len(self.tr.sent)
        for _ in range(20):
            self.bbs.tick(now)
        return self.tr.sent[before:]


class CommandFlowTests(ServiceBase):
    def setUp(self):
        self.make()

    def say(self, text, src="SQ9GIN-6", now=T0):
        """Wysyla komende i zwraca odpowiedzi adresowane do nadawcy (bez powiadomien dla innych)."""
        before = len(self.tr.sent)
        self.dm(text, src=src, msgno=None, now=now)
        self.pump(now)
        return [t for d, t, _k in self.tr.sent[before:] if d == src.upper()]

    def test_bare_commands_no_prefix_needed(self):
        self.assertEqual(self.say("p"), ["pong"])

    def test_optional_prefix_still_works(self):
        self.assertEqual(self.say("sp2ong-1 p"), ["pong"])
        self.assertEqual(self.say("bbs p"), ["pong"])

    def test_first_contact_registers_automatically_with_welcome(self):
        self.assertEqual(self.say("p"), ["pong"])                        # diagnostyka nikogo nie zapisuje
        self.assertIsNone(self.store.get_user("SQ9GIN-6"))
        first = self.say("l")
        self.assertIn("Witaj SQ9GIN! Dodano Cie do uzytkownikow BBS.", first[0])
        self.assertIn("lacznosc amatorska", first[0])
        self.assertIn("Brak wiadomosci.", first[-1])
        self.assertIsNotNone(self.store.get_user("SQ9GIN-6"))
        self.assertEqual(self.say("l"), ["Brak wiadomosci."])             # powitanie tylko raz

    def test_add_still_works_and_is_idempotent(self):
        self.assertIn("Witaj SQ9GIN", self.say("add")[0])
        self.assertEqual(self.say("add"), ["SQ9GIN juz zarejestrowany."])

    def test_unknown_text_does_not_register_strangers(self):
        self.assertEqual(self.say("czesc, jak sie masz?"), [])
        self.assertIsNone(self.store.get_user("SQ9GIN-6"))

    def test_non_callsign_cannot_register(self):
        self.assertEqual(self.say("add", src="260"), ["Nieprawidlowy znak wywolawczy."])

    def test_bulletin_and_private_flow(self):
        self.say("add", "SQ9GIN-6")
        self.say("add", "SP2ABC-7")
        self.assertIn("Dodano #1", self.say("bs Test:Hej wszystkim END", "SQ9GIN-6")[0])
        listing = self.say("br", "SP2ABC-7")
        self.assertEqual(len(listing), 1)
        self.assertIn("[1] SQ9GIN-6: Test", listing[0])
        self.assertNotIn("Hej wszystkim", listing[0])                    # lista = tylko tematy
        self.assertEqual(self.say("br 1", "SP2ABC-7"), ["[1] SQ9GIN-6 Test: Hej wszystkim"])
        self.assertEqual(self.say("br 9", "SP2ABC-7"), ["Brak wpisu #9."])
        self.assertEqual(self.say("s sp2abc Czesc:dziala? END", "SQ9GIN-6"), ["Wyslano do SP2ABC."])
        notices = [s for s in self.tr.sent if "nowe wiadomosci" in s[1]]   # powiadomienie o poczcie
        self.assertEqual(len(notices), 1)
        self.assertEqual(notices[0][0], "SP2ABC-7")                     # na ostatnio widziany pelny adres
        self.assertIn("nowe wiadomosci: 1", notices[0][1])
        self.assertIn("[1] SQ9GIN-6: Czesc", self.say("l", "SP2ABC-7")[0])
        self.assertEqual(self.say("r 1", "SP2ABC-7"), ["[1] od SQ9GIN-6 Czesc: dziala?"])
        self.assertNotIn("*", self.say("l", "SP2ABC-7")[0])             # przeczytana: bez gwiazdki
        self.assertEqual(self.say("d 1", "SP2ABC-7"), ["Usunieto."])
        self.assertEqual(self.say("l", "SP2ABC-7"), ["Brak wiadomosci."])

    def test_send_to_unregistered_and_mailbox_limit(self):
        self.say("add", "SQ9GIN-6")
        self.assertIn("nie jest zarejestrowany", self.say("s SP2ABC t:x END", "SQ9GIN-6")[0])
        self.say("add", "SP2ABC-7")
        for i in range(5):
            self.assertEqual(self.say("s SP2ABC t:%d END" % i, "SQ9GIN-6"), ["Wyslano do SP2ABC."])
        self.assertIn("pelna", self.say("s SP2ABC t:6 END", "SQ9GIN-6")[0])

    def test_retention_private_7_days_bulletins_30_days(self):
        self.say("add")
        self.say("add", "SP2ABC-7")
        self.say("bs Stare:wpis END")
        self.say("s SP2ABC Stare:poczta END")
        day = 24 * 3600
        self.assertIn("[1]", " ".join(self.say("l", "SP2ABC-7", now=T0 + 6 * day)))       # po 6 dniach jest
        self.assertIn("Brak wiadomosci.", self.say("l", "SP2ABC-7", now=T0 + 8 * day))   # po 8 juz nie
        self.assertIn("[1]", " ".join(self.say("bl", now=T0 + 29 * day)))                  # tablica po 29 dniach jest
        self.assertIn("Tablica pusta.", self.say("bl", now=T0 + 31 * day))           # po 31 nie

    def test_unknown_command_silent_for_strangers_helpful_for_users(self):
        self.assertEqual(self.say("czesc, jak sie masz?"), [])          # zwykla rozmowa: cisza
        self.assertEqual(self.say("bbs xyz"), ["Nieznana komenda. h = pomoc"])
        self.say("add")
        self.assertEqual(self.say("xyz"), ["Nieznana komenda. h = pomoc"])

    def test_long_reply_is_chunked_within_limit(self):
        self.say("add")
        for i in range(5):
            self.say("bs Temat numer %d dosc dlugi:x END" % i)
        self.say("bs Dluga:" + "dluga tresc " * 20 + "END")
        frames = self.say("br 6")
        self.assertGreater(len(frames), 1)
        for f in frames:
            self.assertLessEqual(len(f.encode()), 147)


class MultipartTests(ServiceBase):
    def setUp(self):
        self.make()

    def say(self, text, src="SQ9GIN-6", now=T0):
        before = len(self.tr.sent)
        self.dm(text, src=src, msgno=None, now=now)
        self.pump(now)
        return [t for d, t, _k in self.tr.sent[before:] if d == src.upper()]

    def test_bulletin_in_several_messages_until_end(self):
        self.say("add")
        self.assertIn("END", self.say("bs Pomoc:")[0])
        self.assertEqual(self.say("Potrzebuje pomocy"), [])              # cisza: oszczedza airtime
        self.assertEqual(self.say("w ustawieniu noda"), [])
        self.assertEqual(self.say("dziekuje END"), ["Dodano #1."])
        self.assertEqual(self.say("br 1"), ["[1] SQ9GIN-6 Pomoc: Potrzebuje pomocy w ustawieniu noda dziekuje"])

    def test_private_multipart_and_cancel(self):
        self.say("add")
        self.say("add", "SP2ABC-7")
        self.say("s SP2ABC Temat:")
        self.say("czesc")
        self.assertEqual(self.say("end"), ["Wyslano do SP2ABC."])
        self.say("bs T:")
        self.assertEqual(self.say("cancel"), ["Anulowano."])
        self.assertEqual(self.say("br"), ["Tablica pusta."])

    def test_draft_without_end_is_autosaved_by_tick(self):
        self.say("add")
        self.say("bs Temat: czesc bez konca")                            # brak END: BBS czeka, milczy
        self.assertEqual(self.store.bulletins(5, T0), [])
        notice = [t for d, t, _k in self.pump(T0 + 200) if d == "SQ9GIN-6"]   # po draft_autosave_s: zapis + powiadomienie
        self.assertEqual(notice, ["Dodano #1. (autozapis: brak END)"])
        self.assertEqual(self.say("br 1", now=T0 + 201), ["[1] SQ9GIN-6 Temat: czesc bez konca"])

    def test_stale_draft_is_saved_before_next_command(self):
        self.say("add")
        self.say("bs Temat: pierwsza")
        self.assertEqual(self.say("p", now=T0 + 700), ["Dodano #1. (autozapis: brak END) | pong"])

    def test_bang_prefix_and_control_words(self):
        self.say("add")
        self.assertEqual(self.say("!ping"), ["pong"])
        self.assertEqual(self.say("! p"), ["pong"])
        self.assertEqual(self.say("!xyz"), ["Nieznana komenda. h = pomoc"])
        self.say("!bs Temat: a")
        self.assertEqual(self.say("b"), [])                              # kontynuacja bez prefiksu
        self.assertEqual(self.say("!end"), ["Dodano #1."])
        self.assertEqual(self.say("br 1"), ["[1] SQ9GIN-6 Temat: a b"])
        self.say("!bs T2: x")
        self.assertEqual(self.say("!cancel"), ["Anulowano."])

    def test_long_names_are_aliases(self):
        self.say("add")
        self.assertEqual(self.say("!list"), ["Brak wiadomosci."])
        self.assertEqual(self.say("!board"), ["Tablica pusta."])
        self.assertEqual(self.say("!delete 1"), ["Brak wiadomosci nr 1."])

    def test_bulletin_paging_beyond_five(self):
        self.say("add")
        for i in range(1, 11):
            self.say("bs Wpis%d:tresc END" % i)
        first = " ".join(self.say("br"))
        self.assertIn("[10] ", first)
        self.assertIn("[6] ", first)
        self.assertNotIn("[5] ", first)
        self.assertIn("bl p2 = starsze (5)", first)
        second = " ".join(self.say("bl p2"))
        self.assertIn("[5] ", second)
        self.assertIn("[1] ", second)
        self.assertNotIn("starsze", second)
        self.assertEqual(self.say("bl p3"), ["Brak starszych wpisow."])
        self.assertEqual(self.say("br p2"), self.say("bl p2"))          # zgodnosc: br pN = bl pN
        self.assertEqual(self.say("br x"), ["Uzycie: br N (czytaj), bl [pN] (lista)"])

    def test_end_marker_stripped_from_single_message(self):
        self.say("add")
        self.say("add", "SP2ABC-7")
        self.assertEqual(self.say("s SP2ABC Test poczty: to jest proba END"), ["Wyslano do SP2ABC."])
        self.assertEqual(self.say("r 1", "SP2ABC-7"), ["[1] od SQ9GIN-6 Test poczty: to jest proba"])
        self.say("bs Temat: tresc end")
        self.assertEqual(self.say("br 2"), ["[2] SQ9GIN-6 Temat: tresc"])
        self.assertEqual(self.say("bs T: END"), ["Pusta tresc."])
        self.assertEqual(self.say("p"), ["pong"])                       # i nie wlaczylo to trybu pisania
        self.assertEqual(self.say("bs Weekend: nie ma ENDa tutaj END"), ["Dodano #3."])   # END tylko jako osobne slowo (tu: ENDa nie jest END)

    def test_delete_is_d_and_e_stays_as_alias(self):
        self.say("add")
        self.say("add", "SP2ABC-7")
        for cmd in ("d 1", "e 1", "delete 1"):
            self.say("s SP2ABC T:x END")
            self.assertEqual(self.say(cmd, "SP2ABC-7"), ["Usunieto."], cmd)
        self.assertEqual(self.say("d", "SP2ABC-7"), ["Uzycie: d N albo d *"])

    def test_notice_goes_to_all_recent_ssids_of_the_recipient(self):
        self.say("add", "SP2ABC-1")                                      # polka
        self.say("p", "SP2ABC-7")
        self.say("l", "SP2ABC-7", now=T0 + 10)                            # teren
        self.say("add", "SQ9GIN-6")
        self.say("s SP2ABC T:x END", "SQ9GIN-6", now=T0 + 20)
        targets = sorted(d for d, t, _k in self.tr.sent if "nowe wiadomosci" in t)
        self.assertEqual(targets, ["SP2ABC-1", "SP2ABC-7"])

    def test_old_ssid_is_not_notified_and_limit_applies(self):
        self.say("add", "SP2ABC-1", now=T0 - 20 * 86400)                  # stary SSID sprzed 20 dni
        self.say("l", "SP2ABC-2", now=T0)
        self.say("l", "SP2ABC-3", now=T0 + 1)
        self.say("l", "SP2ABC-4", now=T0 + 2)
        self.say("l", "SP2ABC-5", now=T0 + 3)
        self.say("add", "SQ9GIN-6")
        self.say("s SP2ABC T:x END", "SQ9GIN-6", now=T0 + 20)
        targets = [d for d, t, _k in self.tr.sent if "nowe wiadomosci" in t]
        self.assertEqual(sorted(targets), ["SP2ABC-3", "SP2ABC-4", "SP2ABC-5"])   # 3 najswiezsze

    def test_single_ssid_mode(self):
        self.make(notify_all_ssids=False)
        self.say("add", "SP2ABC-1")
        self.say("l", "SP2ABC-7", now=T0 + 10)
        self.say("add", "SQ9GIN-6")
        self.say("s SP2ABC T:x END", "SQ9GIN-6", now=T0 + 20)
        self.assertEqual([d for d, t, _k in self.tr.sent if "nowe wiadomosci" in t], ["SP2ABC-7"])

    def test_private_list_paging(self):
        self.make(max_private=12)
        self.dm("add", src="SP2ABC-7", msgno=None)
        self.dm("add", msgno=None)
        for i in range(7):
            self.dm("s SP2ABC T%d:x END" % i, msgno=None)
        self.pump(T0)
        mine = lambda cmd: [t for d, t, _k in self._say(cmd) if d == "SP2ABC-7"]
        first = " ".join(mine("l"))
        self.assertIn("[1] ", first)
        self.assertIn("[5] ", first)
        self.assertNotIn("[6] ", first)
        self.assertIn("l p2 = dalsze (2)", first)
        second = " ".join(mine("l p2"))
        self.assertIn("[6] ", second)
        self.assertIn("[7] ", second)
        self.assertNotIn("dalsze", second)

    def _say(self, cmd, src="SP2ABC-7"):
        before = len(self.tr.sent)
        self.dm(cmd, src=src, msgno=None)
        self.pump(T0)
        return self.tr.sent[before:]

    def test_missing_s_command_gives_hint_not_silence(self):
        self.say("add")
        self.assertEqual(self.say("bbs SP2ABC Temat:tresc"), ["Nieznana komenda. h = pomoc"])


class RequirePrefixTests(ServiceBase):
    def test_shared_node_ignores_chat_without_prefix(self):
        self.make(require_prefix=True)
        self.dm("p", msgno=None)
        self.assertEqual(self.pump(T0), [])
        self.dm("bbs p", msgno=None)
        self.assertEqual([t for _d, t, _k in self.pump(T0)], ["pong"])


class AckPolicyTests(ServiceBase):
    """Firmware sam potwierdza DM do dokladnego znaku noda - BBS pod tym znakiem nie moze ACK-owac drugi raz."""

    def test_same_call_as_node_means_no_own_acks_by_default(self):
        cfg = self.make()
        self.assertFalse(cfg.send_acks)
        self.dm("p", msgno="5")
        self.assertEqual(self.tr.acks, [])
        self.assertEqual(len(self.pump(T0)), 1)                       # odpowiedz mimo to idzie

    def test_other_ssid_acks_by_default(self):
        cfg = Config(call="SP2ONG-12", node_call="SP2ONG-1", pace_s=0)
        self.assertTrue(cfg.send_acks)
        self.assertEqual(cfg.own_node_call, "SP2ONG-1")

    def test_node_call_defaults_to_call(self):
        cfg = Config(call="SP2ONG-1")
        self.assertEqual(cfg.own_node_call, "SP2ONG-1")
        self.assertFalse(cfg.send_acks)

    def test_explicit_override_wins(self):
        self.assertTrue(Config(call="SP2ONG-1", ack_incoming=True).send_acks)
        self.assertFalse(Config(call="SP2ONG-12", node_call="SP2ONG-1", ack_incoming=False).send_acks)

    def test_bbs_on_other_ssid_ignores_echoes_of_node_and_itself(self):
        self.store, self.tr = Store(), FakeTransport()
        cfg = Config(call="SP2ONG-12", node_call="SP2ONG-1", pace_s=0)
        self.bbs = BBS(cfg, self.store, self.tr, clock=lambda: T0)
        self.dm("p", src="SP2ONG-1", dst="SP2ONG-12")
        self.dm("p", src="SP2ONG-12", dst="SP2ONG-12")
        self.assertEqual(self.pump(T0), [])
        self.dm("p", src="SQ9GIN-6", dst="SP2ONG-12", msgno="3")      # prawdziwy uzytkownik
        self.assertEqual(self.tr.acks, [("SQ9GIN-6", "3")])
        self.assertEqual(len(self.pump(T0)), 1)


class ReliabilityTests(ServiceBase):
    def setUp(self):
        self.make(ack_incoming=True)

    def test_incoming_dm_is_acked_and_duplicate_runs_once(self):
        self.dm("add", msgno="819")
        self.dm("add", msgno="819", now=T0 + 30)                        # nadawca ponowil: nie dostal ACK
        self.assertEqual(self.tr.acks, [("SQ9GIN-6", "819")] * 2)       # ACK za kazdym razem
        sent = self.pump(T0 + 30)
        # komenda wykonana raz: jedno powitanie (1 ramka), bez 'juz zarejestrowany' z powtorki
        self.assertEqual(len(sent), 1)
        self.assertIn("Witaj SQ9GIN", sent[0][1])

    def test_same_msgno_after_window_is_new_command(self):
        self.dm("p", msgno="9")
        self.dm("p", msgno="9", now=T0 + 10_000)
        self.assertEqual(len(self.pump(T0 + 10_000)), 2)

    def test_own_echo_and_node_are_ignored(self):
        self.dm("p", src="SP2ONG-1")
        self.assertEqual(self.pump(T0), [])
        self.assertEqual(self.tr.acks, [])

    def test_dm_to_someone_else_and_broadcast_ignored(self):
        self.dm("p", dst="SQ5XXX-1")
        self.dm("p", dst="")
        self.dm("p", dst="260")
        self.assertEqual(self.pump(T0), [])

    def test_ack_marks_delivered_and_stops_retries(self):
        self.dm("p", msgno="1")
        (dst, text, key), = self.pump(T0)
        self.tr.on_ack(key)
        self.assertEqual(self.pump(T0 + 500), [])
        self.assertEqual(self.store.outbox_counts(), {"acked": 1})

    def test_no_ack_retries_then_gives_up(self):
        self.dm("p", msgno="1")
        self.assertEqual(len(self.pump(T0)), 1)
        self.assertEqual(self.pump(T0 + 61), [])                        # timeout -> pending, odczekaj 10 s
        self.assertEqual(len(self.pump(T0 + 71)), 1)                    # proba 2
        self.assertEqual(self.pump(T0 + 71 + 61), [])
        self.assertEqual(len(self.pump(T0 + 71 + 61 + 20)), 1)          # proba 3 (reply_attempts=3)
        self.assertEqual(self.pump(T0 + 1000), [])
        self.assertEqual(self.store.outbox_counts(), {"failed": 1})

    def test_node_rejection_counts_as_failed_attempt(self):
        self.dm("p", msgno="1")
        (dst, text, key), = self.pump(T0)
        self.tr.on_tx_result(key, False, "za dlugi")
        self.assertEqual(self.pump(T0 + 5), [])
        self.assertEqual(len(self.pump(T0 + 10)), 1)

    def test_pacing_between_frames(self):
        cfg = self.make()
        cfg.pace_s = 6
        for i in range(3):
            self.dm("p", msgno=str(i), src="SQ9GIN-%d" % (i + 1))
        self.assertEqual(len(self.pump(T0)), 1)
        self.assertEqual(len(self.pump(T0 + 5)), 0)
        self.assertEqual(len(self.pump(T0 + 6)), 1)

    def test_disconnected_transport_keeps_queue(self):
        self.dm("p", msgno="1")
        self.tr.up = False
        self.assertEqual(self.pump(T0), [])
        self.tr.up = True
        self.assertEqual(len(self.pump(T0 + 1)), 1)

    def test_reply_rate_limit(self):
        cfg = self.make()
        cfg.max_replies_per_hour = 3
        for i in range(10):
            self.dm("p", msgno=str(i))
        self.assertEqual(len(self.pump(T0)), 3)


if __name__ == "__main__":
    unittest.main()


class WxTests(unittest.TestCase):
    def setUp(self):
        self.store = Store()
        self.cmds = Commands(self.store, Config(call="SP2ONG-1"), 1000.0)

    def test_no_data(self):
        self.assertEqual(self.cmds.run("SQ9GIN-6", "wx", 1000.0), ["TELEMETRIA: brak danych"])
        self.assertIsNone(self.store.get_user("SQ9GIN"))                     # wx nie rejestruje

    def test_only_real_sensors_are_shown_and_frames_merge(self):
        self.store.set_telemetry({"temp1": 18.46, "temp2": 0, "hum": 47.94, "gas": 0, "co2": 0}, 1000)
        self.store.set_telemetry({"qfe": 1022.72, "qnh": 1031.116}, 1000)
        out = self.cmds.run("SQ9GIN-6", "wx", 1720.0)
        self.assertEqual(out, ["TELEMETRIA (12m temu): T 18.5C | H 48% | QFE 1022.7 | QNH 1031.1 hPa"])

    def test_all_zero_means_no_data_and_stale_is_flagged(self):
        self.store.set_telemetry({"temp1": 0, "hum": 0, "qfe": 0}, 1000)
        self.assertEqual(self.cmds.run("SQ9GIN-6", "pogoda", 1001.0), ["TELEMETRIA: brak danych"])
        self.store.set_telemetry({"temp1": 0.0}, 1000)                         # float 0.0 to prawdziwe 0 stopni
        self.assertIn("T 0.0C", self.cmds.run("SQ9GIN-6", "wx", 1001.0)[0])
        self.assertIn("NIEAKTUALNE", self.cmds.run("SQ9GIN-6", "wx", 1000.0 + 3 * 3600)[0])
