"""Provider 基类:各家适配器的最小契约。
不强统一字段,每家 fetch 返回自己的原生数据。"""
from datetime import datetime, timezone


STATUS_OK = "ok"
STATUS_UNCONFIGURED = "unconfigured"
STATUS_EXPIRED = "expired"
STATUS_ERROR = "error"

# 错误分类 → 人话文案。kind 决定前端配色(红/灰/橙)。
ERROR_KINDS = {
    "auth":       "Key 失效或无权限,请重新填写",
    "expired":    "Key 已过期,请重新生成",
    "network":    "网络超时或连接失败,稍后重试",
    "rate_limit": "请求过频被限流,稍后自动重试",
    "unknown":    "查询失败",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class Provider:
    """子类需设置 key/name/refresh_interval,并实现 fetch()。"""
    key: str = ""
    name: str = ""
    refresh_interval: int = 300  # 秒,子类可覆盖

    def fetch(self) -> dict:
        """子类实现。返回 _wrap(status, data) 或 unconfigured()/error(e)。"""
        raise NotImplementedError

    def _wrap(self, status: str, data: dict) -> dict:
        """统一外层壳:每家数据塞进 data,各家自由。"""
        return {
            "key": self.key,
            "name": self.name,
            "status": status,
            "data": data,
            "updated_at": _now_iso(),
        }

    def unconfigured(self) -> dict:
        return {
            "key": self.key,
            "name": self.name,
            "status": STATUS_UNCONFIGURED,
            "data": {},
            "updated_at": _now_iso(),
        }

    def error(self, message: str, kind: str = "unknown") -> dict:
        """返回错误结果。message=原始信息(存 detail),kind=分类码(映射人话)。
        前端显示 error(人话);error_detail 供调试。"""
        return {
            "key": self.key,
            "name": self.name,
            "status": STATUS_ERROR,
            "data": {},
            "error": ERROR_KINDS.get(kind, ERROR_KINDS["unknown"]),
            "error_kind": kind,
            "error_detail": message,
            "updated_at": _now_iso(),
        }
