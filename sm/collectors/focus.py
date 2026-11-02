from __future__ import annotations

from . import Collector, register
from ..focus import FocusMonitor


@register
class Focus(Collector):
    """Janela em foco no PC."""
    name = "focus"
    interval = 2

    def setup(self) -> None:
        self.mon = FocusMonitor()

    def collect(self) -> dict:
        st = self.mon.status()
        if st["window"]:
            st["window"]["process"] = st["window"]["name"]
        return st
