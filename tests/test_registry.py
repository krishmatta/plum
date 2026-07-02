import pytest

from plum import DuplicateRegistration, Registry, UnknownName


class Widget:
    def __init__(self, id: str) -> None:
        self.id = id


def test_register_and_get():
    reg: Registry[Widget] = Registry("widget")
    w = Widget("a")
    assert reg.register(w) is w
    assert reg.get("a") is w


def test_register_explicit_name():
    reg: Registry[Widget] = Registry("widget")
    w = Widget("ignored")
    reg.register(w, name="custom")
    assert reg.get("custom") is w
    assert "ignored" not in reg


def test_register_as_decorator():
    reg: Registry[type] = Registry("thing", key="__name__")

    @reg.register
    class Foo:
        pass

    assert reg.get("Foo") is Foo


def test_decorator_with_name():
    reg: Registry[type] = Registry("thing")

    @reg.register(name="bar")
    class Foo:
        pass

    assert reg.get("bar") is Foo


def test_custom_key():
    reg: Registry[Widget] = Registry("widget", key="id")
    reg.register(Widget("x"))
    assert "x" in reg


def test_duplicate_raises():
    reg: Registry[Widget] = Registry("widget")
    reg.register(Widget("dup"))
    with pytest.raises(DuplicateRegistration):
        reg.register(Widget("dup"))


def test_unknown_raises_with_known_names():
    reg: Registry[Widget] = Registry("widget")
    reg.register(Widget("a"))
    reg.register(Widget("b"))
    with pytest.raises(UnknownName) as exc:
        reg.get("missing")
    assert exc.value.known == ["a", "b"]


def test_missing_key_attribute_raises():
    class NoId:
        pass

    reg: Registry[NoId] = Registry("thing")
    with pytest.raises(TypeError, match="no 'id' attribute"):
        reg.register(NoId())


def test_items_sorted_by_name():
    reg: Registry[Widget] = Registry("widget")
    b, a = Widget("b"), Widget("a")
    reg.register(b)
    reg.register(a)
    assert reg.items() == [("a", a), ("b", b)]


def test_non_str_key_raises():
    class Bad:
        id = 123

    reg: Registry[Bad] = Registry("bad")
    with pytest.raises(TypeError):
        reg.register(Bad())


def test_iter_len_contains_names():
    reg: Registry[Widget] = Registry("widget")
    reg.register(Widget("b"))
    reg.register(Widget("a"))
    assert len(reg) == 2
    assert "a" in reg
    assert reg.names() == ["a", "b"]
    assert {w.id for w in reg} == {"a", "b"}
