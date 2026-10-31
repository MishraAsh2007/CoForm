import copy

import pytest
import yaml

from cad.common import ROOT, load_config
from cad.spec import default_spec, validate
from checks.fit_rules import rules


@pytest.fixture(scope="session")
def cfg():
    return load_config()


@pytest.fixture(scope="session")
def brief():
    with open(ROOT / "benchmark" / "briefs" / "a_pi5_standard.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


@pytest.fixture(scope="session")
def good_spec(cfg):
    """v0 generic design + the GPIO ribbon slot: the known-good reference."""
    spec = default_spec("rpi5_cooler", rules(cfg, "generic"), cfg)
    for s in spec["cable_slots"]:
        if s["name"] == "gpio_ribbon":
            s["enabled"] = True
    return validate(spec)


@pytest.fixture
def spec(good_spec):
    return copy.deepcopy(good_spec)


def codes(result):
    return {f["code"] for f in result["failures"]}
