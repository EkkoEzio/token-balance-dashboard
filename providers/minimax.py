"""MiniMax Token Plan 额度查询。
公开文档端点 /v1/token_plan/remains,Bearer 鉴权(需 sk-cp- 开头的 Subscription Key)。
注意:接口存在新旧两版字段漂移,本 provider 同时兼容:
- 旧版:current_interval_total_count / current_interval_usage_count / current_interval_reset_time
        current_weekly_total_count / current_weekly_usage_count / current_weekly_reset_time
- 新版:current_interval_remaining_percent / current_weekly_remaining_percent(旧 count 字段恒为0)
"""
from datetime import datetime, timezone
import requests
import config
from providers.base import Provider, STATUS_OK, classify_request_exc

REMAINS_URL = "https://api.minimaxi.com/v1/token_plan/remains"

_http_get = requests.get


def _ms_to_iso(ms) -> str:
    try:
        n = int(ms)
        if n <= 0:
            return ""
        # MiniMax 重置时间是秒级时间戳
        if n < 1e12:
            n = n * 1000
        return datetime.fromtimestamp(n / 1000, tz=timezone.utc).isoformat()
    except (TypeError, ValueError):
        return ""


def _build_window(label: str, data: dict, prefix: str) -> dict:
    """从响应数据构建一个窗口。prefix = 'current_interval_'(5h) 或 'current_weekly_'(周)。
    兼容新旧字段:优先用 count(total/used),为0时回退 remaining_percent。"""
    total = data.get(prefix + "total_count", 0)
    used = data.get(prefix + "usage_count", 0)
    reset = data.get(prefix + "reset_time", 0)
    remain_pct = data.get(prefix + "remaining_percent")
    # 新版:count 字段恒为0,用 remaining_percent 反推
    if (not total) and remain_pct is not None:
        # 只有百分比,无法反推绝对值;used 用 100-remain_pct 表示
        return {
            "label": label,
            "total": 0,  # 未知
            "used": round(100 - remain_pct, 1),  # 已用百分比
            "remaining": 0,
            "percentage": round(100 - remain_pct, 1),
            "reset_at": _ms_to_iso(reset),
            "unit": "%",  # 标记:这是百分比不是绝对值
        }
    # 旧版:有 count
    return {
        "label": label,
        "total": total,
        "used": used,
        "remaining": max(0, total - used),
        "percentage": round(used / total * 100, 1) if total else 0,
        "reset_at": _ms_to_iso(reset),
        "unit": "次",
    }


def parse_remains(raw: dict) -> dict:
    """把 /v1/token_plan/remains 响应解析成展示数据。
    兼容新旧版字段。data 可能在 raw.data 或 raw 本身。"""
    data = raw.get("data") if isinstance(raw.get("data"), dict) else raw
    if not isinstance(data, dict):
        data = {}
    windows = []
    w5 = _build_window("5小时", data, "current_interval_")
    ww = _build_window("7天", data, "current_weekly_")
    # 只添加有实际数据的窗口(avoid 全0空窗口)
    if w5["total"] or w5["percentage"] or w5["reset_at"]:
        windows.append(w5)
    if ww["total"] or ww["percentage"] or ww["reset_at"]:
        windows.append(ww)
    return {
        "level": data.get("plan_name") or data.get("plan_type") or "",
        "windows": windows,
    }


class MiniMaxProvider(Provider):
    key = "minimax"
    name = "MiniMax Token Plan"
    refresh_interval = 600

    def fetch(self) -> dict:
        keys = config.get_api_keys()
        key = keys.get("minimax")
        if not key:
            return self.unconfigured()
        try:
            resp = _http_get(
                REMAINS_URL,
                headers={"Authorization": f"Bearer {key}"},
                timeout=12,
            )
            # 401/403 = key 无效或非 Subscription Key
            if resp.status_code in (401, 403):
                return self.error(f"HTTP {resp.status_code}: Key 无效或非 Subscription Key(需 sk-cp- 开头)", kind="auth")
            resp.raise_for_status()
            data = parse_remains(resp.json())
            return self._wrap(STATUS_OK, data)
        except Exception as e:
            return self.error(str(e), kind=classify_request_exc(e))
