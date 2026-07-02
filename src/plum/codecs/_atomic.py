from __future__ import annotations

import os
from pathlib import Path
from typing import Callable


def write_atomic(path: Path | str, writer: Callable[[Path], None]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    writer(tmp)
    os.replace(tmp, path)
