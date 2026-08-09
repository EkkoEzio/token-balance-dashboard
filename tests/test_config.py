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


def test_disabled_default_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    assert config.get_disabled() == set()


def test_set_disabled(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    assert config.set_disabled("minimax", True)["ok"] is True
    assert config.get_disabled() == {"minimax"}
    # 再关一个
    assert config.set_disabled("qianwen", True)["ok"] is True
    assert config.get_disabled() == {"minimax", "qianwen"}
    # 取消关闭
    assert config.set_disabled("minimax", False)["ok"] is True
    assert config.get_disabled() == {"qianwen"}
    # 持久化
    raw = json.loads((tmp_path / "config.json").read_text("utf-8"))
    assert raw["disabled"] == ["qianwen"]


def test_master_password_set_and_check(tmp_path, monkeypatch):
    """设置主密码后,正确密码校验通过,错误密码失败。"""
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    assert config.has_master_password() is False
    assert config.set_master_password("mypass123")["ok"] is True
    assert config.has_master_password() is True
    assert config.check_master_password("mypass123") is True
    assert config.check_master_password("wrong") is False
    # 明文不落盘(只存哈希)
    raw = json.loads((tmp_path / "config.json").read_text("utf-8"))
    assert "mypass123" not in json.dumps(raw)
    assert "master_password" in raw  # 存的是 {salt, hash}


def test_change_master_password(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    config.set_master_password("oldpass")
    config.set_master_password("newpass")
    assert config.check_master_password("oldpass") is False
    assert config.check_master_password("newpass") is True
