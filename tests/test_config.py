import pytest
from pydantic import ValidationError

from plum import Params, parse_kw


class MyParams(Params):
    n: int
    label: str = "default"


def test_params_forbids_extra():
    with pytest.raises(ValidationError):
        MyParams(n=1, unknown=2)


def test_params_defaults():
    p = MyParams(n=3)
    assert p.n == 3
    assert p.label == "default"


def test_parse_kw_none():
    assert parse_kw(None) == {}


def test_parse_kw_json_values():
    out = parse_kw(["n=3", "flag=true", "items=[1, 2]", "cfg={\"a\": 1}"])
    assert out == {"n": 3, "flag": True, "items": [1, 2], "cfg": {"a": 1}}


def test_parse_kw_bare_string_fallback():
    assert parse_kw(["label=hello"]) == {"label": "hello"}


def test_parse_kw_value_with_equals():
    assert parse_kw(["expr=a=b"]) == {"expr": "a=b"}


def test_parse_kw_missing_equals_raises():
    with pytest.raises(ValueError):
        parse_kw(["novalue"])
