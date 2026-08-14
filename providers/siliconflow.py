"""硅基流动 SiliconFlow 余额查询。
官方文档端点 /v1/user/info,Bearer 鉴权,返回充值/总余额(含赠送)。
输出映射成 DeepSeek 同款数据形状,前端复用余额型卡片排版。
"""
import requests
import config
from providers.base import Provider, STATUS_OK, classify_request_exc

INFO_URL = "https://api.siliconflow.cn/v1/user/info"

_http_get = requests.get


def _num(v) -> float:
    """字段值兼容数字/字符串(cc-switch 实测两种都出现过)。"""
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def parse_user_info(raw: dict) -> dict:
    """把 /v1/user/info 响应解析成 DeepSeek 同款展示数据。
    data 缺失视为接口异常(抛错走 error,不静默归零,防止误报余额不足)。
    totalBalance=总余额(充值+赠送),chargeBalance=充值余额;
    不用 balance 字段(社区反馈其语义不稳,曾致显示负数)。"""
    data = raw.get("data")
    if not isinstance(data, dict) or "totalBalance" not in data:
        raise ValueError("响应缺少 data.totalBalance 字段")
    total = _num(data.get("totalBalance"))
    charge = _num(data.get("chargeBalance"))
    granted = max(0.0, round(total - charge, 2))  # 赠送 = 总 - 充值,异常钳为 0
    return {
        "is_available": True,
        "total_balance": f"{total:.2f}",
        "topped_up_balance": f"{charge:.2f}",
        "granted_balance": f"{granted:.2f}",
        "currency": "CNY",
    }


class SiliconFlowProvider(Provider):
    key = "siliconflow"
    name = "SiliconFlow"
    refresh_interval = 300  # 5 分钟,余额型同 DeepSeek

    def fetch(self) -> dict:
        keys = config.get_api_keys()
        key = keys.get("siliconflow")
        if not key:
            return self.unconfigured()
        try:
            resp = _http_get(
                INFO_URL,
                headers={"Authorization": f"Bearer {key}"},
                timeout=10,
            )
            if resp.status_code in (401, 403):
                return self.error(f"HTTP {resp.status_code}: Key 无效或无权限", kind="auth")
            resp.raise_for_status()
            data = parse_user_info(resp.json())
            return self._wrap(STATUS_OK, data)
        except Exception as e:
            return self.error(str(e), kind=classify_request_exc(e))
