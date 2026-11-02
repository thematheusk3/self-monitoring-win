from __future__ import annotations

from . import Collector, register
from ..media import Media


@register
class MediaPlaying(Collector):
    """O que está tocando (controles de mídia do Windows: Spotify, navegador, VLC...)."""
    name = "media"
    interval = 2

    def setup(self) -> None:
        self.media = Media()
        self.ctx.media = self.media      # usado pela API (controles e capa)

    def collect(self) -> dict:
        return self.media.collect()
