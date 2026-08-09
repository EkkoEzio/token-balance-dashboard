"""路径与配置。所有模块统一从这里取路径,避免硬编码。
沿用 B站项目模式:运行时可变配置用 config.XXX 实时读。
"""
import json
import threading
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

CONFIG_FILE = DATA_DIR / "config.json"
PORT = 5070  # 不能用 5060:Chrome/Edge 把它列为受限端口(SIP),浏览器 ERR_UNSAFE_PORT

_DEFAULT_THEME = "auto"
VALID_THEMES = ("light", "dark", "auto")


def _read() -> dict:
    """读完整 config.json(没有文件返回空字典)。"""
    if not CONFIG_FILE.exists():
        return {}
    try:
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write(d: dict) -> None:
    CONFIG_FILE.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")


_lock = threading.Lock()


def get_api_keys() -> dict:
    """返回所有 provider 的 API Key。"""
    return _read().get("api_keys", {})


def set_api_key(provider: str, key: str) -> dict:
    """设置某 provider 的 API Key,持久化。"""
    with _lock:
        cur = _read()
        cur.setdefault("api_keys", {})[provider] = key.strip()
        try:
            _write(cur)
        except Exception as e:
            return {"ok": False, "error": str(e)}
    return {"ok": True}


def get_theme() -> str:
    t = _read().get("theme", _DEFAULT_THEME)
    return t if t in VALID_THEMES else _DEFAULT_THEME


def set_theme(theme: str) -> dict:
    if theme not in VALID_THEMES:
        return {"ok": False, "error": "主题无效(light/dark/auto)"}
    with _lock:
        cur = _read()
        cur["theme"] = theme
        try:
            _write(cur)
        except Exception as e:
            return {"ok": False, "error": str(e)}
    return {"ok": True}


def get_disabled() -> set:
    """返回被关闭的 provider key 集合。关闭 = 不刷新、不展示。"""
    return set(_read().get("disabled", []))


def set_disabled(provider: str, disabled: bool) -> dict:
    """设置某 provider 是否关闭。"""
    with _lock:
        cur = _read()
        lst = set(cur.get("disabled", []))
        if disabled:
            lst.add(provider)
        else:
            lst.discard(provider)
        cur["disabled"] = sorted(lst)
        try:
            _write(cur)
        except Exception as e:
            return {"ok": False, "error": str(e)}
    return {"ok": True}
