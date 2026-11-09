"""Aba Serviços (serviços do Windows) e aba Logs (log de eventos), trazidas da versão Linux."""
import unittest
from unittest import mock

import tests.helpers  # noqa: F401
from sm import eventlog, services

XML = """<Event xmlns='http://schemas.microsoft.com/win/2004/08/events/event'><System>
<Provider Name='Microsoft-Windows-Kernel-Power' Guid='{x}'/><EventID>41</EventID><Version>8</Version><Level>1</Level>
<Task>63</Task><Opcode>0</Opcode><Keywords>0x8000400000000002</Keywords>
<TimeCreated SystemTime='2026-11-09T12:30:05.1234567Z'/><EventRecordID>9876</EventRecordID>
<Execution ProcessID='4' ThreadID='8'/><Channel>System</Channel><Computer>PC</Computer></System>
<EventData><Data Name='BugcheckCode'>0</Data><Data Name='PowerButtonTimestamp'>0</Data></EventData></Event>"""


class EventLogParseTest(unittest.TestCase):
    def test_parse_and_normalize(self):
        raw = eventlog.parse_xml(XML)
        self.assertEqual((raw["Provider"], raw["EventID"], raw["Channel"]), ("Microsoft-Windows-Kernel-Power", "41", "System"))
        self.assertEqual(raw["Data"]["BugcheckCode"], "0")
        m = eventlog.normalize(raw, "O sistema foi reinicializado sem ser desligado corretamente.")
        self.assertEqual((m["prio"], m["event_id"], m["record"], m["pid"]), (2, 41, 9876, 4))
        self.assertAlmostEqual(m["ts"], 1794227405.123456, places=3)
        self.assertTrue(eventlog.RX_CURSOR.fullmatch(m["cursor"]))
        self.assertEqual(eventlog._cursor_key(m["cursor"]), (m["ts"], "System", 9876))

    def test_message_fallback(self):
        raw = eventlog.parse_xml(XML)
        self.assertIn("BugcheckCode", eventlog.normalize(raw)["msg"] + "BugcheckCode")
        self.assertEqual(eventlog.normalize({"EventID": "7", "Data": {}})["msg"], "Evento 7")

    def test_rejects_bad_input(self):
        for c in ("x", "Security|1|2", "System|1|2' or 1=1"):
            with self.assertRaises(ValueError):
                eventlog._cursor_key(c)
        with self.assertRaises(ValueError):
            eventlog.query({"unit": "x' or '1'='1"})

    def test_query_merges_channels_and_pages(self):
        def fake(ch, xp, limit, reverse=True, with_msg=True):
            base = 100.0 if ch == "System" else 100.5
            return [eventlog.normalize({"Channel": ch, "EventRecordID": str(i), "Level": "2", "EventID": "1",
                                        "TimeCreated": eventlog._ts_iso(1.8e9 + base + i), "Data": {"a": f"{ch} {i}"}})
                    for i in range(5)]
        with mock.patch.object(eventlog, "_read", fake):
            r = eventlog.query({"limit": 4})
            self.assertEqual([round(m["ts"] - 1.8e9, 1) for m in r["items"]], [103.0, 103.5, 104.0, 104.5])
            self.assertTrue(r["more"])
            older = eventlog.query({"limit": 3, "before": r["items"][0]["cursor"]})
            self.assertEqual([round(m["ts"] - 1.8e9, 1) for m in older["items"]], [101.5, 102.0, 102.5])
            newer = eventlog.query({"limit": 10, "after": r["items"][1]["cursor"]})
            self.assertEqual([round(m["ts"] - 1.8e9, 1) for m in newer["items"]], [104.0, 104.5])
            self.assertEqual(len(eventlog.query({"grep": "Application 3"})["items"]), 1)


class ServicesTest(unittest.TestCase):
    SVCS = [
        {"name": "Audiosrv", "display": "Áudio", "type": 0x10, "state": 4, "pid": 50, "exit": 0},
        {"name": "Dhcp", "display": "DHCP", "type": 0x20, "state": 4, "pid": 60, "exit": 0},
        {"name": "Dnscache", "display": "DNS", "type": 0x20, "state": 4, "pid": 60, "exit": 0},
        {"name": "Quebrado", "display": "Quebrado", "type": 0x10, "state": 1, "pid": None, "exit": 1067},
        {"name": "Parado", "display": "Parado", "type": 0x10, "state": 1, "pid": None, "exit": 1077},
        {"name": "CDPUserSvc", "display": "CDP", "type": 0x60, "state": 1, "pid": None, "exit": 0},
        {"name": "CDPUserSvc_1a2b", "display": "CDP_1a2b", "type": 0xE0, "state": 4, "pid": 70, "exit": 0},
    ]

    def test_list(self):
        procs = [{"pid": 60, "mem": 1000, "cpu": 1.5, "threads": 9, "create_time": 5.0}]
        with mock.patch.object(services, "enum_services", return_value=self.SVCS), \
                mock.patch.object(services, "_start_types", return_value={"Quebrado": "automatic"}):
            r = services.list_units(processes=procs)
            by = {u["unit"]: u for u in r["units"]}
            self.assertEqual(set(by), {"Audiosrv", "Dhcp", "Dnscache", "Quebrado", "Parado"})
            self.assertEqual((by["Quebrado"]["active"], by["Quebrado"]["exit"]), ("failed", 1067))
            self.assertEqual(by["Parado"]["sub"], "dead")
            self.assertEqual((by["Dhcp"]["mem"], by["Dhcp"]["shared"], by["Dhcp"]["tasks"]), (1000, 2, 9))
            self.assertEqual(r["counts"], {"total": 5, "running": 3, "failed": 1})
            u = services.list_units(user=True, processes=procs)
            self.assertEqual([x["unit"] for x in u["units"]], ["CDPUserSvc_1a2b"])

    def test_action_validation(self):
        with self.assertRaises(ValueError):
            services.action("a b;rm", "stop")
        with self.assertRaises(ValueError):
            services.action("Dhcp", "delete")
        with self.assertRaises(PermissionError):
            services.action("RpcSs", "stop")
        with self.assertRaises(PermissionError):
            services.action("WinDefend", "restart")

    def test_real_enum(self):
        svcs = services.enum_services()
        self.assertTrue(any(s["name"].lower() == "eventlog" for s in svcs))


if __name__ == "__main__":
    unittest.main()
