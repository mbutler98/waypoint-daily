import pytest

from waypoint.config import load_config


@pytest.fixture
def cfg():
    return load_config()
