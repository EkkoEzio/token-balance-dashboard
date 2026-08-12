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

    # 周窗口有 boost(如 Max 1500 = 1.5x,总额度 100%+50%=150%)。
    # remaining_percent 是「剩余/基础额度」的百分比:
    #   剩余% = remaining_percent(相对基础),已用% = 100 - remaining_percent,总额 = 100 + boost加成
    boost = general.get("weekly_boost_permille", 1000) if general else 1000
    boost_extra = max(0, (boost - 1000) / 10)  # 1500 → +50

    windows = []
    # 5 小时窗口(无 boost 字段,默认 100% 基础)
    remain5 = general.get("current_interval_remaining_percent")
    if remain5 is not None:
        used_pct = round(100 - remain5, 1)
        windows.append({
            "label": "5小时",
            "total": 100,
            "used": used_pct,
            "remaining": max(0, remain5),
            "percentage": used_pct,
            "reset_at": _ms_to_iso(general.get("end_time")),
            "unit": "%",
        })
    # 周窗口(含 boost 加成)
    remain_w = general.get("current_weekly_remaining_percent")
    if remain_w is not None:
        used_pct = round(100 - remain_w, 1)
        total_pct = 100 + boost_extra  # 1500 → 150
        windows.append({
            "label": "7天",
            "total": total_pct,
            "used": used_pct,
            "remaining": round(total_pct - used_pct, 1),  # 150 - 2 = 148
            "percentage": used_pct,
            "reset_at": _ms_to_iso(general.get("weekly_end_time")),
            "unit": "%",
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
