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


# ---------- 卡片排序 ----------
def get_order() -> list:
    """返回卡片展示顺序(provider key 列表)。未配置时返回空(用默认顺序)。"""
    return _read().get("order", [])


def set_order(order: list) -> dict:
    """保存卡片顺序。必须是合法 provider key 列表。"""
    with _lock:
        cur = _read()
        cur["order"] = list(order)
        try:
            _write(cur)
        except Exception as e:
            return {"ok": False, "error": str(e)}
    return {"ok": True}


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


# ---------- 告警配置 ----------
_DEFAULT_ALERTS = {"enabled": True, "threshold_balance": 10, "threshold_pct": 20}


def get_alerts_config() -> dict:
    """返回告警配置(开关 + 阈值)。缺失项用默认补全。"""
    cur = _read().get("alerts", {})
    return {**_DEFAULT_ALERTS, **cur}


def set_alerts_config(patch: dict) -> dict:
    """部分更新告警配置。"""
    with _lock:
        cur = _read()
        merged = {**_DEFAULT_ALERTS, **cur.get("alerts", {})}
        if "enabled" in patch:
            merged["enabled"] = bool(patch["enabled"])
        if "threshold_balance" in patch:
            try:
                merged["threshold_balance"] = max(0, float(patch["threshold_balance"]))
            except (TypeError, ValueError):
                return {"ok": False, "error": "余额阈值需为数字"}
        if "threshold_pct" in patch:
            try:
                v = int(patch["threshold_pct"])
                if not 1 <= v <= 99:
                    return {"ok": False, "error": "百分比阈值需在 1-99"}
                merged["threshold_pct"] = v
            except (TypeError, ValueError):
                return {"ok": False, "error": "百分比阈值需为整数"}
        cur["alerts"] = merged
        try:
            _write(cur)
        except Exception as e:
            return {"ok": False, "error": str(e)}
    return {"ok": True, **merged}


# ---------- 主密码(复制 Key 时验证身份,不存明文) ----------
import hashlib
import secrets

_PBKDF2_ITERS = 200_000


def _hash_password(password: str) -> dict:
    """PBKDF2 哈希。返回 {salt, hash, iters}。"""
    salt = secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                            bytes.fromhex(salt), _PBKDF2_ITERS).hex()
    return {"salt": salt, "hash": h, "iters": _PBKDF2_ITERS}


def has_master_password() -> bool:
    """是否已设置主密码。"""
    return bool(_read().get("master_password"))


def set_master_password(password: str) -> dict:
    """设置/修改主密码。存哈希不存明文。"""
    if not password or len(password) < 4:
        return {"ok": False, "error": "密码至少 4 位"}
    with _lock:
        cur = _read()
        cur["master_password"] = _hash_password(password)
        try:
            _write(cur)
        except Exception as e:
            return {"ok": False, "error": str(e)}
    return {"ok": True}


def check_master_password(password: str) -> bool:
    """校验主密码。未设置或错误返回 False。"""
    stored = _read().get("master_password")
    if not stored:
        return False
    try:
        h = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                                bytes.fromhex(stored["salt"]),
                                stored.get("iters", _PBKDF2_ITERS)).hex()
        return secrets.compare_digest(h, stored["hash"])
    except Exception:
        return False
