from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Callable


def write_atomic(path: Path | str, writer: Callable[[Path], None]) -> None:
    """Write to ``path`` atomically: readers see the old file or the new one, never a partial.

    The writer receives a unique temp path in the same directory, so concurrent
    writers to the same target cannot corrupt each other; last one wins.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / f"{path.name}.{uuid.uuid4().hex}.tmp"
    try:
        writer(tmp)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)
