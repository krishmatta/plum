from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Callable


def write_atomic(path: Path | str, writer: Callable[[Path], None]) -> None:
    """Readers see the old file or the new one, never a partial; concurrent writers are safe (last wins)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / f"{path.name}.{uuid.uuid4().hex}.tmp"
    try:
        writer(tmp)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)
