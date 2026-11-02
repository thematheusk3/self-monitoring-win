"""Tabela de conexões TCP do Windows (IP Helper) com bytes por conexão.

- GetExtendedTcpTable: todas as conexões TCP (IPv4/IPv6) com o PID dono.
- GetPerTcpConnectionEStats: bytes enviados/recebidos de cada conexão. O Windows só conta depois que
  alguém liga a coleta naquela conexão (SetPerTcpConnectionEStats), e isso exige administrador; então
  os bytes contam a partir de quando o painel viu a conexão.
"""
from __future__ import annotations

import ctypes
import socket
import struct
from ctypes import wintypes

AF_INET, AF_INET6 = 2, 23
TCP_TABLE_OWNER_PID_ALL = 5
TcpConnectionEstatsData = 1
ERROR_INSUFFICIENT_BUFFER = 122
STATES = {1: "CLOSED", 2: "LISTEN", 3: "SYN-SENT", 4: "SYN-RECV", 5: "ESTAB", 6: "FIN-WAIT-1", 7: "FIN-WAIT-2",
          8: "CLOSE-WAIT", 9: "CLOSING", 10: "LAST-ACK", 11: "TIME-WAIT", 12: "DELETE-TCB"}

try:
    _ip = ctypes.WinDLL("iphlpapi.dll")
except OSError:  # pragma: no cover
    _ip = None


class MIB_TCPROW_OWNER_PID(ctypes.Structure):
    _fields_ = [("dwState", wintypes.DWORD), ("dwLocalAddr", wintypes.DWORD), ("dwLocalPort", wintypes.DWORD),
                ("dwRemoteAddr", wintypes.DWORD), ("dwRemotePort", wintypes.DWORD), ("dwOwningPid", wintypes.DWORD)]


class MIB_TCP6ROW_OWNER_PID(ctypes.Structure):
    _fields_ = [("ucLocalAddr", ctypes.c_ubyte * 16), ("dwLocalScopeId", wintypes.DWORD), ("dwLocalPort", wintypes.DWORD),
                ("ucRemoteAddr", ctypes.c_ubyte * 16), ("dwRemoteScopeId", wintypes.DWORD),
                ("dwRemotePort", wintypes.DWORD), ("dwState", wintypes.DWORD), ("dwOwningPid", wintypes.DWORD)]


class MIB_TCPROW(ctypes.Structure):
    _fields_ = [("dwState", wintypes.DWORD), ("dwLocalAddr", wintypes.DWORD), ("dwLocalPort", wintypes.DWORD),
                ("dwRemoteAddr", wintypes.DWORD), ("dwRemotePort", wintypes.DWORD)]


class MIB_TCP6ROW(ctypes.Structure):
    _fields_ = [("State", wintypes.DWORD), ("LocalAddr", ctypes.c_ubyte * 16), ("dwLocalScopeId", wintypes.DWORD),
                ("dwLocalPort", wintypes.DWORD), ("RemoteAddr", ctypes.c_ubyte * 16),
                ("dwRemoteScopeId", wintypes.DWORD), ("dwRemotePort", wintypes.DWORD)]


class TCP_ESTATS_DATA_RW_v0(ctypes.Structure):
    _fields_ = [("EnableCollection", ctypes.c_ubyte)]


class TCP_ESTATS_DATA_ROD_v0(ctypes.Structure):
    _fields_ = [("DataBytesOut", ctypes.c_ulonglong), ("DataSegsOut", ctypes.c_ulonglong),
                ("DataBytesIn", ctypes.c_ulonglong), ("DataSegsIn", ctypes.c_ulonglong),
                ("SegsOut", ctypes.c_ulonglong), ("SegsIn", ctypes.c_ulonglong),
                ("SoftErrors", wintypes.ULONG), ("SoftErrorReason", wintypes.ULONG), ("SndUna", wintypes.ULONG),
                ("SndNxt", wintypes.ULONG), ("SndMax", wintypes.ULONG), ("ThruBytesAcked", ctypes.c_ulonglong),
                ("RcvNxt", wintypes.ULONG), ("ThruBytesReceived", ctypes.c_ulonglong)]


def _port(v: int) -> int:
    return socket.ntohs(v & 0xFFFF)


def _table(af: int) -> bytes:
    size = wintypes.DWORD(0)
    _ip.GetExtendedTcpTable(None, ctypes.byref(size), False, af, TCP_TABLE_OWNER_PID_ALL, 0)
    for _ in range(3):            # a tabela pode crescer entre as duas chamadas
        buf = ctypes.create_string_buffer(size.value + 4096)
        size = wintypes.DWORD(len(buf))
        rc = _ip.GetExtendedTcpTable(buf, ctypes.byref(size), False, af, TCP_TABLE_OWNER_PID_ALL, 0)
        if rc == 0:
            return buf.raw
        if rc != ERROR_INSUFFICIENT_BUFFER:
            break
    return b""


def tcp_connections() -> list[dict]:
    """[{family, state, lip, lport, rip, rport, pid, row}] — `row` serve para pedir as estatísticas."""
    if _ip is None:
        return []
    out = []
    raw = _table(AF_INET)
    if raw:
        n = struct.unpack_from("<I", raw)[0]
        rows = (MIB_TCPROW_OWNER_PID * n).from_buffer_copy(raw, 4)
        for r in rows:
            out.append({"family": 4, "state": STATES.get(r.dwState, str(r.dwState)),
                        "lip": socket.inet_ntoa(struct.pack("<I", r.dwLocalAddr)), "lport": _port(r.dwLocalPort),
                        "rip": socket.inet_ntoa(struct.pack("<I", r.dwRemoteAddr)), "rport": _port(r.dwRemotePort),
                        "pid": r.dwOwningPid,
                        "row": MIB_TCPROW(r.dwState, r.dwLocalAddr, r.dwLocalPort, r.dwRemoteAddr, r.dwRemotePort)})
    raw = _table(AF_INET6)
    if raw:
        n = struct.unpack_from("<I", raw)[0]
        rows = (MIB_TCP6ROW_OWNER_PID * n).from_buffer_copy(raw, 4)
        for r in rows:
            row = MIB_TCP6ROW(r.dwState, r.ucLocalAddr, r.dwLocalScopeId, r.dwLocalPort, r.ucRemoteAddr,
                              r.dwRemoteScopeId, r.dwRemotePort)
            out.append({"family": 6, "state": STATES.get(r.dwState, str(r.dwState)),
                        "lip": socket.inet_ntop(socket.AF_INET6, bytes(r.ucLocalAddr)), "lport": _port(r.dwLocalPort),
                        "rip": socket.inet_ntop(socket.AF_INET6, bytes(r.ucRemoteAddr)), "rport": _port(r.dwRemotePort),
                        "pid": r.dwOwningPid, "row": row})
    return out


def enable_stats(c: dict) -> bool:
    rw = TCP_ESTATS_DATA_RW_v0(1)
    fn = _ip.SetPerTcpConnectionEStats if c["family"] == 4 else _ip.SetPerTcp6ConnectionEStats
    return fn(ctypes.byref(c["row"]), TcpConnectionEstatsData, ctypes.byref(rw), 0, ctypes.sizeof(rw), 0) == 0


def read_stats(c: dict) -> tuple[int, int] | None:
    """(bytes enviados, bytes recebidos) desde que a coleta foi ligada."""
    rod = TCP_ESTATS_DATA_ROD_v0()
    fn = _ip.GetPerTcpConnectionEStats if c["family"] == 4 else _ip.GetPerTcp6ConnectionEStats
    rc = fn(ctypes.byref(c["row"]), TcpConnectionEstatsData, None, 0, 0, None, 0, 0,
            ctypes.byref(rod), 0, ctypes.sizeof(rod))
    if rc:
        return None
    return rod.DataBytesOut, rod.DataBytesIn
