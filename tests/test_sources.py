from plum import Source, autodiscover

import tests.example.greeters
from tests.example.registries import GREETERS


def test_autodiscover_registers_all_submodules():
    autodiscover(tests.example.greeters)
    assert GREETERS.names() == ["english", "pirate"]


def test_display_name_defaults_to_empty():
    autodiscover(tests.example.greeters)
    english = GREETERS.get("english")
    pirate = GREETERS.get("pirate")
    assert english.display_name == "English"
    assert pirate.display_name == ""
    assert issubclass(english, Source)
