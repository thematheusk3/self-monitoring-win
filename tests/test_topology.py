"""Monitores (principal, ligar/desligar). Nada aqui muda a tela: só leitura e SDC_VALIDATE."""
import unittest
from unittest import mock

import tests.helpers  # noqa: F401
from sm import topology
from sm.display import DisplayError


class TopologyTest(unittest.TestCase):
    def test_reads_screens(self):
        st = topology.screens()
        self.assertIn(st["mode"], ("extend", "clone", "single"))
        active = [s for s in st["screens"] if s["active"]]
        self.assertTrue(active, "deveria haver ao menos um monitor ligado")
        if st["mode"] == "extend":
            self.assertEqual(sum(s["primary"] for s in active), 1)
        for s in st["screens"]:
            self.assertTrue(topology.RX_ID.fullmatch(s["id"]))

    def test_windows_accepts_primary_and_disable_without_applying(self):
        st = topology.screens()
        on = [s for s in st["screens"] if s["active"]]
        if st["mode"] != "extend" or len(on) < 2:
            self.skipTest("precisa de dois monitores estendidos")
        topology.set_primary(on[-1]["id"], validate=True)
        topology.disable(on[0]["id"], validate=True)

    def test_rejects_bad_ids_and_last_screen(self):
        with self.assertRaises(DisplayError):
            topology.disable("x; rm -rf")
        with self.assertRaises(DisplayError):
            topology.TopologyChanger().apply("extend")      # estender/duplicar foram removidos
        one = topology.screens()
        active = [s for s in one["screens"] if s["active"]]
        paths, modes = topology._query(topology.QDC_ONLY_ACTIVE_PATHS)
        only = [p for p in paths if topology._tid(p) == active[0]["id"]]
        with mock.patch.object(topology, "_query", return_value=(only, modes)):
            with self.assertRaises(DisplayError) as e:
                topology.disable(active[0]["id"], validate=True)
        self.assertIn("único monitor", str(e.exception))

    def test_pending_reverts_on_timeout(self):
        t = topology.TopologyChanger()
        st = topology.screens()
        tid = next(s["id"] for s in st["screens"] if s["active"])
        with mock.patch.object(topology, "set_primary") as sp, mock.patch.object(topology.threading, "Timer"), \
                mock.patch.object(topology, "_set") as setter:
            p = t.apply("primary", tid)
            sp.assert_called_once_with(tid)
            self.assertEqual(p["op"], "primary")
            with self.assertRaises(DisplayError):          # uma mudança por vez
                t.apply("primary", tid)
            t._timeout(t.pending)                           # sem confirmação: volta à configuração salva
            setter.assert_called_once()
            self.assertIsNone(t.status())


if __name__ == "__main__":
    unittest.main()
