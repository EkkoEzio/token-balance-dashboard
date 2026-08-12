"""千问 Token Plan 额度查询。
无公开 API,走控制台内部接口 cs-data.qianwenai.com。
鉴权靠 sec_token + 登录 cookie(阿里 SSO 票据),需用户从浏览器抓取填入。

调三个接口组合完整额度:
- subscription: 套餐档位/剩余天数/到期时间
- usage: 周已用百分比 + 重置时间
- quota-config: 各档位额度上限(反推绝对剩余 Credits)
"""
from datetime import datetime, timezone
import requests
import config
from providers.base import Provider, STATUS_OK, classify_request_exc

API_URL = "https://cs-data.qianwenai.com/data/api.json"
_ORIGIN = "https://platform.qianwenai.com.com"
_REFERER = "https://platform.qianwenai.com/home/billing/subscription/token-plan-individual"
_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36")
_CORNERSTONE = {"domain": "platform.qianwenai.com", "consoleSite": "QIANWENAI",
                "console": "ONE_CONSOLE", "xsp_lang": "zh-CN",
                "protocol": "V2", "productCode": "p_efm"}

_http_post = requests.post


def _ms_to_iso(ms) -> str:
    try:
        n = int(ms)
        if n <= 0:
            return ""
        return datetime.fromtimestamp(n / 1000, tz=timezone.utc).isoformat()
    except (TypeError, ValueError):
        return ""


def _call(api_path: str, sec_token: str, cookie: str) -> dict | None:
    """调单个千问控制台接口。返回最内层 data;失败(登录态失效)返回 None。"""
    headers = {
        "accept": "application/json, text/plain, */*",
        "content-type": "application/x-www-form-urlencoded",
        "origin": "https://platform.qianwenai.com",
        "referer": _REFERER,
        "user-agent": _UA,
        "cookie": cookie,
    }
    body = {
        "product": "sfm_bailian",
        "action": "BroadScopeAspnGateway",
        "sec_token": sec_token,
        "region": "cn-beijing",
        "params": __import__("json").dumps({
            "Api": api_path, "Data": {"cornerstoneParam": _CORNERSTONE}, "V": "1.0"}),
    }
    resp = _http_post(API_URL, params={"product": "sfm_bailian", "action": "BroadScopeAspnGateway",
                                       "api": api_path},
                      data=body, headers=headers, timeout=12)
    if "<!DOCTYPE" in resp.text or "<html" in resp.text[:50].lower():
        return None  # 被重定向到登录页 = 登录态失效
    return resp.json().get("data", {}).get("DataV2", {}).get("data", {}).get("data")


def parse_qianwen(usage: dict, subscription: dict, quota_config: dict) -> dict:
    """把三个接口的数据组合成展示数据。
    5小时窗口:平台当前限时取消(usage 不返回 5h 用量),按无限量展示一行;
    7天窗口:usage 给周已用百分比,quota-config 给上限反推绝对值。
    subscription 全空 = 登录态失效,不产窗口(走 error 态)。"""
    spec = subscription.get("specCode", "") if subscription else ""
    remaining_days = subscription.get("remainingDays") if subscription else None
    end_time = _ms_to_iso(subscription.get("endTime")) if subscription else ""
    status = subscription.get("status", "") if subscription else ""

    windows = []
    if subscription:  # 有套餐才展示窗口(全空 = 登录态失效)
        # 5小时窗口:当前限时取消 → 无限量
        windows.append({
            "label": "5小时", "unit": "积分",
            "total": None, "used": None, "remaining": None,
            "percentage": 0, "reset_at": "",
            "unlimited": True, "note": "限时取消",
        })
        if usage:
            used_pct = usage.get("per1WeekPercentage", 0)
            reset_ms = usage.get("per1WeekResetTime", 0)
            total = int(quota_config.get(spec, {}).get("weekly", 0)) if (spec and quota_config) else 0
            used = round(total * used_pct) if total else 0
            remaining = total - used if total else 0
            windows.append({
                "label": "7天", "unit": "积分",
                "total": total, "used": used, "remaining": remaining,
                "percentage": round(used_pct * 100, 1),
                "reset_at": _ms_to_iso(reset_ms),
            })

    return {
        "level": spec,
        "remaining_days": remaining_days,
        "expires_at": end_time,
        "status": status,
        "windows": windows,
    }


class QianwenProvider(Provider):
    key = "qianwen"
    name = "千问 Token Plan"
    refresh_interval = 600

    def fetch(self) -> dict:
        keys = config.get_api_keys()
        sec_token = keys.get("qianwen_sec_token", "").strip()
        cookie = keys.get("qianwen_cookie", "").strip()
        if not sec_token or not cookie:
            return self.unconfigured()
        try:
            usage = _call("zeldaHttp.apikeyMgr./tokenplan/personal/api/v2/usage", sec_token, cookie)
            subscription = _call("zeldaHttp.apikeyMgr./tokenplan/personal/api/v2/subscription", sec_token, cookie)
            quota_config = _call("zeldaHttp.apikeyMgr./tokenplan/personal/api/v2/quota-config", sec_token, cookie)
            # 三个都为 None = 登录态失效
            if usage is None and subscription is None:
                return self.error("登录态失效,请重新抓取 sec_token 和 cookie", kind="expired")
            data = parse_qianwen(usage or {}, subscription or {}, quota_config or {})
            return self._wrap(STATUS_OK, data)
        except Exception as e:
            return self.error(str(e), kind=classify_request_exc(e))
