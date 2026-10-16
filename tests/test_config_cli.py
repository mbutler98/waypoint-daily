import subprocess
import sys

import pytest
import yaml
from pydantic import ValidationError

from waypoint.cli import NOT_IMPLEMENTED_EXIT, RENDER_FAILED_EXIT, main
from waypoint.config import DEFAULT_CONFIG_PATH, Config, load_config

COMMANDS = ["gather", "draft", "render", "publish", "refresh-token", "sample"]


def test_repo_config_loads_with_safe_defaults():
    cfg = load_config()
    assert cfg.dry_run is True
    assert cfg.require_approval is True
    assert cfg.timezone == "America/Toronto"


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("timezone", "Mars/Olympus"),
        ("draft_time", "25:00"),
        ("slots", {"am": "18:00", "pm": "08:00"}),
        ("faculties", {"Space": 1.0}),
        ("pages_base_url", "http://insecure.example/"),
        ("surprise_key", True),
    ],
)
def test_bad_config_rejected(key, value):
    data = yaml.safe_load(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))
    data[key] = value
    with pytest.raises(ValidationError):
        Config.model_validate(data)


def test_help_lists_all_commands():
    out = subprocess.run(
        [sys.executable, "-m", "waypoint", "--help"], capture_output=True, text=True, check=True
    ).stdout
    for cmd in COMMANDS:
        assert cmd in out


@pytest.mark.parametrize(
    "argv",
    [
        ["draft"], ["publish", "am"], ["refresh-token"],
    ],
)
def test_stubs_exit_not_implemented(argv, capsys):
    assert main(argv) == NOT_IMPLEMENTED_EXIT
    assert "not implemented" in capsys.readouterr().err


def test_render_without_post_json_fails_cleanly(tmp_path, capsys):
    assert main(["render", str(tmp_path)]) == RENDER_FAILED_EXIT
    assert "no post.json" in capsys.readouterr().err
