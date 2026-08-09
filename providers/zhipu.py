"""智谱 BigModel Coding Plan 额度查询。
内部接口 /api/monitor/usage/quota/limit,API Key 鉴权(不加 Bearer)。
unit 字段区分窗口:3=5小时,6=周。nextResetTime 是毫秒时间戳。"""
from datetime import datetime, timezone
import requests
import config
from providers.base import Provider, STATUS_OK, STATUS_EXPIRED, _now_iso, classify_request_exc

QUOTA_URL = "https://open.bigmodel.cn/api/monitor/usage/quota/limit"
UNIT_5H = 3
UNIT_WEEK = 6
_UNIT_LABEL = {UNIT_5H: "5小时", UNIT_WEEK: "7天"}
# 接受所有 *_LIMIT 的额度类型(CREDIT_LIMIT=积分套餐;TOKENS_LIMIT 为旧版)
_QUOTA_TYPES = {"CREDIT_LIMIT", "TOKENS_LIMIT"}

_http_get = requests.get


def _ms_to_iso(ms) -> str:
    """毫秒时间戳转 ISO 字符串。0/空/异常返回空串。"""
    try:
        n = int(ms)
        if n <= 0:
            return ""
        return datetime.fromtimestamp(n / 1000, tz=timezone.utc).isoformat()
    except (TypeError, ValueError):
        return ""


def parse_quota(raw: dict) -> dict:
    """把 monitor 接口响应解析成展示数据。
    过滤 type ∈ {CREDIT_LIMIT, TOKENS_LIMIT},按 unit 区分窗口。
    nextResetTime 毫秒时间戳转 ISO。"""
    data = raw.get("data") or {}
    windows = []
    for lim in data.get("limits") or []:
        if lim.get("type") not in _QUOTA_TYPES:
            continue
        unit = lim.get("unit")
        windows.append({
            "label": _UNIT_LABEL.get(unit, f"窗口{unit}"),
            "total": lim.get("usage", 0),
            "used": lim.get("currentValue", 0),
            "remaining": lim.get("remaining", 0),
            "percentage": lim.get("percentage", 0),
            "reset_at": _ms_to_iso(lim.get("nextResetTime", 0)),
        })
    return {"level": data.get("level", ""), "windows": windows}


class ZhipuProvider(Provider):
    key = "zhipu"
    name = "智谱 Coding Plan"
    refresh_interval = 120  # 2 分钟,窗口型需更勤

    def fetch(self) -> dict:
        keys = config.get_api_keys()
        key = keys.get("zhipu")
        if not key:
            return self.unconfigured()
        try:
            resp = _http_get(
                QUOTA_URL,
                headers={"Authorization": key},  # 不加 Bearer
                timeout=10,
            )
            resp.raise_for_status()
            body = resp.json()
            # 智谱业务层错误码:HTTP 200 但 body code != 200
            code = body.get("code")
            if code != 200:
                msg = body.get("msg", f"接口返回 code={code}")
                if code == 401:
                    # key 过期/无效,用专门的 expired 状态
                    return {
                        "key": self.key, "name": self.name, "status": STATUS_EXPIRED,
                        "data": {}, "error": msg, "error_kind": "expired",
                        "error_detail": msg, "updated_at": _now_iso(),
                    }
                return self.error(msg, kind="auth")
            data = parse_quota(body)
            return self._wrap(STATUS_OK, data)
        except Exception as e:
            return self.error(str(e), kind=classify_request_exc(e))
