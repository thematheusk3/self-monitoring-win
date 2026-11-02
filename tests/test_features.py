"""Timer de energia, troca de resolução com reversão, alertas, FPS, áudio, ações e atualizações (tudo simulado)."""
import os
import tempfile
import time
import unittest
from collections import deque
from pathlib import Path

from tests.helpers import Ctx

from sm import actions, audio, display, game, power, updates
from sm import alerts as al


class PowerTimerTest(unittest.TestCase):
    def setUp(self):
        self.calls, self.notes = [], []
        self._orig = (power.request, power.PowerTimer._notify)
        power.request = lambda action, force: self.calls.append((action, force))
        power.PowerTimer._notify = staticmethod(lambda t, b, urgency="normal": self.notes.append(t))
        self.t = power.PowerTimer()
        self.t.file = Path(tempfile.mkdtemp()) / "timer.json"

    def tearDown(self):
        power.request, power.PowerTimer._notify = self._orig

    def test_schedule_cancel(self):
        st = self.t.schedule("poweroff", 30)
        self.assertAlmostEqual(st["seconds_left"], 1800, delta=2)
        self.assertTrue(self.t.file.exists())
        self.t.cancel()
        self.assertIsNone(self.t.status())
        self.assertFalse(self.t.file.exists())
        self.assertEqual(self.calls, [])

    def test_fires_forced(self):
        self.t.schedule("reboot", 1)
        with self.t.lock:
            self.t.timer["at"] = time.time() - 1
        time.sleep(2.5)                    # o laço verifica a cada 2 s
        self.assertEqual(self.calls, [("reboot", True)])   # sempre forçado

    def test_validation(self):
        for a, m in (("halt", 10), ("poweroff", 0), ("poweroff", 99999), ("poweroff", "x")):
            with self.assertRaises(power.PowerError):
                self.t.schedule(a, m)


class ModeRevertTest(unittest.TestCase):
    def setUp(self):
        self.applied = []
        self._orig = (display._apply, display.outputs, display.REVERT_AFTER)
        display._apply = lambda out, mid: self.applied.append((out, mid))
        display.outputs = lambda: [{"name": "DISPLAY1", "current": {"id": "3440x1440@160"},
                                    "modes": [{"id": "3440x1440@160", "w": 3440, "h": 1440, "hz": 160},
                                              {"id": "3440x1440@60", "w": 3440, "h": 1440, "hz": 60}]}]
        display.REVERT_AFTER = 0.3
        self.m = display.ModeChanger()

    def tearDown(self):
        display._apply, display.outputs, display.REVERT_AFTER = self._orig

    def test_auto_revert(self):
        self.m.apply("DISPLAY1", "3440x1440@60")
        time.sleep(0.6)
        self.assertEqual(self.applied, [("DISPLAY1", "3440x1440@60"), ("DISPLAY1", "3440x1440@160")])
        self.assertIsNone(self.m.status())

    def test_confirm_keeps(self):
        self.m.apply("DISPLAY1", "3440x1440@60")
        self.m.confirm()
        time.sleep(0.6)
        self.assertEqual(self.applied, [("DISPLAY1", "3440x1440@60")])

    def test_rejects_unknown_mode_and_injection(self):
        for out, mode in (("DISPLAY1", "1x1@1"), ("DISPLAY1;x", "3440x1440@60"), ("..\\x", "3440x1440@60"),
                          ("DISPLAY1", "3440x1440@60 --x")):
            with self.assertRaises(display.DisplayError):
                self.m.apply(out, mode)


class AlertsTest(unittest.TestCase):
    def setUp(self):
        d = Path(tempfile.mkdtemp())
        self._orig = (al.CONF_FILE, al.LOG_FILE)
        al.CONF_FILE, al.LOG_FILE = d / "a.json", d / "l.json"
        ctx = Ctx()
        ctx.data, ctx.proctrack, ctx.netmon, ctx.updates = {}, None, None, None
        self.ctx = ctx
        self.a = al.Alerts(ctx)
        self.a._deliver = lambda a, pc=True: None

    def tearDown(self):
        al.CONF_FILE, al.LOG_FILE = self._orig

    def test_disk_rule_and_cooldown(self):
        self.ctx.data = {"disks": {"partitions": [{"mount": "C:\\", "used": 95, "total": 100}]}}
        self.a._check(heavy=False)
        self.a._check(heavy=False)
        self.assertEqual(self.a.unread(), 1)

    def test_disabled_rule(self):
        self.a.update_config({"rules": {"disk_full": {"enabled": False}}})
        self.ctx.data = {"disks": {"partitions": [{"mount": "C:\\", "used": 95, "total": 100}]}}
        self.a._check(heavy=False)
        self.assertEqual(self.a.unread(), 0)

    def test_temperature_must_be_sustained(self):
        self.ctx.data = {"cpu": {"temp": 95}}
        self.a._check(heavy=False)
        self.assertEqual(self.a.unread(), 0)
        self.a._since["cpu_temp"] -= 31
        self.a._check(heavy=False)
        self.assertEqual(self.a.unread(), 1)

    def test_threshold_config(self):
        self.a.update_config({"rules": {"cpu_temp": {"value": 70}}})
        self.assertEqual(self.a._val("cpu_temp"), 70)
        with self.assertRaises(ValueError):
            self.a.update_config({"channels": {"ntfy": "ftp://x"}})


class GameTest(unittest.TestCase):
    def setUp(self):
        ctx = Ctx()
        ctx.processes, ctx.data = [], {}
        self.ctx = ctx
        self._orig = (game.running_steam_appid, game.STATE_FILE)
        game.running_steam_appid = lambda: None
        game.STATE_FILE = Path(tempfile.mkdtemp()) / "game.json"
        self.g = game.GameMonitor(ctx)
        self.g.enabled = True

    def tearDown(self):
        game.running_steam_appid, game.STATE_FILE = self._orig

    def frames(self, pid, name, fps, secs=2.0):
        now, ft = time.time(), 1000 / fps
        n = int(secs * fps)
        self.g.frames[pid] = deque((now - (n - i) * ft / 1000, ft) for i in range(n))
        self.g.names[pid] = name

    def test_picks_foreground_then_busiest(self):
        self.frames(10, "chrome.exe", 60)
        self.frames(20, "Game.exe", 140)
        st = self.g.status()
        self.assertEqual(st["game"]["pid"], 20)
        self.assertAlmostEqual(st["fps"], 140, delta=1)
        self.ctx.data = {"focus": {"window": {"pid": 10, "desktop": "Google Chrome"}}}
        self.assertEqual(self.g.status()["game"]["name"], "Google Chrome")

    def test_ignores_non_games_without_focus(self):
        self.frames(10, "dwm.exe", 144)
        st = self.g.status()
        self.assertFalse(st["live"])
        self.assertIsNone(st["game"])

    def test_requires_presentmon(self):
        self.g.exe = None
        orig = game.find_presentmon
        game.find_presentmon = lambda *_: None
        try:
            with self.assertRaises(RuntimeError):
                self.g.set_enabled(True)
        finally:
            game.find_presentmon = orig


class AudioTest(unittest.TestCase):
    def test_kind(self):
        dev = lambda **p: type("D", (), {"properties": p})()   # noqa: E731
        self.assertEqual(audio._kind(dev(**{audio.PK_FRIENDLY: "LG ULTRAGEAR (NVIDIA High Definition Audio)"})), "hdmi")
        self.assertEqual(audio._kind(dev(**{audio.PK_FRIENDLY: "Fones de ouvido (HyperX Cloud Alpha)", audio.PK_FORMFACTOR: 3})), "headset")
        self.assertEqual(audio._kind(dev(**{audio.PK_FRIENDLY: "Fone", audio.PK_ENUMERATOR: "BTHENUM"})), "bluetooth")
        self.assertEqual(audio._kind(dev(**{audio.PK_FRIENDLY: "Alto-falantes", audio.PK_FORMFACTOR: 1})), "speaker")

    def test_rejects_bad_input(self):
        for body in ({"op": "volume", "kind": "sink", "name": "x; del *", "volume": 10},
                     {"op": "volume", "kind": "sink", "name": "{0.0.0.00000000}.{abc}", "volume": 500},
                     {"op": "move", "index": 1, "sink": "x"},
                     {"op": "nope"}):
            orig = audio._find_dev
            audio._find_dev = lambda *_: (_ for _ in ()).throw(AssertionError("não deveria procurar o dispositivo"))
            try:
                with self.assertRaises(audio.AudioError, msg=body):
                    if body.get("volume") == 500:
                        audio._pct(body["volume"])
                    else:
                        audio.action(body)
            finally:
                audio._find_dev = orig


class ActionsTest(unittest.TestCase):
    def test_protected(self):
        for pid in (0, 4, os.getpid()):
            with self.assertRaises(actions.ActionError):
                actions.signal_process(pid, "KILL", None)
        with self.assertRaises(actions.ActionError):
            actions.signal_process(1234, "HUP", None)

    def test_critical_process(self):
        import psutil
        csrss = next((p.pid for p in psutil.process_iter(["name"]) if p.info["name"] == "csrss.exe"), None)
        if csrss is None:
            self.skipTest("csrss.exe não encontrado")
        with self.assertRaises(actions.ActionError) as e:
            actions.signal_process(csrss, "KILL", None)
        self.assertEqual(e.exception.status, 403)


class UpdatesTest(unittest.TestCase):
    WINGET = ("   - \r   \\ \r"
              "Nome                       ID                     Versão       Disponível   Origem\n"
              "------------------------------------------------------------------------------------\n"
              "AnyDesk                    AnyDesk.AnyDesk        ad 9.8.0     9.8.3        winget\n"
              "Python Launcher            Python.Launcher        < 3.14.7     3.14.7       winget\n"
              "NVIDIA App                 Nvidia.NvidiaApp       11.0.1       11.0.2       winget\n"
              "3 atualizações disponíveis.\n")

    def test_parse_winget(self):
        items = updates.parse_winget(self.WINGET)
        self.assertEqual([i["id"] for i in items], ["AnyDesk.AnyDesk", "Python.Launcher", "Nvidia.NvidiaApp"])
        self.assertEqual((items[1]["current"], items[1]["new"]), ("< 3.14.7", "3.14.7"))
        self.assertEqual(items[2]["sensitive"], "driver")
        self.assertEqual(updates.parse_winget("Nenhuma atualização encontrada."), [])


if __name__ == "__main__":
    unittest.main()
