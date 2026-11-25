import json
import os
import socket
import tempfile
import time
import unittest

from mcbbs.commands import Commands
from mcbbs.config import Config, load
from mcbbs.gateways.js8call import Js8Gateway
from mcbbs.service import BBS
from mcbbs.store import Store
from tests.fake import FakeTransport

T0 = 1_000_000.0


def directed(frm, to, text, cmd="", utc=1769740226361):
    """Wiadomosc w ksztalcie z dokumentacji API JS8Call (RX.DIRECTED)."""
    return {"params": {"CMD": cmd, "DIAL": 7078000, "FREQ": 7078816, "FROM": frm, "GRID": "", "OFFSET": 816,
                       "SNR": 1, "SPEED": 0, "TO": to, "UTC": utc, "_ID": -1},
            "type": "RX.DIRECTED", "value": "%s: %s %s ♢ " % (frm, to, text)}


class Js8Base(unittest.TestCase):
    def setUp(self):
        self.cfg = Config(call="SP2ONG-1", pace_s=0)
        self.cfg.js8 = {"host": "127.0.0.1", "groups": ("@GRM",), "urgent": ("@EMC",), "private": ("@PM",),
                        "urgent_group": 9, "urgent_groups": (26024,), "urgent_send": True, "urgent_max_per_hour": 3}
        self.store, self.tr = Store(), FakeTransport()
        self.bbs = BBS(self.cfg, self.store, self.tr, clock=lambda: T0)
        self.make_gw()
        self.cmds = Commands(self.store, self.cfg, T0)
        self.store.add_user("SQ9GIN", "SQ9GIN-6", T0)

    def make_gw(self):
        self.gw = Js8Gateway(self.cfg, self.store, urgent_cb=self.bbs.urgent_notice,
                             forward_cb=lambda g, t, now: self.bbs.send_group(g, t, now), clock=lambda: T0)

    def say(self, cmd, src="SQ9GIN-6"):
        return self.cmds.run(src, cmd, T0)

    def drain(self, n=10):
        for _ in range(n):
            self.bbs.tick(T0)
        return [(d, t) for d, t, _k in self.tr.sent]


class GroupTests(Js8Base):
    def test_group_message_becomes_bulletin_with_title(self):
        self.assertEqual(self.gw.handle_message(directed("SP9XYZ", "@GRM", "Zalew : woda w centrum")), "bulletin:1")
        self.assertIn("[1] SP9XYZ@JS8: Zalew", " ".join(self.say("bl")))
        self.assertEqual(self.say("br 1"), ["[1] SP9XYZ@JS8 Zalew: woda w centrum"])
        self.assertEqual(self.drain(), [])                                   # zwykla grupa nic nie nadaje

    def test_no_colon_means_no_title(self):
        self.gw.handle_message(directed("SP9XYZ", "@GRM", "sama tresc"))
        self.assertIn("SP9XYZ@JS8: -", " ".join(self.say("bl")))

    def test_technical_traffic_and_other_targets_are_ignored(self):
        for msg in (directed("KE2DMC", "@HB", "", cmd=" HEARTBEAT"),
                    directed("SP9XYZ", "@INNA", "tekst"),
                    directed("SP9XYZ", "SP2ONG", "SP2ABC hej"),                  # DM bez @PM: nie przyjmujemy
                    directed("SP9XYZ", "@GRM", "SNR?", cmd=" SNR")):
            res = self.gw.handle_message(msg)
            self.assertTrue(res.startswith("ignored"), (msg["value"], res))
        for msg in ({"type": "RX.SPOT", "params": {"CALL": "SP9XYZ"}, "value": ""},
                    {"type": "RX.ACTIVITY", "params": {}, "value": "fragment"}):
            self.assertIsNone(self.gw.handle_message(msg))
        self.assertEqual(self.say("bl"), ["Tablica pusta."])

    def test_same_message_twice_is_stored_once_but_resend_later_is_new(self):
        msg = directed("SP9XYZ", "@GRM", "test")
        self.assertEqual(self.gw.handle_message(msg), "bulletin:1")
        self.assertEqual(self.gw.handle_message(msg), "duplicate")
        self.assertEqual(self.gw.handle_message(directed("SP9XYZ", "@GRM", "test", utc=1769740999999)), "bulletin:2")

    def test_msg_command_text_is_kept_without_the_command_word(self):
        self.gw.handle_message(directed("SP9XYZ", "@GRM", "MSG T : wszystko ok", cmd=" MSG"))
        self.assertEqual(self.say("br 1"), ["[1] SP9XYZ@JS8 T: wszystko ok"])


class RealTrafficTests(Js8Base):
    """Prawdziwy ruch z JS8Call (SP2RXY -> SP2ONG) przy konfiguracji z grupa @MC i poczta DM."""

    def test_real_js8call_log(self):
        self.cfg.js8.update(groups=("@MC",), private=("SP2ONG",))     # stary log: DM-y szly na znak stacji
        self.make_gw()
        self.store.add_user("SQ9GIN", "SQ9GIN-6", T0)
        path = os.path.join(os.path.dirname(__file__), "data", "js8_real.jsonl")
        results = [self.gw.handle_message(json.loads(line)) for line in open(path, encoding="utf-8") if line.strip()]
        self.assertEqual([r for r in results if r], ["bulletin:1", "private:2", "private:3"])
        rows = [(r["kind"], r["from_call"], r["to_call"], r["subject"], r["body"])
                for r in self.store._all("SELECT * FROM messages ORDER BY id")]
        self.assertEqual(rows, [
            ("bulletin", "SP2RXY", None, "", "POCZTA WYSLANA NA GRUPE @MC"),
            ("private", "SP2RXY", "SQ9GIN", "POCZTA DM", "TEST POCZTY"),
            ("private", "SP2RXY", "SQ9GIN", "DRUGI DM", "TEST Z DIRECT CALLSIGN")])


class UrgentTests(Js8Base):
    def test_emc_is_marked_and_forwarded_to_default_group_without_ack(self):
        self.assertEqual(self.gw.handle_message(directed("SP1GHK", "@EMC", "Pilny kontakt : potrzebna lacznosc")),
                         "urgent:1")
        self.assertTrue(" ".join(self.say("bl")).startswith("![1] SP1GHK@JS8: EMC: Pilny kontakt"))
        self.assertEqual(self.drain(), [("9", "EMC: SP1GHK@JS8 Pilny kontakt: potrzebna lacznosc")])
        self.assertEqual(self.store._all("SELECT state FROM outbox")[0]["state"], "sent")   # bez oczekiwania na ACK

    def test_emc_with_allowed_group_goes_there(self):
        self.assertEqual(self.gw.handle_message(directed("SP1GHK", "@EMC26024", "Toruń : test")), "urgent:1")
        self.assertEqual([d for d, _ in self.drain()], ["26024"])

    def test_emc_with_unlisted_group_is_ignored_and_not_stored(self):
        res = self.gw.handle_message(directed("SP1GHK", "@EMC12345", "x : y"))
        self.assertTrue(res.startswith("ignored"))
        self.assertEqual(self.say("bl"), ["Tablica pusta."])
        self.assertEqual(self.drain(), [])
        self.assertTrue(self.gw.handle_message(directed("SP1GHK", "@EMC123456", "x : y")).startswith("ignored"))

    def test_urgent_send_off_stores_only(self):
        self.cfg.js8["urgent_send"] = False
        self.make_gw()
        self.assertEqual(self.gw.handle_message(directed("SP1GHK", "@EMC", "A : b")), "urgent:1")
        self.assertEqual(self.drain(), [])

    def test_rate_limit_keeps_entries_but_stops_forwards(self):
        for i in range(5):
            self.gw.handle_message(directed("SP1GHK", "@EMC", "A : alarm %d" % i, utc=1000 + i))
        self.assertEqual(len(self.drain(20)), 3)
        self.assertEqual(self.store.count_bulletins(T0), 5)

    def test_repeat_queues_copies(self):
        self.cfg.js8["urgent_repeat"] = 2
        self.gw.handle_message(directed("SP1GHK", "@EMC", "A : b"))
        self.assertEqual(self.store._all("SELECT COUNT(*) n FROM outbox")[0]["n"], 2)

    def test_long_emc_is_chunked_within_limit(self):
        self.gw.handle_message(directed("SP1GHK", "@EMC", "Tytul : " + "slowo " * 60))
        sent = self.drain(20)
        self.assertGreater(len(sent), 1)
        for dst, text in sent:
            self.assertLessEqual(len(("{%s}" % dst + text).encode()) - 1, 160)

    def test_dm_to_subscribers_only_when_urgent_dm(self):
        self.say("urgent on")
        self.cfg.js8["urgent_send"] = False
        self.make_gw()
        self.gw.handle_message(directed("SP9XYZ", "@EMC", "A : b"))
        self.assertEqual(self.drain(), [])
        self.cfg.js8["urgent_dm"] = True
        self.make_gw()
        self.gw.handle_message(directed("SP9XYZ", "@EMC", "A : c", utc=5))
        self.assertEqual(self.drain(), [("SQ9GIN-6", "PILNE EMC od SP9XYZ. Czytaj: br 2")])

    def test_urgent_command_status_and_off(self):
        self.assertIn("wylaczone", self.say("urgent")[0])
        self.assertEqual(self.say("urgent on"), ["Powiadomienia PILNE: wlaczone."])
        self.say("urgent off")
        self.assertEqual(self.store.urgent_subscribers(), [])


class PrivateTests(Js8Base):
    def test_private_for_registered_user(self):
        self.say("l", "SP2ABC-7")
        res = self.gw.handle_message(directed("SP9XYZ", "@PM", "SP2ABC Temat : zadzwon do mnie"))
        self.assertEqual(res, "private:1")
        self.assertIn("*[1] SP9XYZ@JS8: Temat", " ".join(self.say("l", "SP2ABC-7")))
        self.assertEqual(self.say("r 1", "SP2ABC-7"), ["[1] od SP9XYZ@JS8 Temat: zadzwon do mnie"])

    def test_private_without_subject(self):
        self.say("l", "SP2ABC-7")
        self.gw.handle_message(directed("SP9XYZ", "@PM", "SP2ABC czesc"))
        self.assertEqual(self.say("r 1", "SP2ABC-7"), ["[1] od SP9XYZ@JS8: czesc"])

    def test_unknown_user_and_full_mailbox_store_nothing(self):
        self.assertEqual(self.gw.handle_message(directed("SP9XYZ", "@PM", "SP2ABC hej")), "unknown-user")
        self.say("l", "SP2ABC-7")
        for i in range(5):
            self.gw.handle_message(directed("SP9XYZ", "@PM", "SP2ABC t%d" % i, utc=i))
        self.assertEqual(self.gw.handle_message(directed("SP9XYZ", "@PM", "SP2ABC za duzo", utc=99)), "mailbox-full")


class StreamTests(unittest.TestCase):
    def test_drain_splits_concatenated_and_partial_objects(self):
        a, b = json.dumps({"type": "A", "params": {}, "value": ""}), json.dumps({"type": "B", "params": {}, "value": "x"})
        objs, rest = Js8Gateway.drain(a + b + "\n" + b[:10])
        self.assertEqual([o["type"] for o in objs], ["A", "B"])
        self.assertEqual(rest, b[:10])
        objs2, rest2 = Js8Gateway.drain(rest + b[10:])
        self.assertEqual([o["type"] for o in objs2], ["B"])
        self.assertEqual(rest2, "")

    def test_garbage_line_is_skipped(self):
        objs, _ = Js8Gateway.drain('smiec\n{"type":"A","params":{},"value":""}\n')
        self.assertEqual([o["type"] for o in objs], ["A"])


class SocketTests(unittest.TestCase):
    def test_real_tcp_with_split_utf8(self):
        server = socket.socket()
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        cfg = Config(call="SP2ONG-1", pace_s=0)
        cfg.js8 = {"host": "127.0.0.1", "port": server.getsockname()[1], "groups": ("@GRM",), "urgent": ()}
        store = Store()
        gw = Js8Gateway(cfg, store)
        gw.start()
        try:
            conn, _ = server.accept()
            conn.settimeout(3)
            payload = (json.dumps(directed("SP9XYZ", "@GRM", "T : zażółć gęślą jaźń"),
                                                 ensure_ascii=False) + "\n").encode()
            cut = payload.index("ż".encode()) + 1                                # w srodku znaku 2-bajtowego
            conn.sendall(payload[:cut])
            time.sleep(0.2)
            conn.sendall(payload[cut:])
            deadline = time.time() + 3
            while not store.bulletins(5, time.time()) and time.time() < deadline:
                time.sleep(0.05)
            rows = store.bulletins(5, time.time())
            self.assertEqual(rows[0]["body"], "zażółć gęślą jaźń")
            conn.close()
        finally:
            gw.stop()
            server.close()


class ConfigTests(unittest.TestCase):
    def test_js8call_section_is_loaded_and_validated(self):
        d = tempfile.mkdtemp()
        path = os.path.join(d, "c.toml")
        with open(path, "w") as f:
            f.write('[bbs]\ncall="SP2ONG-1"\n[js8call]\nhost="127.0.0.1"\nport=2442\n'
                    'groups=["@grm"]\nurgent=["@EMC"]\nprivate=["@pm"]\nurgent_groups=[26024]\n')
        cfg, _ = load(path)
        self.assertEqual(cfg.js8["groups"], ("@GRM",))
        self.assertEqual(cfg.js8["urgent"], ("@EMC",))
        self.assertEqual(cfg.js8["private"], ("@PM",))
        self.assertEqual((cfg.js8["urgent_group"], cfg.js8["urgent_groups"]), (9, (26024,)))
        with open(path, "a") as f:
            f.write("urgent_groups=[123456]\n")
        with self.assertRaises(ValueError):
            load(path)
        s = open(path).read().replace("urgent_groups=[123456]\n", "")
        open(path, "w").write(s)
        with open(path, "a") as f:
            f.write("bogus=1\n")
        with self.assertRaises(ValueError):
            load(path)


if __name__ == "__main__":
    unittest.main()
