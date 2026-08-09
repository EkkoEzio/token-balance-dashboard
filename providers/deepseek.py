"""DeepSeek 余额查询。公开 API,GET /user/balance,Bearer 鉴权。"""
import requests
import config
from providers.base import Provider, STATUS_OK

BALANCE_URL = "https://api.deepseek.com/user/balance"

# 网络函数可被测试替换(沿用 B站 _http_get 模式)
_http_get = requests.get


def parse_balance(raw: dict) -> dict:
    """把 /user/balance 响应解析成展示数据。balance_infos 为空时给默认零值。"""
    infos = raw.get("balance_infos") or []
    info = infos[0] if infos else {}
    return {
        "is_available": raw.get("is_available", False),
        "total_balance": str(info.get("total_balance", "0")),
        "granted_balance": str(info.get("granted_balance", "0")),
        "topped_up_balance": str(info.get("topped_up_balance", "0")),
        "currency": info.get("currency", "CNY"),
    }


def _classify_exc(e) -> str:
    """把 requests 异常归类成 error kind。"""
    if isinstance(e, requests.exceptions.Timeout):
        return "network"
    if isinstance(e, requests.exceptions.ConnectionError):
        return "network"
    if isinstance(e, requests.exceptions.HTTPError):
        code = e.response.status_code if e.response is not None else 0
        if code in (401, 403):
            return "auth"
        if code == 429:
            return "rate_limit"
    return "unknown"


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
        except requests.exceptions.HTTPError as e:
            return self.error(str(e), kind=_classify_exc(e))
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
            return self.error(str(e), kind="network")
        except Exception as e:
            return self.error(str(e), kind="unknown")
