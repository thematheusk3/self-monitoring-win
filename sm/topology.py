"""Monitores: tela principal e ligar/desligar cada monitor (a mesma API das Configurações do Windows).

Usa a "Connecting and Configuring Displays" (CCD): QueryDisplayConfig lê os caminhos (placa de vídeo -> monitor) e
SetDisplayConfig aplica.
- Estender/duplicar não está aqui: com três monitores o Windows recusa SDC_TOPOLOGY_EXTEND/CLONE (erro 87), testado
  na 0.20.0-win; removido na 0.20.1-win. A leitura ainda informa se a tela está estendida ou duplicada.
- Principal: é o monitor cuja área começa em (0, 0); desloca as posições de todos para que o escolhido fique na origem.
- Desligar um monitor: aplica a configuração ativa sem o caminho dele (o Windows deixa de usá-lo, como em
  "Desconectar este vídeo"). Nunca desliga o último.
- Ligar um monitor: aplica os caminhos ativos mais o dele, deixando o Windows escolher o modo (SDC_TOPOLOGY_SUPPLIED).

Toda mudança guarda a configuração anterior e volta sozinha em REVERT_AFTER segundos se não for confirmada: se a tela
ficar preta ou o monitor que o usuário vê sumir, não é preciso fazer nada. `validate=True` só pergunta ao Windows se a
configuração é aceita (SDC_VALIDATE), sem mudar nada na tela (usado nos testes).
"""
from __future__ import annotations

import ctypes
import re
import struct
import threading
import time
from .display import _HEADER, _LUID, _PATH, _SOURCE_NAME, _TARGET_NAME, OUTPUT_TECH, DisplayError

REVERT_AFTER = 20
user32 = ctypes.windll.user32

QDC_ALL_PATHS, QDC_ONLY_ACTIVE_PATHS = 0x1, 0x2
PATH_ACTIVE = 0x1
MODE_IDX_INVALID = 0xFFFFFFFF
MODE_SOURCE, MODE_TARGET = 1, 2
SDC_TOPOLOGY_SUPPLIED = 0x10
SDC_USE_SUPPLIED_DISPLAY_CONFIG, SDC_VALIDATE, SDC_APPLY = 0x20, 0x40, 0x80
SDC_SAVE_TO_DATABASE, SDC_ALLOW_CHANGES, SDC_ALLOW_PATH_ORDER_CHANGES = 0x200, 0x400, 0x2000
ERRORS = {87: "configuração inválida", 50: "o driver de vídeo não suporta", 31: "a placa de vídeo recusou",
          5: "acesso negado", 1610: "configuração do vídeo indisponível", 1004: "configuração inválida"}
RX_ID = re.compile(r"[0-9a-f]{1,8}-[0-9a-f]{1,8}-\d{1,10}")


class _MODE(ctypes.Structure):
    """DISPLAYCONFIG_MODE_INFO (64 bytes). A parte variável fica em `data`; para o modo de origem:
    largura, altura, formato do pixel e a posição (x, y) da área no desktop."""
    _fields_ = [("infoType", ctypes.c_uint32), ("id", ctypes.c_uint32), ("adapterId", _LUID), ("data", ctypes.c_byte * 48)]

    def source_pos(self) -> tuple[int, int]:
        return struct.unpack_from("<ii", bytes(self.data), 12)

    def set_source_pos(self, x: int, y: int) -> None:
        raw = bytearray(bytes(self.data))
        struct.pack_into("<ii", raw, 12, x, y)
        ctypes.memmove(self.data, bytes(raw), 48)

    def source_size(self) -> tuple[int, int]:
        return struct.unpack_from("<II", bytes(self.data), 0)


def _query(flags: int):
    np, nm = ctypes.c_uint32(), ctypes.c_uint32()
    for _ in range(3):         # a configuração pode mudar entre as duas chamadas
        if user32.GetDisplayConfigBufferSizes(flags, ctypes.byref(np), ctypes.byref(nm)):
            raise DisplayError("Não foi possível ler a configuração dos monitores.", 500)
        paths, modes = (_PATH * max(1, np.value))(), (_MODE * max(1, nm.value))()
        rc = user32.QueryDisplayConfig(flags, ctypes.byref(np), paths, ctypes.byref(nm), modes, None)
        if rc == 0:
            return list(paths[:np.value]), list(modes[:nm.value])
        if rc != 122:          # ERROR_INSUFFICIENT_BUFFER: tenta de novo
            break
    raise DisplayError("Não foi possível ler a configuração dos monitores.", 500)


def _set(paths: list, modes: list | None, flags: int) -> None:
    n = len(paths)
    parr = (_PATH * max(1, n))(*paths)
    if modes is None:
        rc = user32.SetDisplayConfig(n, parr, 0, None, flags)
    else:
        marr = (_MODE * max(1, len(modes)))(*modes)
        rc = user32.SetDisplayConfig(n, parr, len(modes), marr, flags)
    if rc:
        raise DisplayError(f"O Windows recusou a configuração: {ERRORS.get(rc, f'erro {rc}')}.", 500)


def _tid(p) -> str:
    return f"{p.tgtAdapter.HighPart & 0xFFFFFFFF:x}-{p.tgtAdapter.LowPart:x}-{p.tgtId}"


def _target_name(p) -> tuple[str | None, str | None]:
    t = _TARGET_NAME()
    t.header = _HEADER(2, ctypes.sizeof(_TARGET_NAME), p.tgtAdapter, p.tgtId)
    if user32.DisplayConfigGetDeviceInfo(ctypes.byref(t)):
        return None, None
    return t.monitorFriendlyDeviceName or None, OUTPUT_TECH.get(t.outputTechnology)


def _source_name(p) -> str | None:
    s = _SOURCE_NAME()
    s.header = _HEADER(1, ctypes.sizeof(_SOURCE_NAME), p.srcAdapter, p.srcId)
    if user32.DisplayConfigGetDeviceInfo(ctypes.byref(s)):
        return None
    return s.viewGdiDeviceName.removeprefix("\\\\.\\")


# ---------- leitura ----------
def screens() -> dict:
    """Monitores conectados (ligados ou não), qual é o principal e o jeito atual: estendido, duplicado ou um só."""
    allp, _ = _query(QDC_ALL_PATHS)
    act, amodes = _query(QDC_ONLY_ACTIVE_PATHS)
    active = {_tid(p): p for p in act}
    out, seen = [], set()
    for p in allp:
        tid = _tid(p)
        if tid in seen or not (p.available or tid in active):
            continue
        seen.add(tid)
        name, conn = _target_name(p)
        a = active.get(tid)
        item = {"id": tid, "name": name or "Monitor", "connector": conn, "active": a is not None,
                "primary": False, "display": None, "position": None, "size": None}
        if a is not None:
            item["display"] = _source_name(a)
            if a.srcModeIdx != MODE_IDX_INVALID and a.srcModeIdx < len(amodes):
                m = amodes[a.srcModeIdx]
                item["position"], item["size"] = m.source_pos(), m.source_size()
                item["primary"] = item["position"] == (0, 0)
        out.append(item)
    srcs = [(p.srcAdapter.LowPart, p.srcAdapter.HighPart, p.srcId) for p in act]
    mode = "single" if len(act) <= 1 else "clone" if len(set(srcs)) < len(srcs) else "extend"
    out.sort(key=lambda s: (not s["active"], (s["position"] or (1 << 30, 0))[0], s["name"]))
    return {"mode": mode, "screens": out}


# ---------- mudanças ----------
def _check_id(tid: str) -> str:
    if not RX_ID.fullmatch(tid or ""):
        raise DisplayError("Monitor inválido.")
    return tid


def _shift_to(modes: list, pos: tuple[int, int]) -> None:
    """Desloca todas as áreas para que `pos` vire a origem (0, 0): é assim que o Windows define o principal."""
    x0, y0 = pos
    for m in modes:
        if m.infoType == MODE_SOURCE:
            x, y = m.source_pos()
            m.set_source_pos(x - x0, y - y0)


def set_primary(tid: str, validate: bool = False) -> None:
    _check_id(tid)
    act, modes = _query(QDC_ONLY_ACTIVE_PATHS)
    p = next((p for p in act if _tid(p) == tid), None)
    if p is None:
        raise DisplayError("Esse monitor está desligado: ligue-o antes de torná-lo principal.", 409)
    if p.srcModeIdx == MODE_IDX_INVALID:
        raise DisplayError("Não foi possível localizar a área desse monitor.", 500)
    shared = [q for q in act if (q.srcAdapter.LowPart, q.srcAdapter.HighPart, q.srcId) == (p.srcAdapter.LowPart, p.srcAdapter.HighPart, p.srcId)]
    if len(shared) > 1:
        raise DisplayError("Com a tela duplicada todos mostram a mesma imagem: estenda a tela para escolher o principal.", 409)
    pos = modes[p.srcModeIdx].source_pos()
    if pos == (0, 0):
        return
    _shift_to(modes, pos)
    _set(act, modes, SDC_USE_SUPPLIED_DISPLAY_CONFIG | SDC_ALLOW_CHANGES | (SDC_VALIDATE if validate else SDC_APPLY | SDC_SAVE_TO_DATABASE))


def disable(tid: str, validate: bool = False) -> None:
    _check_id(tid)
    act, modes = _query(QDC_ONLY_ACTIVE_PATHS)
    keep = [p for p in act if _tid(p) != tid]
    if len(keep) == len(act):
        raise DisplayError("Esse monitor já está desligado.", 409)
    if not keep:
        raise DisplayError("Este é o único monitor ligado: desligá-lo deixaria o PC sem imagem.", 409)
    # se o desligado era o principal, o mais à esquerda dos que sobram passa a ser
    src = [modes[p.srcModeIdx] for p in keep if p.srcModeIdx != MODE_IDX_INVALID]
    if src and (0, 0) not in [m.source_pos() for m in src]:
        _shift_to(modes, min((m.source_pos() for m in src), key=lambda xy: (xy[0], xy[1])))
    _set(keep, modes, SDC_USE_SUPPLIED_DISPLAY_CONFIG | SDC_ALLOW_CHANGES | (SDC_VALIDATE if validate else SDC_APPLY | SDC_SAVE_TO_DATABASE))


def enable(tid: str, validate: bool = False) -> None:
    _check_id(tid)
    allp, allm = _query(QDC_ALL_PATHS)
    act = [p for p in allp if p.flags & PATH_ACTIVE]
    if any(_tid(p) == tid for p in act):
        raise DisplayError("Esse monitor já está ligado.", 409)
    used = {(p.srcAdapter.LowPart, p.srcAdapter.HighPart, p.srcId) for p in act}
    new = next((p for p in allp if _tid(p) == tid and p.available
                and (p.srcAdapter.LowPart, p.srcAdapter.HighPart, p.srcId) not in used), None)
    if new is None:
        raise DisplayError("Não há saída livre na placa de vídeo para ligar esse monitor.", 409)
    # Os ativos ficam como estão (com os modos deles) e o novo vai sem modo: com SDC_ALLOW_CHANGES o Windows escolhe
    # resolução e posição para ele. (SDC_TOPOLOGY_SUPPLIED com todos os modos em branco é recusado pela NVIDIA, erro 31.)
    new.flags |= PATH_ACTIVE
    new.srcModeIdx = new.tgtModeIdx = MODE_IDX_INVALID
    _set(act + [new], allm, SDC_USE_SUPPLIED_DISPLAY_CONFIG | SDC_ALLOW_CHANGES
         | (SDC_VALIDATE if validate else SDC_APPLY | SDC_SAVE_TO_DATABASE))


# ---------- com confirmação ----------
LABELS = {"primary": "Novo monitor principal",
          "disable": "Monitor desligado", "enable": "Monitor ligado"}


class TopologyChanger:
    """Aplica e volta à configuração anterior se não for confirmada a tempo (como a troca de resolução)."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.pending: dict | None = None
        self._saved = None

    def status(self) -> dict | None:
        with self.lock:
            if not self.pending:
                return None
            return {**self.pending, "seconds_left": max(0, round(self.pending["deadline"] - time.time()))}

    def apply(self, op: str, tid: str | None = None, other_pending: bool = False) -> dict:
        fn = {"primary": lambda: set_primary(tid), "disable": lambda: disable(tid), "enable": lambda: enable(tid)}.get(op)
        if fn is None:
            raise DisplayError("Operação inválida.")
        if other_pending:
            raise DisplayError("Confirme ou desfaça a mudança de resolução primeiro.", 409)
        names = {s["id"]: s["name"] for s in screens()["screens"]}
        if op in ("primary", "disable", "enable") and tid not in names:
            raise DisplayError("Monitor não encontrado.", 404)
        with self.lock:
            if self.pending:
                raise DisplayError("Confirme ou desfaça a mudança anterior primeiro.", 409)
            saved = _query(QDC_ONLY_ACTIVE_PATHS)
            fn()
            self._saved = saved
            label = LABELS[op] + (f": {names[tid]}" if tid else "")
            self.pending = {"op": op, "target": tid, "label": label, "deadline": time.time() + REVERT_AFTER}
            token = self.pending
        threading.Timer(REVERT_AFTER, self._timeout, args=(token,)).start()
        return self.status()

    def confirm(self) -> None:
        with self.lock:
            self.pending, self._saved = None, None

    def revert(self) -> None:
        with self.lock:
            saved, self.pending, self._saved = self._saved, None, None
        if saved:
            paths, modes = saved
            _set(paths, modes, SDC_APPLY | SDC_USE_SUPPLIED_DISPLAY_CONFIG | SDC_SAVE_TO_DATABASE | SDC_ALLOW_CHANGES)

    def _timeout(self, token: dict) -> None:
        with self.lock:
            if self.pending is not token:
                return
        try:
            self.revert()
        except DisplayError:
            pass            # a configuração salva não vale mais (ex.: um monitor foi desconectado no meio)


TOPOLOGY = TopologyChanger()
