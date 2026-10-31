import pytest

from cad.common import load_config


@pytest.fixture(scope="session")
def cfg():
    return load_config()
