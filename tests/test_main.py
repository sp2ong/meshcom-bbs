import unittest

from mcbbs.__main__ import build_transport
from mcbbs.config import Config


class BuildTransportTests(unittest.TestCase):
    def test_udp_single_port_default_1799(self):
        tr = build_transport(Config(call="SP2ONG-1"), {"type": "udp", "host": "10.0.0.5"})
        self.assertEqual((tr.node_port, tr.listen_port), (1799, 1799))

    def test_udp_single_port_override_used_both_ways(self):
        tr = build_transport(Config(call="SP2ONG-1"), {"type": "udp", "host": "10.0.0.5", "port": 1800})
        self.assertEqual((tr.node_port, tr.listen_port), (1800, 1800))

    def test_udp_rejects_other_ssid(self):
        with self.assertRaises(SystemExit):
            build_transport(Config(call="SP2ONG-12", node_call="SP2ONG-1"), {"type": "udp", "host": "h"})


if __name__ == "__main__":
    unittest.main()
