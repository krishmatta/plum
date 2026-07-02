from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from plum.codecs import Codec

SHARD_RE = re.compile(r"^shard_(\d{5})(\..+)$")


class Shards:
    """Resumable sharded output for a pipeline run.

    Wraps the shard bookkeeping behind the loop pipelines actually write:

        shards = ctx.shards(len(items), shard_size, codec)
        if shards.pending:
            backend = ...  # only pay for expensive setup when there is work
            for idx, sl in shards.pending:
                shards.write(idx, compute(items[sl]))
        shards.finalize(ctx.output_path())
    """

    def __init__(self, run_dir: Path | str, n_items: int, shard_size: int, codec: Codec):
        if shard_size <= 0:
            raise ValueError(f"shard_size must be positive, got {shard_size}")
        self.shards_dir = Path(run_dir) / "shards"
        self.n_items = n_items
        self.shard_size = shard_size
        self.codec = codec
        self.total_shards = (n_items + shard_size - 1) // shard_size if n_items > 0 else 0

    def _shard_slice(self, idx: int) -> slice:
        start = idx * self.shard_size
        return slice(start, min(start + self.shard_size, self.n_items))

    def _shard_path(self, idx: int) -> Path:
        return self.shards_dir / f"shard_{idx:05d}{self.codec.extension}"

    def _completed(self) -> set[int]:
        done: set[int] = set()
        if not self.shards_dir.exists():
            return done
        for entry in self.shards_dir.iterdir():
            m = SHARD_RE.match(entry.name)
            if not m or m.group(2) != self.codec.extension:
                continue
            idx = int(m.group(1))
            if idx < self.total_shards:
                done.add(idx)
        return done

    @property
    def pending(self) -> list[tuple[int, slice]]:
        done = self._completed()
        return [
            (idx, self._shard_slice(idx))
            for idx in range(self.total_shards)
            if idx not in done
        ]

    def write(self, idx: int, payload: Any) -> None:
        self.codec.write(payload, self._shard_path(idx))

    def finalize(self, final_path: Path | str) -> Path:
        final_path = Path(final_path)
        paths = [self._shard_path(i) for i in range(self.total_shards)]
        missing = [p for p in paths if not p.exists()]
        if missing:
            raise FileNotFoundError(f"cannot finalize, missing shards: {missing}")
        combined: list[Any] = []
        for p in paths:
            part = self.codec.read(p)
            if not isinstance(part, list):
                raise TypeError(f"shard {p} is not a list; got {type(part)}")
            combined.extend(part)
        self.codec.write(combined, final_path)
        return final_path
