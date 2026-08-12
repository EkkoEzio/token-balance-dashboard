"""MiniMax Token Plan 额度查询。
公开文档端点 /v1/token_plan/remains,Bearer 鉴权(需 sk-cp- 开头的 Subscription Key)。

接口返回 model_remains 数组,按模型分(general 文本/video 视频)。
取 general 模型,有 5h 和周两个窗口,新版只有 remaining_percent(count 恒为0)。
"""
from datetime import datetime, timezone
import requests
import config
from providers.base import Provider, STATUS_OK, classify_request_exc

REMAINS_URL = "https://api.minimaxi.com/v1/token_plan/remains"

_http_get = requests.get


def _ms_to_iso(ms) -> str:
    """毫秒时间戳转 ISO。"""
    try:
        n = int(ms)
        if n <= 0:
            return ""
        return datetime.fromtimestamp(n / 1000, tz=timezone.utc).isoformat()
    except (TypeError, ValueError):
        return ""


def parse_remains(raw: dict) -> dict:
    """把 /v1/token_plan/remains 响应解析成展示数据。
    从 model_remains 取 general 模型的 5h/周窗口(百分比制)。"""
    models = raw.get("model_remains") or []
    # 找 general(文本模型);video 是视频,默认忽略
    general = next((m for m in models if m.get("model_name") == "general"), None)
    if not general:
        # 兜底:取第一个
        general = models[0] if models else {}

    windows = []
    # 5 小时窗口
    remain5 = general.get("current_interval_remaining_percent")
    if remain5 is not None:
        used_pct = round(100 - remain5, 1)
        windows.append({
            "label": "5小时",
            "total": general.get("current_interval_total_count", 0),
            "used": general.get("current_interval_usage_count", 0),
            "remaining": max(0, remain5),
            "percentage": used_pct,
            "reset_at": _ms_to_iso(general.get("end_time")),
            "unit": "%" if not general.get("current_interval_total_count") else "次",
        })
    # 周窗口
    remain_w = general.get("current_weekly_remaining_percent")
    if remain_w is not None:
        used_pct = round(100 - remain_w, 1)
        windows.append({
            "label": "7天",
            "total": general.get("current_weekly_total_count", 0),
            "used": general.get("current_weekly_usage_count", 0),
            "remaining": max(0, remain_w),
            "percentage": used_pct,
            "reset_at": _ms_to_iso(general.get("weekly_end_time")),
            "unit": "%" if not general.get("current_weekly_total_count") else "次",
        })

    # 是否有 video 模型(额外提示)
    has_video = any(m.get("model_name") == "video" for m in models)

    return {
        "level": "",  # 由 provider 层填(接口本身不返回套餐名)
        "windows": windows,
        "has_video": has_video,
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
            # 注:套餐名需登录态接口,API Key 拿不到,level 留空不显示
            return self._wrap(STATUS_OK, data)
        except Exception as e:
            return self.error(str(e), kind=classify_request_exc(e))
