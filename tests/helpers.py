"""Utilitários de teste: contexto mínimo."""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sm import config  # noqa: E402
from sm import audit  # noqa: E402

# nenhum teste grava o registro de ações na pasta data/ do repositório
audit.FILE = Path(tempfile.mkdtemp(prefix="sm-test-")) / "audit-log.json"


class Ctx:
    def __init__(self, **overrides) -> None:
        self.config = config.load(ROOT / "nao-existe.toml")   # só os padrões do código
        for k, v in overrides.items():
            setattr(self.config, k, v)
