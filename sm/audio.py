"""Áudio do Windows (Core Audio via pycaw/comtypes): saídas, entradas, volume, mudo, áudio por aplicativo.

Saída por aplicativo (ex.: Chrome no monitor, Spotify no fone): é o "Dispositivo de saída" do Mixer de
volume do Windows. Não há API pública; o Windows usa a interface interna IAudioPolicyConfigFactory
(a mesma do EarTrumpet e do SoundVolumeView), que só existe a partir do Windows 10 1803 e mudou de
identificador no 21H2. Se não estiver disponível, a opção some do painel. A escolha fica gravada pelo
Windows para o executável (vale nas próximas vezes que o app abrir) e "Padrão" desfaz.

O volume vai até 100% (o Windows não amplifica).
"""
from __future__ import annotations

import ctypes
import hashlib
import re
import warnings

import psutil

try:
    import comtypes
    from pycaw.api.audioclient import ISimpleAudioVolume
    from pycaw.api.audiopolicy import IAudioSessionControl2
    from pycaw.constants import CLSID_MMDeviceEnumerator, EDataFlow, ERole
    from pycaw.pycaw import IMMDeviceEnumerator
    from pycaw.utils import AudioUtilities
except ImportError:  # pragma: no cover
    comtypes = None

DEVICE_STATE_ACTIVE = 1
PK_FRIENDLY = "{A45C254E-DF1C-4EFD-8020-67D146A850E0} 14"
PK_DESC = "{A45C254E-DF1C-4EFD-8020-67D146A850E0} 2"
PK_ENUMERATOR = "{A45C254E-DF1C-4EFD-8020-67D146A850E0} 24"
PK_FORMFACTOR = "{1DA5D803-D492-4EDD-8C23-E0C0FFEE7F0E} 0"
# AudioEndpoint FormFactor: 1 alto-falantes, 3 fones, 5 headset, 9 HDMI/DisplayPort...
FORM = {3: "headset", 5: "headset", 6: "headset", 9: "hdmi"}
SESSION_EXPIRED = 2


class AudioError(Exception):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


# ---------- saída por aplicativo (interface interna do Windows) ----------
POLICY_CLASS = "Windows.Media.Internal.AudioPolicyConfig"
POLICY_IIDS = ("ab3d4648-e242-459f-b02f-541c70306324",    # Windows 10 21H2+ e Windows 11
               "2a59116d-6c4f-45e0-a74f-707e3fef9258")    # Windows 10 1803 a 21H1
DEVINTERFACE_RENDER = "{e6327cad-dcec-4949-ae8a-991e976a79d2}"
# posição dos métodos na vtable: 6 do IInspectable + 19 internos que não usamos
SET_PERSISTED, GET_PERSISTED = 25, 26
ROLES = (0, 1)    # eConsole, eMultimedia (o que o Mixer de volume do Windows altera)


class _Routing:
    """Precisa ser criado dentro de um _Com() (o COM da thread já iniciado)."""

    def __init__(self) -> None:
        from .hw.gpu import _guid
        self.cb = ctypes.WinDLL("combase.dll")
        self.cb.WindowsGetStringRawBuffer.restype = ctypes.c_void_p
        self.factory = None
        hs = self._hstring(POLICY_CLASS)
        try:
            for iid in POLICY_IIDS:
                f = ctypes.c_void_p()
                if self.cb.RoGetActivationFactory(hs, ctypes.byref(_guid(iid)), ctypes.byref(f)) == 0:
                    self.factory = f
                    break
        finally:
            self.cb.WindowsDeleteString(hs)

    def _hstring(self, s: str) -> ctypes.c_void_p:
        hs = ctypes.c_void_p()
        self.cb.WindowsCreateString(s, len(s), ctypes.byref(hs))
        return hs

    def get(self, pid: int) -> str | None:
        """Saída escolhida para o processo (id do dispositivo) ou None = segue o padrão do Windows."""
        from .hw.gpu import _vcall
        out = ctypes.c_void_p()
        if _vcall(self.factory, GET_PERSISTED, ctypes.c_long, ctypes.c_uint(pid), ctypes.c_int(0),
                  ctypes.c_int(1), ctypes.byref(out)) or not out.value:
            return None
        try:
            raw = ctypes.wstring_at(self.cb.WindowsGetStringRawBuffer(out, None))
        finally:
            self.cb.WindowsDeleteString(out)
        # \\?\SWD#MMDEVAPI#{0.0.0.00000000}.{guid}#{e6327cad-...}
        m = re.search(r"MMDEVAPI#(\{[^}]+\}\.\{[^}]+\})", raw)
        return m.group(1) if m else None

    def set(self, pid: int, dev_id: str | None) -> None:
        from .hw.gpu import _vcall
        hs = self._hstring(f"\\\\?\\SWD#MMDEVAPI#{dev_id}#{DEVINTERFACE_RENDER}") if dev_id else ctypes.c_void_p()
        try:
            for role in ROLES:
                hr = _vcall(self.factory, SET_PERSISTED, ctypes.c_long, ctypes.c_uint(pid), ctypes.c_int(0),
                            ctypes.c_int(role), hs)
                if hr:
                    raise AudioError(f"O Windows recusou a troca de saída (0x{hr & 0xFFFFFFFF:08X}).", 500)
        finally:
            if dev_id:
                self.cb.WindowsDeleteString(hs)

    def close(self) -> None:
        from .hw.gpu import _vcall
        if self.factory:
            _vcall(self.factory, 2, ctypes.c_ulong)    # Release
            self.factory = None


def _routing() -> _Routing | None:
    try:
        r = _Routing()
    except OSError:
        return None
    return r if r.factory else None


class _Com:
    """with _Com(): ... inicia o COM na thread atual (cada thread do servidor HTTP precisa)."""

    def __enter__(self):
        if comtypes is None:
            raise AudioError("pycaw/comtypes não instalados (rode o instalador de novo).", 501)
        comtypes.CoInitialize()
        return self

    def __exit__(self, *exc):
        comtypes.CoUninitialize()


def _enum():
    return comtypes.CoCreateInstance(CLSID_MMDeviceEnumerator, IMMDeviceEnumerator, comtypes.CLSCTX_INPROC_SERVER)


def _devices(flow: int) -> list:
    coll = _enum().EnumAudioEndpoints(flow, DEVICE_STATE_ACTIVE)
    out = []
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")      # propriedades ilegíveis de alguns drivers só geram aviso
        for i in range(coll.GetCount()):
            out.append(AudioUtilities.CreateDevice(coll.Item(i)))
    return out


def _default_id(flow: int) -> str | None:
    try:
        return _enum().GetDefaultAudioEndpoint(flow, ERole.eConsole.value).GetId()
    except (OSError, comtypes.COMError):
        return None


def _kind(dev) -> str:
    p = dev.properties
    name = f"{p.get(PK_FRIENDLY) or ''} {p.get(PK_DESC) or ''}".lower()
    bus = str(p.get(PK_ENUMERATOR) or "").upper()
    ff = p.get(PK_FORMFACTOR)
    if ff == 9 or "hdmi" in name or "displayport" in name or "nvidia high definition" in name:
        return "hdmi"
    if bus.startswith("BTH") or "bluetooth" in name:
        return "bluetooth"
    if ff in FORM or re.search(r"headset|headphone|fone|cloud", name):
        return FORM.get(ff, "headset")
    if bus == "USB":
        return "usb"
    return "speaker"


def _dev(d, default: str | None) -> dict:
    vol = d.EndpointVolume
    return {"name": d.id, "index": d.id, "description": d.FriendlyName or d.id,
            "volume": round(vol.GetMasterVolumeLevelScalar() * 100), "mute": bool(vol.GetMute()),
            "state": "RUNNING", "default": d.id == default, "kind": _kind(d), "port": None}


def _session_key(dev_id: str, pid: int) -> int:
    """Identificador numérico estável do grupo (dispositivo, processo) para a API."""
    return int(hashlib.sha1(f"{dev_id}|{pid}".encode()).hexdigest()[:8], 16)


def _sessions(dev) -> dict[int, dict]:
    """Sessões de áudio de um dispositivo agrupadas por processo (como o Mixer de volume)."""
    groups: dict[int, dict] = {}
    try:
        en = dev.AudioSessionManager.GetSessionEnumerator()
    except (OSError, comtypes.COMError):
        return groups
    for i in range(en.GetCount()):
        ctl = en.GetSession(i)
        if ctl is None:
            continue
        c2 = ctl.QueryInterface(IAudioSessionControl2)
        state = c2.GetState()
        if state == SESSION_EXPIRED:
            continue
        pid = c2.GetProcessId()
        key = _session_key(dev.id, pid)
        g = groups.setdefault(key, {"key": key, "pid": pid, "ctls": [], "active": False, "display": c2.GetDisplayName()})
        g["ctls"].append(c2)
        g["active"] |= state == 1
    return groups


def _app_name(pid: int, display: str) -> tuple[str, str | None]:
    if pid == 0:
        return "Sons do sistema", None
    try:
        p = psutil.Process(pid)
        exe = p.exe()
        from .win import version_info
        return version_info(exe).get("description") or p.name().removesuffix(".exe"), p.name()
    except (psutil.Error, OSError):
        return (display if display and not display.startswith("@") else f"PID {pid}"), None


def _stream(g: dict, dev_id: str) -> dict:
    vols = [c.QueryInterface(ISimpleAudioVolume) for c in g["ctls"]]
    app, binary = _app_name(g["pid"], g["display"])
    return {"index": g["key"], "app": app, "media": None, "binary": binary, "pid": g["pid"] or None, "icon": None,
            "volume": round(max(v.GetMasterVolume() for v in vols) * 100),
            "mute": all(v.GetMute() for v in vols), "corked": not g["active"], "device": dev_id}


def state() -> dict:
    with _Com():
        render = EDataFlow.eRender.value
        capture = EDataFlow.eCapture.value
        dsink, dsource = _default_id(render), _default_id(capture)
        sinks, sources = _devices(render), _devices(capture)
        routing = _routing()
        apps, recording = [], []
        for d in sinks:
            for g in _sessions(d).values():
                if g["pid"] and not psutil.pid_exists(g["pid"]):
                    continue
                if not g["pid"] and d.id != dsink:     # "Sons do sistema" existe em toda saída; mostra o da padrão
                    continue
                s = _stream(g, d.id)
                s["routed"] = routing.get(g["pid"]) if routing and g["pid"] else None
                apps.append(s)
        if routing:
            routing.close()
        for d in sources:
            for g in _sessions(d).values():
                if g["active"]:
                    recording.append(_stream(g, d.id))
        apps.sort(key=lambda a: (a["corked"], a["app"].lower()))
        return {
            "sinks": [_dev(s, dsink) for s in sinks],
            "sources": [_dev(s, dsource) for s in sources],
            "apps": apps, "recording": recording,
            "default_sink": dsink, "default_source": dsource,
            "max_volume": 100, "can_move": routing is not None,
        }


def _name_ok(n) -> str:
    if not isinstance(n, str) or not re.fullmatch(r"[\w.{}#&\\-]{1,200}", n):
        raise AudioError("Dispositivo inválido.")
    return n


def _pct(v) -> float:
    try:
        v = int(v)
    except (TypeError, ValueError):
        raise AudioError("Volume inválido.") from None
    if not 0 <= v <= 100:
        raise AudioError("Volume deve estar entre 0 e 100%.")
    return v / 100


def _find_dev(dev_id: str):
    for flow in (EDataFlow.eRender.value, EDataFlow.eCapture.value):
        for d in _devices(flow):
            if d.id == dev_id:
                return d
    raise AudioError("Dispositivo não encontrado.", 404)


def _find_group(key: int, flow: int) -> dict:
    for d in _devices(flow):
        g = _sessions(d).get(key)
        if g:
            return g
    raise AudioError("O aplicativo não está mais tocando.", 404)


def action(body: dict) -> dict:
    """{"op": ..., ...}
    set_default  {kind: sink|source, name}
    volume       {kind: sink|source|app|rec, name|index, volume}
    mute         {kind: ..., name|index, mute: bool}
    move         {index, sink}           (app -> outra saída; sink "" = padrão do Windows)
    """
    op = body.get("op")
    kind = body.get("kind", "sink")
    with _Com():
        if op == "set_default":
            dev_id = _name_ok(body.get("name"))
            _find_dev(dev_id)
            try:
                AudioUtilities.SetDefaultDevice(dev_id, [ERole.eConsole, ERole.eMultimedia, ERole.eCommunications])
            except OSError as e:
                raise AudioError(f"O Windows recusou: {e}", 500) from None
        elif op in ("volume", "mute"):
            if kind in ("sink", "source"):
                vol = _find_dev(_name_ok(body.get("name"))).EndpointVolume
                if op == "volume":
                    vol.SetMasterVolumeLevelScalar(_pct(body.get("volume")), None)
                else:
                    vol.SetMute(bool(body.get("mute")), None)
            elif kind in ("app", "rec"):
                flow = EDataFlow.eRender.value if kind == "app" else EDataFlow.eCapture.value
                g = _find_group(int(body["index"]), flow)
                for c in g["ctls"]:
                    v = c.QueryInterface(ISimpleAudioVolume)
                    if op == "volume":
                        v.SetMasterVolume(_pct(body.get("volume")), None)
                    else:
                        v.SetMute(bool(body.get("mute")), None)
            else:
                raise AudioError("Tipo inválido.")
        elif op == "move":
            # sink "" = volta a seguir a saída padrão do Windows
            sink = body.get("sink") or None
            if sink is not None:
                sink = _name_ok(sink)
                if sink not in {d.id for d in _devices(EDataFlow.eRender.value)}:
                    raise AudioError("Saída não encontrada.", 404)
            g = _find_group(int(body["index"]), EDataFlow.eRender.value)
            if not g["pid"]:
                raise AudioError("Os sons do sistema sempre saem pela saída padrão.")
            routing = _routing()
            if not routing:
                raise AudioError("Esta versão do Windows não permite escolher a saída de cada app; use "
                                 "Configurações > Sistema > Som > Mixer de volume no PC.", 501)
            try:
                routing.set(g["pid"], sink)
            finally:
                routing.close()
        else:
            raise AudioError("Operação inválida.")
    return state()
