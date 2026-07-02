from __future__ import annotations

from typing import Callable, Generic, Iterator, TypeVar, overload

from plum.errors import DuplicateRegistration, UnknownName

T = TypeVar("T")


class Registry(Generic[T]):
    def __init__(self, kind: str, *, key: str = "id") -> None:
        self.kind = kind
        self.key = key
        self._items: dict[str, T] = {}

    @overload
    def register(self, obj: T, *, name: str | None = None) -> T: ...

    @overload
    def register(self, obj: None = None, *, name: str | None = None) -> Callable[[T], T]: ...

    def register(
        self, obj: T | None = None, *, name: str | None = None
    ) -> T | Callable[[T], T]:
        if obj is None:

            def decorator(target: T) -> T:
                self._add(target, name)
                return target

            return decorator
        self._add(obj, name)
        return obj

    def _add(self, obj: T, name: str | None) -> None:
        if name is not None:
            key = name
        else:
            try:
                key = getattr(obj, self.key)
            except AttributeError:
                raise TypeError(
                    f"cannot register {obj!r} in {self.kind} registry: "
                    f"no '{self.key}' attribute and no explicit name given"
                ) from None
        if not isinstance(key, str):
            raise TypeError(
                f"{self.kind} registry key must be a str; got {type(key).__name__} "
                f"from attribute '{self.key}' (define it as a class attribute, not a property)"
            )
        if key in self._items:
            raise DuplicateRegistration(self.kind, key)
        self._items[key] = obj

    def get(self, name: str) -> T:
        try:
            return self._items[name]
        except KeyError:
            raise UnknownName(self.kind, name, self.names()) from None

    def names(self) -> list[str]:
        return sorted(self._items)

    def items(self) -> list[tuple[str, T]]:
        return [(name, self._items[name]) for name in self.names()]

    def __contains__(self, name: object) -> bool:
        return name in self._items

    def __iter__(self) -> Iterator[T]:
        return iter(self._items.values())

    def __len__(self) -> int:
        return len(self._items)
