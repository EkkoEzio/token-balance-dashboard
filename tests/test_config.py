import json
from pathlib import Path
import config


def test_get_api_keys_default_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    assert config.get_api_keys() == {}


def test_set_and_get_api_key(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    r = config.set_api_key("deepseek", "sk-abc123")
    assert r["ok"] is True
    assert config.get_api_keys() == {"deepseek": "sk-abc123"}
    # 持久化
    raw = json.loads((tmp_path / "config.json").read_text("utf-8"))
    assert raw["api_keys"]["deepseek"] == "sk-abc123"


def test_set_theme_validates(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    assert config.set_theme("dark")["ok"] is True
    assert config.set_theme("purple")["ok"] is False
    assert config.get_theme() == "dark"
