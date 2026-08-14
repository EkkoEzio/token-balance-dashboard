"""Kimi For Coding(月之暗面)订阅用量查询。
端点 /coding/v1/usages,Bearer 鉴权(无需登录态,同 cc-switch 实现)。
响应结构:
- limits[]: 5 小时窗口,每项 detail.{limit, remaining, resetTime}
- usage: 周限额,{limit, remaining, resetTime}
resetTime 兼容 ISO 字符串和秒/毫秒时间戳;已用 = limit - remaining。
"""
from datetime import datetime, timezone
import requests
import config
from providers.base import Provider, STATUS_OK, classify_request_exc

USAGES_URL = "https://api.kimi.com/coding/v1/usages"

_http_get = requests.get


def _num(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _parse_reset(v) -> str:
    """resetTime 兼容:ISO 字符串直传;数字按秒(<1e12)/毫秒判别转 ISO;<=0 视为无。"""
    if v is None:
        return ""
    if isinstance(v, str):
        return v
    n = _num(v)
    if n <= 0:
        return ""
    if n < 1_000_000_000_000:  # 秒级时间戳
        n *= 1000
    return datetime.fromtimestamp(n / 1000, tz=timezone.utc).isoformat()


def _window(label: str, limit: float, remaining: float, reset_v) -> dict:
    used = max(0.0, limit - remaining)
    remaining = max(0.0, remaining)
    pct = round(used / limit * 100, 1) if limit > 0 else 0
    return {
        "label": label,
        "unit": "次",
        "total": int(limit),
        "used": int(used),
        "remaining": int(remaining),
        "percentage": pct,
        "reset_at": _parse_reset(reset_v),
    }


def parse_usages(raw: dict) -> dict:
    """把 /coding/v1/usages 响应解析成展示数据(windows 数组,智谱/千问同款)。
    limits[] 每项一个 5 小时窗口(通常 1 项);usage 为周限额。"""
    windows = []
    for item in raw.get("limits") or []:
        d = item.get("detail") or {}
        windows.append(_window("5小时", _num(d.get("limit")), _num(d.get("remaining")),
                                d.get("resetTime")))
    usage = raw.get("usage") or {}
    if usage:
        windows.append(_window("7天", _num(usage.get("limit")), _num(usage.get("remaining")),
                               usage.get("resetTime")))
    return {"windows": windows}


class KimiProvider(Provider):
    key = "kimi"
    name = "Kimi For Coding"
    refresh_interval = 600  # 10 分钟,订阅窗口型同 MiniMax/千问

    def fetch(self) -> dict:
        keys = config.get_api_keys()
        key = keys.get("kimi")
        if not key:
            return self.unconfigured()
        try:
            resp = _http_get(
                USAGES_URL,
                headers={"Authorization": f"Bearer {key}"},
                timeout=15,
            )
            if resp.status_code in (401, 403):
                return self.error(f"HTTP {resp.status_code}: Key 无效(需 Kimi For Coding 的 API Key)", kind="auth")
            resp.raise_for_status()
            data = parse_usages(resp.json())
            if not data["windows"]:
                return self.error("响应无用量数据(limits/usage 均为空)", kind="unknown")
            return self._wrap(STATUS_OK, data)
        except Exception as e:
            return self.error(str(e), kind=classify_request_exc(e))
