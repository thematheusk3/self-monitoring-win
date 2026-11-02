"""Downloads da Steam, mídia, sensores, captura de tela e apps: partes sem dependência do estado do PC."""
from __future__ import annotations

import struct
import tempfile
import unittest
import zlib
from pathlib import Path

from tests import helpers  # noqa: F401  (ajusta sys.path)
from sm import desktop, media, screenshot, steamdl, win
from sm.hw import lhm


class SteamLogTest(unittest.TestCase):
    def test_states(self):
        apps = {}
        for line in [
            "[x] AppID 5 state changed : Update Required,Fully Installed,Update Queued,Update Running,Update Started,",
            "[x] AppID 5 update started : download 100/2000, store 0/0, reuse 0/1, delta 0/0, stage 0/5000 ",
            "[x] AppID 5 update changed : Running Update,Downloading,Staging,",
            "[x] AppID 6 state changed : Update Required,Fully Installed,Update Queued,Update Started, (Suspended)",
            "[x] AppID 7 state changed : Update Required,Update Queued, (Update delayed for 30 secs)",
            "[x] AppID 8 state changed : Update Required,Update Queued,",
            "[x] AppID 9 state changed : Fully Installed,",
        ]:
            steamdl.parse_log_line(line, apps)
        st = {k: steamdl.status_of(v["flags"]) for k, v in apps.items()}
        self.assertEqual(st, {"5": "running", "6": "paused", "7": None, "8": "queued", "9": None})
        self.assertEqual((apps["5"]["dl_total"], apps["5"]["stage_total"], apps["5"]["phase"]), (2000, 5000, "baixando"))

    def test_acf(self):
        acf = '"AppState"\n{\n\t"appid"\t\t"5"\n\t"name"\t\t"Jogo X"\n\t"StateFlags"\t\t"1026"\n' \
              '\t"BytesToDownload"\t\t"1000"\n\t"BytesDownloaded"\t\t"250"\n}'
        d = steamdl.parse_acf(acf)
        self.assertEqual((d["name"], d["StateFlags"], d["BytesToDownload"], d["BytesDownloaded"]), ("Jogo X", 1026, 1000, 250))

    def test_steam_cache_icon(self):
        root = Path(tempfile.mkdtemp()) / "Steam"
        cache = root / "appcache/librarycache/123"
        cache.mkdir(parents=True)
        (cache / "library_600x900.jpg").write_bytes(b"x")
        (cache / ("a" * 40 + ".jpg")).write_bytes(b"x")
        orig, desktop.STEAM_ROOTS = desktop.STEAM_ROOTS, [root]
        try:
            self.assertEqual(desktop.steam_cache_icon("123").name, "a" * 40 + ".jpg")
            self.assertIsNone(desktop.steam_cache_icon("999"))
        finally:
            desktop.STEAM_ROOTS = orig


class AppsTest(unittest.TestCase):
    def test_category(self):
        self.assertEqual(desktop.category("steam://rungameid/570940", "Dark Souls"), "Jogos")
        self.assertEqual(desktop.category(r"{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\notepad.exe", "Bloco"), "Windows")
        self.assertEqual(desktop.category("Microsoft.WindowsNotepad_8wekyb3d8bbwe!App", "Bloco de notas"), "Windows")
        self.assertEqual(desktop.category("SpotifyAB.SpotifyMusic_zpdnekdrzrea0!Spotify", "Spotify"), "Loja")
        self.assertEqual(desktop.category(r"{6D809377-6AF0-444B-8957-A3773F02200E}\Bambu\b.exe", "Bambu"), "Programas")
        self.assertTrue(desktop.HIDE.search("Desinstalar Programa X"))
        self.assertFalse(desktop.HIDE.search("Steam"))


class MediaTest(unittest.TestCase):
    def _media(self):
        m = media.Media.__new__(media.Media)     # sem falar com o Windows
        m.players, m.last_playing, m.arts = [], {}, {}
        m.manager = type("M", (), {"get_sessions": lambda self: []})()
        return m

    def test_app_name(self):
        self.assertEqual(media.app_name("Spotify.exe"), "Spotify")
        self.assertEqual(media.app_name("308046B0AF4A39CB"), "Firefox")
        self.assertEqual(media.app_name("Microsoft.ZuneMusic_8wekyb3d8bbwe!Microsoft.ZuneMusic"), "Media Player")
        self.assertEqual(media.app_name("vlc.exe"), "VLC")

    def test_control_validates(self):
        m = self._media()
        with self.assertRaises(media.MediaError):      # player que não está na lista
            m.control("../../etc", "next")
        m.players = [{"id": "vlc", "can": {"seek": True}, "position": 10, "length": 100},
                     {"id": "radio", "can": {"seek": False}}]
        with self.assertRaises(media.MediaError):      # sessão não existe mais
            m.control("vlc", "rm -rf")


class SensorsTest(unittest.TestCase):
    TREE = {"id": 0, "Text": "Sensor", "Children": [{"id": 1, "Text": "PC", "ImageURL": "images_icon/computer.png", "Children": [
        {"id": 2, "Text": "AMD Ryzen 7 9800X3D", "ImageURL": "images_icon/cpu.png", "Children": [
            {"id": 3, "Text": "Temperatures", "Children": [
                {"id": 4, "Text": "Core (Tctl/Tdie)", "Value": "65,4 °C", "SensorId": "/amdcpu/0/temperature/2", "Children": []}]},
            {"id": 5, "Text": "Load", "Children": [{"id": 6, "Text": "CPU Total", "Value": "5.0 %", "Children": []}]}]},
        {"id": 7, "Text": "MSI PRO X870-P", "ImageURL": "images_icon/mainboard.png", "Children": [
            {"id": 8, "Text": "Nuvoton NCT6687D", "ImageURL": "images_icon/chip.png", "Children": [
                {"id": 9, "Text": "Fans", "Children": [
                    {"id": 10, "Text": "CPU Fan", "Value": "1200 RPM", "SensorId": "/lpc/nct6687d/0/fan/0", "Children": []}]},
                {"id": 11, "Text": "Voltages", "Children": [
                    {"id": 12, "Text": "Vcore", "Value": "99999 V", "SensorId": "/lpc/nct6687d/0/voltage/0", "Children": []}]}]}]},
        {"id": 13, "Text": "AMD Radeon(TM) Graphics", "ImageURL": "images_icon/ati.png", "Children": [
            {"id": 14, "Text": "Temperatures", "Children": [
                {"id": 15, "Text": "GPU Core", "Value": "48 °C", "SensorId": "/gpu-amd/0/temperature/0", "Children": []}]}]}]}]}

    def test_parse(self):
        rs = lhm.parse(self.TREE)
        got = {(r.chip, r.chip_id, r.kind, r.label, r.value) for r in rs}
        self.assertIn(("amdcpu", "/amdcpu/0", "temp", "Core (Tctl/Tdie)", 65.4), got)   # vírgula decimal
        self.assertIn(("lpc", "/lpc/nct6687d/0", "fan", "CPU Fan", 1200.0), got)
        self.assertFalse(any(r.kind == "in" for r in rs))            # valor absurdo descartado
        self.assertFalse(any(r.label == "CPU Total" for r in rs))    # grupo "Load" não entra

    def test_cpu_and_gpu_temp(self):
        reader = lhm.LHM("http://x")
        reader._cache = (float("inf"), lhm.parse(self.TREE))
        self.assertEqual(reader.cpu_temp(), (65.4, "LHM/Core (Tctl/Tdie)"))
        self.assertEqual(reader.gpu_temps("AMD Radeon(TM) Graphics"), {"GPU Core": 48.0})
        self.assertEqual(reader.gpu_temps("NVIDIA GeForce RTX 5070 Ti"), {})


class ScreenshotTest(unittest.TestCase):
    def test_png_complete(self):
        d = Path(tempfile.mkdtemp())
        full = b"\x89PNG\r\n\x1a\n" + b"x" * 100 + b"\x00\x00\x00\x00IEND\xaeB`\x82"
        (d / "a.png").write_bytes(full)
        (d / "b.png").write_bytes(full[:60])
        self.assertTrue(screenshot.png_complete(d / "a.png"))
        self.assertFalse(screenshot.png_complete(d / "b.png"))
        self.assertFalse(screenshot.png_complete(d / "nao-existe.png"))

    def test_names(self):
        self.assertIsNotNone(screenshot._NAME.match("shot-20261101-232608.png"))
        self.assertIsNotNone(screenshot._NAME.match("shot-20261101-232608-2.png"))
        self.assertIsNone(screenshot._NAME.match("..\\shot-20261101-232608.png"))

    def test_png_encoder(self):
        bgra = bytes([255, 0, 0, 255, 0, 255, 0, 128])          # azul opaco, verde semitransparente
        png = win.png_from_bgra(bgra, 2, 1, alpha=True)
        idat = png[png.index(b"IDAT") + 4:png.index(b"IEND") - 8]
        self.assertEqual(zlib.decompress(idat), b"\x00" + bytes([0, 0, 255, 255, 0, 255, 0, 128]))
        w, h = struct.unpack(">II", png[16:24])
        self.assertEqual((w, h), (2, 1))
        self.assertTrue(png.endswith(screenshot.PNG_END))


if __name__ == "__main__":
    unittest.main()
