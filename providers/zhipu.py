"""智谱 BigModel Coding Plan 额度查询。
内部接口 /api/monitor/usage/quota/limit,API Key 鉴权(不加 Bearer)。
unit 字段区分窗口:3=5小时,6=周。"""
import requests
import config
from providers.base import Provider, STATUS_OK

QUOTA_URL = "https://open.bigmodel.cn/api/monitor/usage/quota/limit"
UNIT_5H = 3
UNIT_WEEK = 6
_UNIT_LABEL = {UNIT_5H: "5小时", UNIT_WEEK: "7天"}

_http_get = requests.get


def parse_quota(raw: dict) -> dict:
    """把 monitor 接口响应解析成展示数据。
    过滤 type==TOKENS_LIMIT,按 unit 区分窗口。"""
    data = raw.get("data") or {}
    windows = []
    for lim in data.get("limits") or []:
        if lim.get("type") != "TOKENS_LIMIT":
            continue
        unit = lim.get("unit")
        windows.append({
            "label": _UNIT_LABEL.get(unit, f"窗口{unit}"),
            "total": lim.get("usage", 0),
            "used": lim.get("currentValue", 0),
            "remaining": lim.get("remaining", 0),
            "percentage": lim.get("percentage", 0),
            "reset_at": lim.get("nextResetTime", ""),
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
            # 智谱业务层错误码:HTTP 200 但 body code != 200(如 key 过期返回 code:401)
            if body.get("code") != 200:
                return self.error(body.get("msg", f"接口返回 code={body.get('code')}"))
            data = parse_quota(body)
            return self._wrap(STATUS_OK, data)
        except Exception as e:
            return self.error(f"查询失败: {e}")
