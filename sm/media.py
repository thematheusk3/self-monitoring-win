"""O que está tocando no PC (Spotify, navegador, VLC...) pelos controles de mídia do Windows.

São os mesmos dados do painel de mídia do Windows (o que aparece ao mudar o volume): cada app que
toca algo publica uma sessão com título, artista, álbum, capa, posição e botões permitidos.
API: Windows.Media.Control (WinRT), via o pacote `winrt-Windows.Media.Control`.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import threading
import time
from datetime import datetime, timezone

log = logging.getLogger(__name__)
# PlaybackStatus: 0 Closed, 1 Opened, 2 Changing, 3 Stopped, 4 Playing, 5 Paused
STATUS = {4: "playing", 5: "paused"}
APP_NAMES = {"spotify.exe": "Spotify", "chrome": "Google Chrome", "msedge": "Microsoft Edge", "308046b0af4a39cb": "Firefox",
             "6f193ccc56814779": "Firefox", "brave": "Brave", "opera": "Opera", "vlc.exe": "VLC",
             "microsoft.zunemusic_8wekyb3d8bbwe!microsoft.zunemusic": "Media Player", "discord.exe": "Discord"}
OPS = {"play_pause": "try_toggle_play_pause_async", "next": "try_skip_next_async",
       "previous": "try_skip_previous_async", "play": "try_play_async", "pause": "try_pause_async"}
REPEAT = {0: "none", 1: "track", 2: "list"}        # MediaPlaybackAutoRepeatMode
REPEAT_NEXT = {"none": 2, "list": 1, "track": 0}    # toque no botão: desligado -> playlist -> faixa -> desligado
# player (AUMID) -> executável da sessão de áudio, quando o nome não bate direto
AUDIO_EXE = {"chrome": "chrome", "msedge": "msedge", "308046b0af4a39cb": "firefox", "6f193ccc56814779": "firefox",
             "brave": "brave", "opera": "opera", "microsoft.zunemusic_8wekyb3d8bbwe!microsoft.zunemusic": "microsoft.media.player"}


class MediaError(Exception):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def app_name(aumid: str) -> str:
    low = aumid.lower()
    if low in APP_NAMES:
        return APP_NAMES[low]
    if "!" in aumid:                       # app da Loja: Pacote_hash!App
        return aumid.split("_")[0].split(".")[-1]
    return aumid.removesuffix(".exe").removesuffix(".EXE")


class Media:
    def __init__(self) -> None:
        from winrt.windows.media.control import GlobalSystemMediaTransportControlsSessionManager as Manager
        self._Manager = Manager
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self.loop.run_forever, daemon=True, name="media").start()
        self.manager = self._run(Manager.request_async(), 10)
        self.players: list[dict] = []
        self.last_playing: dict[str, float] = {}
        self.arts: dict[str, tuple[str, bytes, str]] = {}    # player -> (chave da faixa, bytes, tipo)

    def _run(self, aw, timeout: float = 3):
        async def wrap():
            return await aw
        return asyncio.run_coroutine_threadsafe(wrap(), self.loop).result(timeout)

    async def _collect(self) -> list[dict]:
        out = []
        now = datetime.now(timezone.utc)
        for s in self.manager.get_sessions():
            aid = s.source_app_user_model_id
            try:
                p = await s.try_get_media_properties_async()
            except OSError:
                continue
            pb = s.get_playback_info()
            tl = s.get_timeline_properties()
            c = pb.controls
            status = STATUS.get(int(pb.playback_status), "stopped")
            try:
                shuffle = None if pb.is_shuffle_active is None else bool(pb.is_shuffle_active)
                repeat = None if pb.auto_repeat_mode is None else REPEAT.get(int(pb.auto_repeat_mode))
            except Exception:  # noqa: BLE001  (sem o pacote winrt-Windows.Media)
                shuffle = repeat = None
            length = tl.end_time.total_seconds() - tl.start_time.total_seconds() if tl.end_time else 0
            pos = tl.position.total_seconds() if tl.position is not None else None
            # a posição vale para last_updated_time; tocando, soma o tempo desde então
            if pos is not None and status == "playing" and tl.last_updated_time:
                upd = tl.last_updated_time
                if upd.tzinfo is None:
                    upd = upd.replace(tzinfo=timezone.utc)
                pos += max(0.0, (now - upd).total_seconds())
            if pos is not None and length:
                pos = min(pos, length)
            track = f"{p.title}|{p.artist}|{p.album_title}"
            art = None
            if p.thumbnail:
                art = "thumb:" + hashlib.sha1(f"{aid}|{track}".encode()).hexdigest()[:12]
                if self.arts.get(aid, ("",))[0] != art:
                    data = await _read_thumb(p.thumbnail)
                    if data:
                        self.arts[aid] = (art, data, _ctype(data))
            out.append({
                "id": aid, "app": app_name(aid), "desktop": None, "status": status,
                "title": p.title or None, "artist": p.artist or p.album_artist or None, "album": p.album_title or None,
                "art": art if aid in self.arts else None, "url": None,
                "length": round(length, 1) if length > 0 else None,
                "position": round(pos, 1) if pos is not None and length > 0 else None,
                "rate": pb.playback_rate or 1.0, "shuffle": shuffle, "repeat": repeat,
                "can": {"play_pause": bool(c.is_play_pause_toggle_enabled or c.is_play_enabled or c.is_pause_enabled),
                        "next": bool(c.is_next_enabled), "previous": bool(c.is_previous_enabled),
                        "seek": bool(c.is_playback_position_enabled) and length > 0,
                        "shuffle": bool(c.is_shuffle_enabled) and shuffle is not None,
                        "repeat": bool(c.is_repeat_enabled) and repeat is not None},
            })
        return out

    def collect(self) -> dict:
        try:
            players = self._run(self._collect())
        except Exception as e:  # noqa: BLE001
            log.debug("mídia: %s", e)
            players = []
        now = time.time()
        for p in players:
            p["at"] = now
            if p["status"] == "playing":
                self.last_playing[p["id"]] = now
        players.sort(key=lambda p: (p["status"] != "playing", p["status"] != "paused", -self.last_playing.get(p["id"], 0)))
        self.players = players
        current = next((p for p in players if p["status"] != "stopped" and p["title"]), None)
        return {"current": current, "players": players}

    def _find(self, pid: str) -> dict:
        p = next((p for p in self.players if p["id"] == pid), None)
        if not p:
            raise MediaError("Player não encontrado (fechou?).", 404)
        return p

    def control(self, pid: str, op: str, offset: float = 0) -> dict:
        """op em OPS, ou "seek" com offset em segundos (negativo volta)."""
        p = self._find(pid)
        session = next((s for s in self.manager.get_sessions() if s.source_app_user_model_id == pid), None)
        if session is None:
            raise MediaError("Player não encontrado (fechou?).", 404)
        if op == "seek":
            if not p["can"].get("seek"):
                raise MediaError("Este player não permite avançar/voltar.")
            try:
                off = float(offset)
            except (TypeError, ValueError):
                raise MediaError("offset inválido.") from None
            if not off or abs(off) > 24 * 3600:
                raise MediaError("offset inválido.")
            target = max(0.0, min((p["position"] or 0) + off, p["length"] or 0))
            aw = session.try_change_playback_position_async(int(target * 10_000_000))   # unidades de 100 ns
        elif op == "shuffle":
            if not p["can"].get("shuffle"):
                raise MediaError("Este player não permite o modo aleatório.")
            aw = session.try_change_shuffle_active_async(not p.get("shuffle"))
        elif op == "repeat":
            if not p["can"].get("repeat"):
                raise MediaError("Este player não permite repetir.")
            from winrt.windows.media import MediaPlaybackAutoRepeatMode
            aw = session.try_change_auto_repeat_mode_async(MediaPlaybackAutoRepeatMode(REPEAT_NEXT[p.get("repeat") or "none"]))
        elif op in OPS:
            aw = getattr(session, OPS[op])()
        else:
            raise MediaError("Operação inválida.")
        try:
            ok = self._run(aw)
        except Exception as e:  # noqa: BLE001
            raise MediaError(f"O player recusou: {e}", 500) from None
        if not ok:
            raise MediaError("O player recusou o comando.", 409)
        time.sleep(0.3)       # o player leva um instante para atualizar o estado
        return self.collect()

    def audio_session(self, pid: str) -> dict | None:
        """Sessão de áudio (Mixer de volume) do app deste player: volume próprio do app, sem mexer no geral."""
        self._find(pid)
        from . import audio
        return match_session(pid, audio.state()["apps"])

    def art_file(self, pid: str) -> tuple[bytes, str] | None:
        self._find(pid)
        a = self.arts.get(pid)
        return (a[1], a[2]) if a else None


def match_session(player_id: str, apps: list[dict]) -> dict | None:
    """Player (AUMID: "Spotify.exe", "MSEdge", "Microsoft.ZuneMusic_...!...") -> sessão de áudio do mesmo executável.
    Se houver várias (uma por saída), fica com a que está tocando."""
    low = player_id.lower()
    want = AUDIO_EXE.get(low) or low.split("!")[0].split("_")[0].removesuffix(".exe")
    found = [a for a in apps if a.get("binary") and a["binary"].lower().removesuffix(".exe") == want]
    if not found:
        return None
    a = sorted(found, key=lambda x: x.get("corked", False))[0]
    return {"index": a["index"], "volume": a["volume"], "mute": a["mute"], "app": a["app"], "device": a.get("device")}


async def _read_thumb(ref) -> bytes | None:
    from winrt.windows.storage.streams import DataReader
    try:
        st = await ref.open_read_async()
        n = st.size
        if not n or n > 10 * 1024 * 1024:
            return None
        r = DataReader(st.get_input_stream_at(0))
        await r.load_async(n)
        return bytes(memoryview(r.read_buffer(n)))
    except OSError:
        return None


def _ctype(data: bytes) -> str:
    for magic, ct in ((b"\x89PNG", "image/png"), (b"\xff\xd8\xff", "image/jpeg"), (b"GIF8", "image/gif"), (b"RIFF", "image/webp")):
        if data.startswith(magic):
            return ct
    return "application/octet-stream"
