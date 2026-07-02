from plum import Source, autodiscover

import tests.example.methods
from tests.example.registries import METHODS


def test_autodiscover_registers_all_submodules():
    autodiscover(tests.example.methods)
    assert METHODS.names() == ["cube", "square"]


def test_display_name_defaults_to_empty():
    autodiscover(tests.example.methods)
    cube = METHODS.get("cube")
    square = METHODS.get("square")
    assert cube.display_name == "Cube (x³)"
    assert square.display_name == ""
    assert issubclass(cube, Source)
