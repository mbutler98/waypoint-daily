import subprocess
import sys

import pytest
import yaml
from pydantic import ValidationError

from waypoint.cli import NOT_IMPLEMENTED_EXIT, RENDER_FAILED_EXIT, main
from waypoint.config import DEFAULT_CONFIG_PATH, Config, load_config

COMMANDS = ["gather", "draft", "write", "render", "publish", "refresh-token", "sample"]


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
        ["publish", "am"], ["refresh-token"],
    ],
)
def test_stubs_exit_not_implemented(argv, capsys):
    assert main(argv) == NOT_IMPLEMENTED_EXIT
    assert "not implemented" in capsys.readouterr().err


def test_draft_without_key_fails_before_any_network(monkeypatch, tmp_path, capsys):
    import waypoint.state

    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(waypoint.state, "load_dotenv", lambda *a, **k: None)
    assert main(["draft", "--date", "2026-10-17", "--out", str(tmp_path)]) == 1
    assert "GEMINI_API_KEY is not set" in capsys.readouterr().err
    assert list(tmp_path.iterdir()) == []


def test_render_without_post_json_fails_cleanly(tmp_path, capsys):
    assert main(["render", str(tmp_path)]) == RENDER_FAILED_EXIT
    assert "no post.json" in capsys.readouterr().err
