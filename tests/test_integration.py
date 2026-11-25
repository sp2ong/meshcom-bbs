"""Test z prawdziwymi gniazdami: atrapa noda KISS na localhost <-> KissTransport <-> BBS."""
import socket
import time
import unittest

from mcbbs.config import Config
from mcbbs.service import BBS
from mcbbs.store import Store
from mcbbs.transports.kiss import KissTransport
from mcbbs.transports.kisscodec import Deframer, build_ax25, kiss_deslip, kiss_encode, parse_ax25


def read_frames(conn, n, timeout=3.0):
    """Czyta z atrapy noda n ramek KISS i zwraca je rozkodowane."""
    deframer, frames, end = Deframer(), [], time.time() + timeout
    conn.settimeout(0.2)
    while len(frames) < n and time.time() < end:
        try:
            data = conn.recv(4096)
        except socket.timeout:
            continue
        frames += [kiss_deslip(f) for f in deframer.feed(data)]
    return frames


class IntegrationTests(unittest.TestCase):
    def test_dm_roundtrip_over_real_tcp(self):
        server = socket.socket()
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]
        cfg = Config(call="SP2ONG-1", pace_s=0, prefixes=("bbs",), ack_incoming=True)
        store = Store()
        tr = KissTransport("127.0.0.1", port, "SP2ONG-1", "SP2ONG-1")
        bbs = BBS(cfg, store, tr)
        tr.start()
        try:
            conn, _ = server.accept()
            deadline = time.time() + 3
            while not tr.connected and time.time() < deadline:
                time.sleep(0.01)
            # node podaje DM od SQ9GIN-6 (jak w logu) ...
            conn.sendall(kiss_encode(build_ax25("SQ9GIN-6", "APRSMC", ":SP2ONG-1 :p{12")))
            # ... BBS najpierw potwierdza (ack), potem po ticku odpowiada
            ack = read_frames(conn, 1)[0]
            self.assertEqual(parse_ax25(ack[1:])[3], ":SQ9GIN-6 :ack12")
            reply = None
            deadline = time.time() + 3
            while reply is None and time.time() < deadline:
                bbs.tick()
                got = read_frames(conn, 1, timeout=0.3)
                reply = got[0] if got else None
            self.assertEqual(parse_ax25(reply[1:])[3], ":SQ9GIN-6 :pong{1")
            # node odpowiada 0xF0 na obie ramki, a potem SQ9GIN-6 potwierdza odpowiedz BBS (ack1)
            ok = bytes([0xF0, 0x01]) + (1).to_bytes(4, "little")
            conn.sendall(kiss_encode(ok[1:], 0xF0) * 2)
            conn.sendall(kiss_encode(build_ax25("SQ9GIN-6", "APRSMC", ":SP2ONG-1 :ack1")))
            deadline = time.time() + 3
            while store.outbox_counts().get("acked") != 1 and time.time() < deadline:
                time.sleep(0.02)
            self.assertEqual(store.outbox_counts(), {"acked": 1})
            conn.close()
        finally:
            tr.stop()
            server.close()


if __name__ == "__main__":
    unittest.main()
