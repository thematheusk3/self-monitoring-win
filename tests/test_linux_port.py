"""Recursos trazidos da versão Linux (PARIDADE.md, seção 2)."""
import tempfile
import threading
import unittest
from pathlib import Path

from tests.helpers import Ctx

from sm import game, updates
from sm.collectors.sensors import fan_hint


def _vdf_entry(idx: bytes, appid: int, name: str, path: str) -> bytes:
    return (b"\x00" + idx + b"\x00" + b"\x02appid\x00" + appid.to_bytes(4, "little")
            + b"\x01AppName\x00" + name.encode() + b"\x00"
            + b"\x01Exe\x00" + f'"{path}"'.encode() + b"\x00\x08")


class GameExtrasTest(unittest.TestCase):
    """CPU/GPU ocupada do PresentMon, atalhos não-Steam (shortcuts.vdf) e jogos achados pela pasta da Steam."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        steam = self.tmp / "Steam"
        (steam / "steamapps" / "common" / "ELDEN RING" / "Game").mkdir(parents=True)
        (steam / "steamapps" / "appmanifest_1245620.acf").write_text(
            '"AppState"\n{\n\t"appid"\t"1245620"\n\t"name"\t"ELDEN RING"\n\t"installdir"\t"ELDEN RING"\n}\n',
            encoding="utf-8")
        cfg = steam / "userdata" / "123" / "config"
        cfg.mkdir(parents=True)
        self.exe = str(steam / "steamapps" / "common" / "ELDEN RING" / "Game" / "ersc_launcher.exe")
        vdf = (b"\x00shortcuts\x00" + _vdf_entry(b"0", 3593100922, "Seamless Coop", self.exe)
               + _vdf_entry(b"1", 2999999999, "Emulador", r"C:\Emu\emu.exe") + b"\x08\x08")
        (cfg / "shortcuts.vdf").write_bytes(vdf)
        self._orig = game.STEAM_ROOTS
        game.STEAM_ROOTS = [steam]
        self._clear()

    def tearDown(self):
        game.STEAM_ROOTS = self._orig
        self._clear()

    @staticmethod
    def _clear():
        game._names.clear()
        game._common_cache.clear()
        game._shortcuts_cache = ((), {})

    def test_busy_percent(self):
        ex = [(0, 10.0, 4.0, 9.0, 20.0), (0, 10.0, 6.0, None, None)]
        self.assertEqual(game._busy(ex, 2), 50.0)
        self.assertEqual(game._busy(ex, 3), 90.0)
        self.assertIsNone(game._busy([(0, 10.0, None, None, None)], 2))

    def test_shortcuts(self):
        self.assertEqual(game.steam_shortcuts(), {"3593100922": "Seamless Coop", "2999999999": "Emulador"})
        s = game.steam_shortcut("3593100922")
        self.assertEqual((s["game_appid"], s["game"]), ("1245620", "ELDEN RING"))
        self.assertNotIn("game_appid", game.steam_shortcut("2999999999"))
        self.assertEqual(game.shortcut_by_exe(r"C:\Emu\EMU.exe"), "Emulador")

    def test_exe_in_steam_folder_is_that_game(self):
        self.assertEqual(game._common_appid(self.exe), "1245620")
        self.assertIsNone(game._common_appid(r"C:\Jogos\x.exe"))

    def test_running_shortcut(self):
        ctx = Ctx()
        ctx.processes, ctx.data = [], {}
        orig = (game.running_steam_appid, game.STATE_FILE)
        game.STATE_FILE = self.tmp / "game.json"
        try:
            g = game.GameMonitor(ctx)
            game.running_steam_appid = lambda: "3593100922"
            self.assertEqual(g.running_game()["appid"], "1245620")
            game.running_steam_appid = lambda: "2999999999"
            r = g.running_game()
            self.assertEqual((r["appid"], r["name"], r["exe"]), (None, "Emulador", "emu.exe"))
        finally:
            game.running_steam_appid, game.STATE_FILE = orig


class NonFiniteTest(unittest.TestCase):
    """NaN/infinito no JSON congelava a página inteira (o navegador recusa e para de atualizar)."""

    def test_presentmon_values(self):
        self.assertIsNone(game._num(["inf"], 0))
        self.assertIsNone(game._num(["NaN"], 0))
        self.assertIsNone(game._num(["1e309"], 0))
        self.assertEqual(game._num(["8.5"], 0), 8.5)

    def test_dumps_never_emits_nan(self):
        import json
        from sm.server import dumps
        out = dumps({"a": float("inf"), "b": [float("nan"), 1.5], "c": {"d": -float("inf")}})
        self.assertEqual(json.loads(out), {"a": None, "b": [None, 1.5], "c": {"d": None}})
        self.assertNotIn("Infinity", out)


class FanHintTest(unittest.TestCase):
    def test_hint(self):
        self.assertIsNone(fan_hint([], [{"rpm": 900}], "MSI"))
        self.assertIn("PawnIO", fan_hint([{"driver": "amdcpu"}], [], "MSI"))
        self.assertIn("hub", fan_hint([{"driver": "lpc"}], [], ""))


class RebootTest(unittest.TestCase):
    def test_status_has_reboot_fields(self):
        u = updates.Updates.__new__(updates.Updates)
        u.lock, u.running = threading.Lock(), False
        u.data = {"items": [], "count": 0, "checked": 1, "reboot": True}     # cache de versão antiga
        st = u.status()
        self.assertTrue(st["reboot_items"])
        self.assertTrue(st["reboot"] is None or st["reboot"]["reason"] == "sistema")


if __name__ == "__main__":
    unittest.main()
