"""Testy transportow, m.in. na prawdziwych danych z logow noda SP2ONG-1 (2026-11-03)."""
import socket
import unittest

from mcbbs.transports.kiss import KissTransport
from mcbbs.transports.kisscodec import (Deframer, build_ax25, kiss_deslip, kiss_encode,
                                        parse_aprs_message, parse_ax25)
from mcbbs.transports.udp import UdpTransport


def data_frame(src, info, dst="APRSMC"):
    return bytes([0x00]) + build_ax25(src, dst, info)


class KissCodecTests(unittest.TestCase):
    def test_escaping_roundtrip_and_stream_reassembly(self):
        payload = build_ax25("SP2XYZ-7", "APRS", ":SP2ABC-1  :x{1") + bytes([0xC0, 0xDB, 0x41])
        raw = kiss_encode(payload)
        self.assertEqual(raw.count(b"\xC0"), 2)
        d = Deframer()
        frames = []
        for i in range(0, len(raw * 2), 5):          # dwie ramki, pociete na kawalki jak w TCP
            frames += d.feed((raw * 2)[i:i + 5])
        self.assertEqual(len(frames), 2)
        self.assertEqual(kiss_deslip(frames[0]), bytes([0]) + payload)

    def test_parse_message_variants(self):
        p = parse_aprs_message(":SP2ONG-1 :gggg{819")
        self.assertEqual((p["addressee"], p["text"], p["msgno"], p["is_ack"]), ("SP2ONG-1", "gggg", "819", False))
        self.assertTrue(parse_aprs_message(":SP2ONG-1 :ack110")["is_ack"])
        self.assertEqual(parse_aprs_message(":OE1XYZ-99:hi{01")["addressee"], "OE1XYZ-99")
        self.assertIsNone(parse_aprs_message("!5303.36NM01833.81E#x"))
        # broadcast '*' z logu: pusty adresat; tekst z '{' ale bez numeru {nn
        p = parse_aprs_message(":         :{CET}2026-11-03 16:12:19")
        self.assertEqual((p["addressee"], p["msgno"]), ("", None))


class KissTransportTests(unittest.TestCase):
    def setUp(self):
        self.t = KissTransport("x", 8001, "SP2ONG-1", "SP2ONG-1")
        self.msgs, self.acks, self.results, self.heard = [], [], [], []
        self.t.on_message = self.msgs.append
        self.t.on_ack = self.acks.append
        self.t.on_tx_result = lambda k, ok, info: self.results.append((k, ok, info))
        self.t.on_heard = self.heard.append

    def test_beacon_comment_gives_telemetry_only_from_own_node(self):
        got = []
        self.t.on_telemetry = got.append
        info = "!5213.40N/01653.20E#/B=091/A=000230/P=1022.7/H=47.9/T=18.5/F=70/Q=1031.1/R=0"
        self.t.handle_frame(data_frame("SP2ONG-1", info))
        self.t.handle_frame(data_frame("SQ9GIN-6", info))                    # cudza pozycja: pomijana
        self.assertEqual(got, [{"qfe": 1022.7, "hum": 47.9, "temp1": 18.5, "qnh": 1031.1}])

    def test_incoming_dm_with_polish_text(self):
        self.t.handle_frame(data_frame("SQ9GIN-6", ":SP2ONG-1 :bądź tu mądry i pisz wiersze{825"))
        m = self.msgs[0]
        self.assertEqual((m.src, m.dst, m.text, m.msgno), ("SQ9GIN-6", "SP2ONG-1", "bądź tu mądry i pisz wiersze", "825"))

    def test_srcinfo_overrides_clamped_ssid(self):
        self.t.handle_frame(bytes([0x20]) + b"OE1XAR-26")
        self.t.handle_frame(data_frame("OE1XAR-15", ":         :hello"))
        self.assertEqual(self.msgs[0].src, "OE1XAR-26")
        self.assertEqual(self.msgs[0].dst, "")
        self.t.handle_frame(data_frame("OE1XAR-15", ":         :again"))   # override dziala tylko raz
        self.assertEqual(self.msgs[1].src, "OE1XAR-15")

    def test_ack_uses_our_original_number(self):
        self.t.handle_frame(data_frame("SQ9GIN-6", ":SP2ONG-1 :ack110"))      # z logu: ack110
        self.assertEqual(self.acks, [110])
        self.assertEqual(self.msgs, [])
        self.t.handle_frame(data_frame("SQ9GIN-6", ":INNYZNAK  :ack5"))      # nie do nas
        self.assertEqual(self.acks, [110])

    def test_position_only_counts_as_heard(self):
        self.t.handle_frame(data_frame("SP2ONG-1", "!5303.36NM01833.81E#MeshCom#Waldek/B=078"))
        self.assertEqual(self.msgs, [])
        self.assertEqual(self.heard, ["SP2ONG-1"])

    def test_tx_result_matches_send_order(self):
        a, b = socket.socketpair()
        self.t._sock = a
        k1 = self.t.send("sq9gin-6", "pierwsza")
        k2 = self.t.send("SQ9GIN-6", "druga")
        self.t.handle_frame(bytes([0xF0, 0x01]) + (123).to_bytes(4, "little"))
        self.t.handle_frame(bytes([0xF0, 0x04]))
        self.assertEqual(self.results[0][:2], (k1, True))
        self.assertEqual(self.results[1][:2], (k2, False))
        self.assertIn("za dlugi", self.results[1][2])
        a.close()
        got = b""
        while True:
            chunk = b.recv(4096)
            if not chunk:
                break
            got += chunk
        frames = [kiss_deslip(f) for f in Deframer().feed(got)]
        src, dst, digis, info = parse_ax25(frames[0][1:])
        self.assertEqual((src, info), ("SP2ONG-1", ":SQ9GIN-6 :pierwsza{1"))   # adresat dokladnie 9 znakow

    def test_send_ack_wire_format(self):
        a, b = socket.socketpair()
        self.t._sock = a
        self.t.send_ack("sq9gin-6", "819")
        a.close()
        frame = kiss_deslip(Deframer().feed(b.recv(4096))[0])
        self.assertEqual(parse_ax25(frame[1:])[3], ":SQ9GIN-6 :ack819")

    def test_send_without_connection_raises(self):
        with self.assertRaises(ConnectionError):
            self.t.send("SQ9GIN-6", "x")


class UdpTransportTests(unittest.TestCase):
    """JSON-y skopiowane z logu: wysylka 'UDP Test' do SQ9GIN-6 i jej potwierdzenie."""

    def setUp(self):
        self.t = UdpTransport("127.0.0.1", node_call="SP2ONG-1")
        self.t._sock = type("NoNet", (), {"sendto": lambda s, data, addr: None})()   # bez prawdziwej sieci
        self.msgs, self.acks = [], []
        self.t.on_message = self.msgs.append
        self.t.on_ack = self.acks.append

    def test_tele_frame_int_zero_kept_for_wx_to_hide(self):
        got = []
        self.t.on_telemetry = got.append
        self.t.handle_json({"type": "tele", "src": "SP2ONG-1", "temp1": 18.46, "temp2": 0, "hum": 47.94,
                            "qfe": 1022.72, "qnh": 1031.116, "gas": 0, "co2": 0})
        self.t.handle_json({"type": "tele", "src": "SQ9GIN-6", "temp1": 5.5})   # cudzy node: pomijany
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["temp1"], 18.46)
        self.assertIsInstance(got[0]["temp2"], int)

    def test_echo_then_ack_correlates_by_msg_id(self):
        key = self.t.send("sq9gin-6", "UDP Test")
        echo = {'src_type': 'node', 'type': 'msg', 'src': 'SP2ONG-1', 'dst': 'SQ9GIN-6',
                'msg': 'UDP Test{305', 'msg_id': 'A2C5C131', 'firmware': '4.40', 'fw_sub': 'a',
                'rssi': 0, 'snr': 0, 'hw_id': 44, 'lora_mod': 8, 'max_hop': 4}
        self.t.handle_json(echo)
        self.assertEqual(self.msgs, [])                    # echo wlasnej wysylki to nie wiadomosc
        pseudo = {'src_type': 'udp', 'type': 'msg', 'src': 'SQ9GIN-6', 'dst': 'SP2ONG-1',
                  'msg': 'SP2ONG-1 :ack305', 'msg_id': 'A31232BF'}
        self.t.handle_json(pseudo)
        self.assertEqual(self.msgs, [])                    # duplikat ACK-a jako tekst ignorowany
        self.t.handle_json({'type': 'ack', 'msg_id': 'A2C5C131', 'status': 2, 'from': 'SQ9GIN-6', 'via': 'udp'})
        self.assertEqual(self.acks, [key])

    def test_unknown_ack_or_status_ignored(self):
        self.t.handle_json({'type': 'ack', 'msg_id': 'DEADBEEF', 'status': 2})
        self.t.send("SQ9GIN-6", "x")
        self.t.handle_json({'src_type': 'node', 'type': 'msg', 'src': 'SP2ONG-1', 'dst': 'SQ9GIN-6',
                            'msg': 'x{1', 'msg_id': 'AA'})
        self.t.handle_json({'type': 'ack', 'msg_id': 'AA', 'status': 1})
        self.assertEqual(self.acks, [])

    def test_incoming_dm_from_log(self):
        self.t.handle_json({'src_type': 'udp', 'type': 'msg', 'src': 'SQ9GIN-6', 'dst': 'SP2ONG-1',
                            'msg': 'jeszcze jeden teścik{820', 'msg_id': 'A3123334', 'firmware': 40})
        m = self.msgs[0]
        self.assertEqual((m.src, m.dst, m.text, m.msgno, m.msg_id),
                         ("SQ9GIN-6", "SP2ONG-1", "jeszcze jeden teścik", "820", "A3123334"))

    def test_group_and_broadcast_destinations(self):
        self.t.handle_json({'src_type': 'udp', 'type': 'msg', 'src': 'OE1XAR-26', 'dst': '*',
                            'msg': '{CET}2026-11-03 16:12:19', 'msg_id': '6ADD074C'})
        self.t.handle_json({'src_type': 'udp', 'type': 'msg', 'src': 'SQ5FNF-1', 'dst': '260',
                            'msg': 'namawiam kolegów', 'msg_id': '7AC560EC'})
        self.assertEqual([m.dst for m in self.msgs], ["", "260"])
        self.assertEqual(self.msgs[0].msgno, None)


if __name__ == "__main__":
    unittest.main()
