"""Incidentes, regras do log de eventos e histórico persistente (trazidos da versão Linux)."""
import tempfile
import time
import unittest
from pathlib import Path

import tests.helpers  # noqa: F401
from sm import incidents, rules
from sm.store import Store


def ev(provider, eid, prio=3, msg="", data=None, ts=None):
    return {"provider": provider, "ident": provider, "event_id": eid, "prio": prio, "msg": msg, "data": data or {},
            "ts": ts or time.time(), "cursor": "System|1|1.0", "channel": "System", "pid": 4}


class RulesTest(unittest.TestCase):
    def setUp(self):
        self._boot = rules._boot_age
        rules._boot_age = lambda ts: 1e9          # longe do início do Windows

    def tearDown(self):
        rules._boot_age = self._boot

    def test_app_crash(self):
        e = rules.classify(ev("Application Error", 1000, data={
            "AppName": "eldenring.exe", "AppVersion": "2.7.2.0", "ModuleName": "ntdll.dll", "ExceptionCode": "c0000005",
            "ProcessId": "0x13b4", "AppPath": r"C:\Steam\steamapps\common\ELDEN RING\Game\eldenring.exe"}))
        self.assertEqual((e["kind"], e["family"], e["trigger"], e["pid"]), ("coredump", "crash", True, 0x13b4))
        self.assertIn("acesso inválido à memória", e["message"])
        self.assertEqual(e["data"]["module"], "ntdll.dll")

    def test_explorer_is_session(self):
        e = rules.classify(ev("Application Error", 1000, msg="explorer.exe", data={"AppName": "explorer.exe"}))
        self.assertEqual(e["kind"], "compositor")

    def test_gpu(self):
        self.assertEqual(rules.classify(ev("Display", 4101, prio=4))["kind"], "gpu_reset")
        self.assertEqual(rules.classify(ev("nvlddmkm", 153))["kind"], "gpu_reset")
        self.assertEqual(rules.classify(ev("nvlddmkm", 13))["kind"], "gpu_hang")
        self.assertIsNone(rules.classify(ev("nvlddmkm", 999, prio=6)))

    def test_old_live_kernel_report_does_not_trigger(self):
        files = r"\\?\C:\WINDOWS\LiveKernelReports\WATCHDOG\WATCHDOG-20260917-1816.dmp"
        data = {"EventName": "LiveKernelEvent", "P1": "141", "AttachedFiles": files}
        old = rules.classify(ev("Windows Error Reporting", 1001, prio=6, data=data))
        self.assertEqual(old["kind"], "gpu_hang")
        self.assertFalse(old["trigger"])
        self.assertTrue(old["data"]["old_report"])
        when = rules.dump_time(files)
        fresh = rules.classify(ev("Windows Error Reporting", 1001, prio=6, data=data, ts=when + 30))
        self.assertTrue(fresh["trigger"])

    def test_others(self):
        self.assertEqual(rules.classify(ev("Microsoft-Windows-WER-SystemErrorReporting", 1001, prio=2))["kind"], "bugcheck")
        self.assertFalse(rules.classify(ev("Microsoft-Windows-Kernel-Power", 41, prio=2))["trigger"])
        self.assertEqual(rules.classify(ev("Ntfs", 55))["family"], "storage")
        self.assertEqual(rules.classify(ev("Microsoft-Windows-WHEA-Logger", 18, prio=2))["kind"], "mce")
        self.assertEqual(rules.classify(ev("Microsoft-Windows-Resource-Exhaustion-Detector", 2004, prio=4))["kind"], "oom")
        s = rules.classify(ev("Service Control Manager", 7031, data={"param1": "Serviço Inexistente XYZ"}))
        self.assertEqual((s["kind"], s["trigger"], s["data"]["result"]), ("unit_failed", True, "crash"))
        self.assertEqual(rules.classify(ev("Qualquer", 1, prio=3))["kind"], "error")
        self.assertIsNone(rules.classify(ev("Qualquer", 1, prio=4)))

    def test_boot_noise(self):
        rules._boot_age = lambda ts: 30
        self.assertFalse(rules.classify(ev("nvlddmkm", 153))["trigger"])
        self.assertTrue(rules.classify(ev("Ntfs", 55))["trigger"])      # disco abre incidente mesmo no boot


class HypothesesTest(unittest.TestCase):
    def facts(self, **kw):
        f = {"t": 1000.0, "kind": "coredump", "family": "crash", "kinds": {}, "near_gpu": [], "gpu_fence": [],
             "crash": None, "signal": None, "is_game": False, "mod_launcher": False, "module": None, "code": None,
             "bugcheck": [], "temps": {}, "mem": None, "gtt": None, "vram": None, "swap_total": 1, "oom": [],
             "storage": [], "mce": [], "aer": [], "kernel_bug": [], "thermal": [], "unit": None, "unit_result": None,
             "subject": None, "files": [], "panel": [], "packages": [], "hardware": [], "metrics_available": True,
             "tail": None}
        f.update(kw)
        return f

    def test_gpu_crash_beats_app(self):
        crash = {"ts": 1002.0, "data": {"comm": "eldenring.exe", "meaning": "acesso inválido à memória"}}
        h = incidents.hypotheses(self.facts(crash=crash, signal="0xc0000005", module="nvwgf2umx.dll",
                                            near_gpu=[{"ts": 999.0, "kind": "gpu_reset", "message": "TDR"}]))
        self.assertEqual(h[0]["id"], "gpu")
        app = next(x for x in h if x["id"] == "app")
        self.assertTrue(any("driver de vídeo" in e for e in app["evidence"]))

    def test_bsod_and_shutdown(self):
        h = incidents.hypotheses(self.facts(kind="unexpected_shutdown", bugcheck=[{"ts": 1000.0, "message": "código 0x0000009f"}],
                                            tail=[]))
        self.assertEqual(h[0]["id"], "bsod")
        h = incidents.hypotheses(self.facts(kind="unexpected_shutdown", tail=[]))
        self.assertEqual(h[0]["id"], "shutdown_power")

    def test_service(self):
        unit = {"kind": "unit_failed"}
        h = incidents.hypotheses(self.facts(kind="unit_failed", unit=unit, unit_result="crash",
                                            subject={"unit_info": {"ExitCode": 1066, "ServiceSpecificExitCode": 5}}))
        self.assertEqual(h[0]["id"], "unit")
        self.assertIn("Código de saída: 5", h[0]["evidence"])


class StoreTest(unittest.TestCase):
    def test_roundtrip(self):
        st = Store("m1", Path(tempfile.mkdtemp()) / "s.db")
        e = {"ts": 10.0, "kind": "coredump", "family": "crash", "severity": "error", "message": "x", "data": {"a": 1}}
        eid = st.add_event(e)
        self.assertEqual(st.add_event(dict(e, ts=11.0)), eid)          # repetição vira count
        self.assertEqual(st.events(since=0)[0]["count"], 2)
        iid = st.add_incident({"uid": "u1", "opened": 1.0, "trigger_ts": 10.0, "family": "crash", "kind": "coredump",
                               "severity": "error", "status": "open", "title": "t"})
        st.update_incident(iid, report={"ok": 1}, status="resolved")
        self.assertEqual((st.incident(iid)["status"], st.incident(iid)["report"]), ("resolved", {"ok": 1}))
        st.set_meta("eventlog_System", "42")
        self.assertEqual(st.get_meta("eventlog_System"), "42")


if __name__ == "__main__":
    unittest.main()
