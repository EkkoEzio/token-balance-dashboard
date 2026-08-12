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
PLAN_URL = "https://www.minimaxi.com/backend/account/resource_package_plan"

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
    # 极速版标志:weekly_boost_permille ≥1500 = 1.5x 加速(极速版)
    boost = general.get("weekly_boost_permille", 1000) if general else 1000

    return {
        "level": "",  # 由 provider 层填(接口本身不返回套餐名)
        "windows": windows,
        "has_video": has_video,
        "boost_permille": boost,
    }


class MiniMaxProvider(Provider):
    key = "minimax"
    name = "MiniMax Token Plan"
    refresh_interval = 600

    def _get_cookie(self) -> str:
        """可选:登录 cookie(用于拿会员等级)。没填返回空串。"""
        return config.get_api_keys().get("minimax_cookie", "").strip()

    def _fetch_plan_level(self, cookie: str) -> str:
        """从 resource_package_plan 接口拿会员等级(需登录 cookie)。
        失败返回空串(降级为 boost 推断)。"""
        if not cookie:
            return ""
        try:
            resp = _http_get(
                PLAN_URL,
                headers={"Cookie": cookie,
                         "Origin": "https://platform.minimaxi.com",
                         "Referer": "https://platform.minimaxi.com/console/usage",
                         "User-Agent": "Mozilla/5.0"},
                timeout=12,
            )
            if resp.status_code != 200:
                return ""
            data = resp.json()
            # 兼容两种可能结构:data 直接是包信息 或 data.data
            inner = data.get("data") if isinstance(data.get("data"), dict) else data
            # 找套餐名:常见字段 plan_name / package_name / name / plan_type
            for f in ("plan_name", "package_name", "name", "plan_type", "product_name"):
                if isinstance(inner, dict) and inner.get(f):
                    return str(inner[f])
        except Exception:
            pass
        return ""

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

            # 等级:优先 cookie 接口,失败回退 boost 推断
            level = self._fetch_plan_level(self._get_cookie())
            if not level:
                level = self._infer_level(data)
            data["level"] = level
            return self._wrap(STATUS_OK, data)
        except Exception as e:
            return self.error(str(e), kind=classify_request_exc(e))

    def _infer_level(self, data: dict) -> str:
        """从 boost_permille 推断等级:≥1500 = 极速版(1.5x 加速)。"""
        boost = data.get("boost_permille", 1000)
        if boost >= 1500:
            return "极速版"
        return ""  # 标准版无明确标志,留空
