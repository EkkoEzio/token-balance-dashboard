"""Provider 基类:各家适配器的最小契约。
不强统一字段,每家 fetch 返回自己的原生数据。"""
from datetime import datetime, timezone


STATUS_OK = "ok"
STATUS_UNCONFIGURED = "unconfigured"
STATUS_EXPIRED = "expired"
STATUS_ERROR = "error"


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

    def error(self, message: str) -> dict:
        return {
            "key": self.key,
            "name": self.name,
            "status": STATUS_ERROR,
            "data": {},
            "error": message,
            "updated_at": _now_iso(),
        }
