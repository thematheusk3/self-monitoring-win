import socket
import unittest

import tests.helpers  # noqa: F401
from sm import netmon, winnet
from sm.hw import gpu


class NetTest(unittest.TestCase):
    def test_port_byte_order(self):
        # a tabela do Windows guarda a porta em ordem de rede nos 16 bits baixos
        self.assertEqual(winnet._port(socket.htons(443)), 443)
        self.assertEqual(winnet._port(socket.htons(8765) | 0xABCD0000), 8765)

    def test_classify_and_service(self):
        self.assertEqual(netmon.classify("127.0.0.1"), "local")
        self.assertEqual(netmon.classify("192.168.0.10"), "lan")
        self.assertEqual(netmon.classify("8.8.8.8"), "internet")
        self.assertEqual(netmon.classify("0.0.0.0"), "any")
        self.assertEqual(netmon.classify("::ffff:192.168.0.1"), "lan")
        self.assertEqual(netmon.service(443, "tcp"), "HTTPS")
        self.assertEqual(netmon.service(445, "tcp"), "SMB")
        self.assertIsNone(netmon.service(49152, "tcp"))   # porta alta sem nome conhecido

    def test_live_tables(self):
        cs = winnet.tcp_connections()
        self.assertTrue(cs, "a tabela TCP do Windows não deveria vir vazia")
        self.assertTrue(all(0 <= c["lport"] <= 65535 and c["state"] for c in cs))


class GpuCountersTest(unittest.TestCase):
    def test_engine_instance_names(self):
        m = gpu._ENG.match("pid_26968_luid_0x00000000_0x00014CA7_phys_0_eng_0_engtype_3D")
        self.assertEqual(m.groups(), ("26968", "0x00000000_0x00014CA7", "3D"))
        m = gpu._ENG.match("pid_4_luid_0x00000000_0x00014CA7_phys_0_eng_10_engtype_VideoDecode")
        self.assertEqual(m.group(3), "VideoDecode")
        self.assertEqual(gpu._AMEM.match("luid_0x00000000_0x0001676A_phys_0").group(1), "0x00000000_0x0001676A")


if __name__ == "__main__":
    unittest.main()
