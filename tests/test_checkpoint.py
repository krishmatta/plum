import pytest
from pydantic import BaseModel

from plum import JsonlCodec, Shards


class Item(BaseModel):
    n: int


def codec():
    return JsonlCodec(Item)


def items(n):
    return [Item(n=i) for i in range(n)]


def test_fresh_pending_covers_all_ranges(tmp_path):
    shards = Shards(tmp_path, 10, 4, codec())
    assert shards.pending == [
        (0, slice(0, 4)),
        (1, slice(4, 8)),
        (2, slice(8, 10)),
    ]


def test_pending_shows_remainder_after_partial(tmp_path):
    all_items = items(10)
    shards = Shards(tmp_path, 10, 4, codec())
    for idx, sl in shards.pending[:2]:
        shards.write(idx, all_items[sl])
    reopened = Shards(tmp_path, 10, 4, codec())
    assert reopened.pending == [(2, slice(8, 10))]


def test_finalize_missing_shards_raises(tmp_path):
    all_items = items(10)
    shards = Shards(tmp_path, 10, 4, codec())
    shards.write(0, all_items[0:4])
    with pytest.raises(FileNotFoundError):
        shards.finalize(tmp_path / "out.jsonl")


def test_finalize_concatenates_in_order(tmp_path):
    all_items = items(10)
    shards = Shards(tmp_path, 10, 4, codec())
    for idx, sl in shards.pending:
        shards.write(idx, all_items[sl])
    out = shards.finalize(tmp_path / "out.jsonl")
    assert codec().read(out) == all_items


def test_zero_items(tmp_path):
    shards = Shards(tmp_path, 0, 4, codec())
    assert shards.pending == []
    out = shards.finalize(tmp_path / "out.jsonl")
    assert codec().read(out) == []


def test_shard_size_zero_raises(tmp_path):
    with pytest.raises(ValueError):
        Shards(tmp_path, 10, 0, codec())


def test_reopen_different_shard_size_raises(tmp_path):
    Shards(tmp_path, 10, 4, codec())
    with pytest.raises(ValueError) as exc:
        Shards(tmp_path, 10, 5, codec())
    msg = str(exc.value)
    assert "shard_size=4" in msg
    assert "shard_size=5" in msg


def test_reopen_different_n_items_raises(tmp_path):
    Shards(tmp_path, 10, 4, codec())
    with pytest.raises(ValueError) as exc:
        Shards(tmp_path, 12, 4, codec())
    msg = str(exc.value)
    assert "n_items=10" in msg
    assert "n_items=12" in msg


def test_meta_json_not_treated_as_shard(tmp_path):
    all_items = items(10)
    shards = Shards(tmp_path, 10, 4, codec())
    for idx, sl in shards.pending:
        shards.write(idx, all_items[sl])
    assert (tmp_path / "shards" / "meta.json").exists()
    assert shards.pending == []
    out = shards.finalize(tmp_path / "out.jsonl")
    assert codec().read(out) == all_items
