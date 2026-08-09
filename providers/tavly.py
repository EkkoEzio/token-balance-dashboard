"""Tavly 用量查询。公开 API,GET /usage,Bearer 鉴权。
支持多 key(逗号分隔),同卡片展示多个账号额度。每月1号重置。"""
import requests
import config
from providers.base import Provider, STATUS_OK, ERROR_KINDS

USAGE_URL = "https://api.tavly.com/usage"

_http_get = requests.get


def _classify_tavly_exc(e) -> str:
    """把单个 key 的异常归类成人话文案(供卡片内展示)。"""
    if isinstance(e, (requests.exceptions.Timeout, requests.exceptions.ConnectionError)):
        return ERROR_KINDS["network"]
    if isinstance(e, requests.exceptions.HTTPError):
        code = e.response.status_code if e.response is not None else 0
        if code in (401, 403):
            return ERROR_KINDS["auth"]
        if code == 429:
            return ERROR_KINDS["rate_limit"]
    return ERROR_KINDS["unknown"]


def parse_usage(raw: dict) -> dict:
    """把 /usage 响应解析成单个账号的展示数据。
    remaining = limit - usage(limit 为 null 视为无限)。"""
    account = raw.get("account") or {}
    plan_usage = account.get("plan_usage", 0)
    plan_limit = account.get("plan_limit")
    remaining = (plan_limit - plan_usage) if plan_limit is not None else None
    return {
        "plan": account.get("current_plan", ""),
        "used": plan_usage,
        "total": plan_limit,  # None = 无限
        "remaining": remaining,
        "reset_note": "每月1号重置",
    }


class TavlyProvider(Provider):
    key = "tavly"
    name = "Tavly"
    refresh_interval = 300  # 5 分钟

    def _get_keys(self) -> list:
        """返回 tavly 的 key 列表(支持逗号分隔多 key)。"""
        val = config.get_api_keys().get("tavly", "")
        if not val:
            return []
        # 支持逗号或换行分隔多个 key
        return [k.strip() for k in val.replace("\n", ",").split(",") if k.strip()]

    def fetch(self) -> dict:
        keys = self._get_keys()
        if not keys:
            return self.unconfigured()
        accounts = []
        errors = []
        for i, key in enumerate(keys, 1):
            label = f"账号{i}" if len(keys) > 1 else ""
            try:
                resp = _http_get(
                    USAGE_URL,
                    headers={"Authorization": f"Bearer {key}"},
                    timeout=15,
                )
                resp.raise_for_status()
                data = parse_usage(resp.json())
                data["label"] = label
                accounts.append(data)
            except Exception as e:
                # 归类成人话(卡片内展示)
                human = _classify_tavly_exc(e)
                errors.append(f"{label or '账号'}: {human}")
        if not accounts:
            # 全部失败:用第一个错误的 kind 定主状态
            kind = "unknown"
            return self.error("; ".join(errors) or "查询失败", kind=kind)
        data = {"accounts": accounts}
        if errors:
            data["errors"] = errors
        return self._wrap(STATUS_OK, data)
