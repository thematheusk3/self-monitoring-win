"""Letra sincronizada da música que está tocando, do LRCLIB (lrclib.net: banco aberto e gratuito, sem conta).

A letra vem em formato LRC ("[01:23.45] verso"); o Mural destaca o verso pela posição que o player informa ao
Windows (a mesma da barra de progresso). Só título, artista, álbum e duração saem do PC. Resultados (inclusive
"não achei") ficam guardados em memória para não repetir a busca a cada atualização.
Desliga em Mural > Letra sincronizada.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import urllib.error
import urllib.parse
import urllib.request
from collections import OrderedDict

from . import __version__

log = logging.getLogger(__name__)
API = "https://lrclib.net/api"
UA = f"self-monitoring-win/{__version__} (https://github.com/thematheusk3/self-monitoring-win)"
TIMEOUT = 8
MAX_CACHE = 200
RX_LRC = re.compile(r"\[(\d{1,3}):(\d{1,2}(?:[.:]\d{1,3})?)\]")
# "Música - Remastered 2011", "Música (feat. X)", "Música - Ao Vivo": versões que o banco costuma guardar sem o sufixo
RX_EXTRA = re.compile(r"\s*[-–(\[].*?(remaster|remix|ao vivo|live|feat\.?|ft\.|with |versão|version|edit|mono|stereo|"
                      r"deluxe|bonus|acoustic|ac[uú]stico).*$", re.I)

_cache: OrderedDict[tuple, dict] = OrderedDict()
_lock = threading.Lock()


def parse_lrc(text: str) -> list[list]:
    """LRC -> [[segundos, verso], ...] em ordem. Uma linha pode ter vários tempos (refrão repetido)."""
    out = []
    for line in (text or "").splitlines():
        stamps = list(RX_LRC.finditer(line))
        if not stamps:
            continue
        verse = line[stamps[-1].end():].strip()
        for m in stamps:
            sec = float(m.group(2).replace(":", "."))
            out.append([round(int(m.group(1)) * 60 + sec, 2), verse])
    out.sort(key=lambda x: x[0])
    return out


def _get(path: str, params: dict):
    url = f"{API}/{path}?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return json.loads(r.read(2_000_000).decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def _pick(items, duration: float | None) -> dict | None:
    """Da busca, o resultado com sincronia e duração mais próxima (até 4 s de diferença)."""
    best = None
    for it in items or []:
        if not it.get("syncedLyrics"):
            continue
        diff = abs((it.get("duration") or 0) - duration) if duration else 0
        if duration and diff > 4:
            continue
        if best is None or diff < best[0]:
            best = (diff, it)
    return best[1] if best else None


def fetch(title: str, artist: str, album: str | None, duration: float | None) -> dict:
    params = {"track_name": title, "artist_name": artist}
    if album:
        params["album_name"] = album
    if duration:
        params["duration"] = int(round(duration))
    hit = _get("get", params)
    if not (hit and hit.get("syncedLyrics")):
        short = RX_EXTRA.sub("", title).strip() or title
        found = _get("search", {"track_name": short, "artist_name": artist.split(",")[0].split(" & ")[0]})
        hit = _pick(found, duration) or hit
    if not hit:
        return {"found": False, "synced": [], "plain": None}
    return {"found": True, "synced": parse_lrc(hit.get("syncedLyrics") or ""), "plain": (hit.get("plainLyrics") or None),
            "instrumental": bool(hit.get("instrumental")), "source": "LRCLIB", "id": hit.get("id")}


def lyrics(title: str | None, artist: str | None, album: str | None = None, duration: float | None = None) -> dict:
    if not title or not artist:
        return {"found": False, "synced": [], "plain": None}
    key = (title.lower(), artist.lower(), round(duration or 0))
    with _lock:
        if key in _cache:
            _cache.move_to_end(key)
            return _cache[key]
    try:
        res = fetch(title, artist, album, duration)
    except (OSError, ValueError) as e:
        log.info("letra: LRCLIB indisponível (%s)", e)
        return {"found": False, "synced": [], "plain": None, "error": "Não foi possível buscar a letra agora."}
    with _lock:
        _cache[key] = res
        while len(_cache) > MAX_CACHE:
            _cache.popitem(last=False)
    return res
