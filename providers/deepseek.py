"""DeepSeek 余额查询。公开 API,GET /user/balance,Bearer 鉴权。"""
import requests
import config
from providers.base import Provider, STATUS_OK, classify_request_exc

BALANCE_URL = "https://api.deepseek.com/user/balance"

# 网络函数可被测试替换(沿用 B站 _http_get 模式)
_http_get = requests.get


def parse_balance(raw: dict) -> dict:
    """把 /user/balance 响应解析成展示数据。

    关键:balance_infos 为空且 is_available=true 时抛 ValueError(fetch 走 error 分支),
    而不是静默归零 —— 否则 API 偶发异常会被当成「真实余额 0」,
    触发「余额仅剩 ¥0.00」误报通知。
    多币种时优先取 CNY(用户按人民币充值),避免 USD 0 排在前误显示。
    """
    infos = raw.get("balance_infos") or []
    is_available = raw.get("is_available", False)
    if not infos:
        # is_available=false(真实余额不足)且无余额信息 → 合法零值状态
        if is_available is False:
            return {
                "is_available": False,
                "total_balance": "0",
                "granted_balance": "0",
                "topped_up_balance": "0",
                "currency": "CNY",
            }
        # is_available=true/缺失却无余额信息 → API 异常,禁止当成余额 0
        raise ValueError("balance_infos 为空或缺失")
    # 多币种优先 CNY;没有 CNY 才取第一条
    info = next((i for i in infos if i.get("currency") == "CNY"), infos[0])
    return {
        "is_available": is_available,
        "total_balance": str(info.get("total_balance", "0")),
        "granted_balance": str(info.get("granted_balance", "0")),
        "topped_up_balance": str(info.get("topped_up_balance", "0")),
        "currency": info.get("currency", "CNY"),
    }


class DeepSeekProvider(Provider):
    key = "deepseek"
    name = "DeepSeek"
    refresh_interval = 300  # 5 分钟

    def fetch(self) -> dict:
        keys = config.get_api_keys()
        key = keys.get("deepseek")
        if not key:
            return self.unconfigured()
        try:
            resp = _http_get(
                BALANCE_URL,
                headers={"Authorization": f"Bearer {key}"},
                timeout=10,
            )
            resp.raise_for_status()
            data = parse_balance(resp.json())
            return self._wrap(STATUS_OK, data)
        except Exception as e:
            return self.error(str(e), kind=classify_request_exc(e))
