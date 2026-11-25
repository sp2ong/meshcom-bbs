import os
import tempfile
import unittest

from mcbbs.config import Config
from mcbbs.service import BBS
from mcbbs.shell import AdminShell, Quit
from mcbbs.store import Store
from tests.fake import FakeTransport

T0 = 1_000_000.0


class ShellTests(unittest.TestCase):
    def setUp(self):
        self.cfg = Config(call="SP2ONG-1", pace_s=0, prefixes=("bbs",))
        self.store = Store()
        self.t = T0
        self.sh = AdminShell(self.cfg, self.store, "SP2ONG-7", clock=lambda: self.t)

    def test_bbs_commands_work_like_over_radio(self):
        self.assertEqual(self.sh.execute("!list"), ["Brak wiadomosci."])
        self.assertEqual(self.sh.execute("bs Temat: z shella END"), ["Dodano #1."])
        self.assertEqual(self.sh.execute("br 1"), ["[1] SP2ONG-7 Temat: z shella"])

    def test_multiline_message_in_shell(self):
        self.sh.execute("bs Temat:")
        self.assertEqual(self.sh.execute("czesc pierwsza"), [])
        self.assertEqual(self.sh.execute("druga END"), ["Dodano #1."])
        self.assertEqual(self.sh.execute("br 1"), ["[1] SP2ONG-7 Temat: czesc pierwsza druga"])

    def test_stats_users_and_purge(self):
        self.sh.execute("bs A: a END")
        out = self.sh.execute("stats")
        self.assertIn("Users    : 1", out)
        self.assertIn("Bulletins: 1 (waznych)", out)
        self.assertEqual(len(self.sh.execute("users")), 1)
        self.assertEqual(self.sh.execute("purge-posts --days 30"), ["Usunieto 0 wpis(ow) starszych niz 30 dni."])
        self.t = T0 + 31 * 86400
        self.assertEqual(self.sh.execute("purge-posts --days 30"), ["Usunieto 1 wpis(ow) starszych niz 30 dni."])
        self.assertEqual(self.sh.execute("purge-posts --days 30"), ["Usunieto 0 wpis(ow) starszych niz 30 dni."])
        self.assertEqual(self.sh.execute("purge-posts"), ["Uzycie: purge-posts --days N [--all]"])

    def test_del_user_and_bulletin(self):
        self.sh.execute("add")
        self.sh.execute("as SQ9GIN-6")
        self.sh.execute("s SP2ONG T:hej END")
        self.assertEqual(self.sh.execute("del-user SP2ONG"), ["Usunieto SP2ONG i 1 wiad. prywatnych."])
        self.assertEqual(self.sh.execute("del-user SP2ONG"), ["Nie ma takiego uzytkownika."])
        self.assertEqual(self.sh.execute("bs T: x END"), ["Dodano #2."])
        self.assertEqual(self.sh.execute("del-bulletin 2"), ["Usunieto."])
        self.assertEqual(self.sh.execute("br 2"), ["Brak wpisu #2."])
        self.assertEqual(self.sh.execute("del-bulletin 99"), ["Brak wpisu #99."])

    def test_dm_goes_to_outbox_and_quit(self):
        out = self.sh.execute("dm SQ9GIN-6 Czesc, tu BBS")
        self.assertIn("W kolejce: 1", out[0])
        self.assertEqual(self.store.outbox_rows()[0]["to_addr"], "SQ9GIN-6")
        self.assertIn("SQ9GIN-6", self.sh.execute("queue")[0])
        with self.assertRaises(Quit):
            self.sh.execute("quit")

    def test_second_process_does_not_reset_waiting_and_daemon_sends_shell_dm(self):
        path = os.path.join(tempfile.mkdtemp(), "bbs.sqlite3")
        daemon_store, tr = Store(path), FakeTransport()
        bbs = BBS(self.cfg, daemon_store, tr, clock=lambda: T0)
        daemon_store.enqueue("SQ9GIN-6", "stary", T0, 3)
        bbs.tick(T0)
        self.assertEqual(daemon_store.outbox_counts(), {"waiting": 1})
        shell = AdminShell(self.cfg, Store(path, reset_waiting=False), "SP2ONG-7", clock=lambda: T0)
        self.assertEqual(daemon_store.outbox_counts(), {"waiting": 1})      # shell nie ruszyl 'waiting'
        shell.execute("dm SP2ABC-7 z shella")
        bbs.tick(T0 + 10)
        self.assertEqual([t for _d, t, _k in tr.sent], ["stary", "z shella"])


if __name__ == "__main__":
    unittest.main()
