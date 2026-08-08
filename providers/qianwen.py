"""千问 Token Plan。无公开 API,需走控制台内部接口 + Edge cookie。
一期占位:返回 unconfigured。抓包后补实现。"""
from providers.base import Provider, STATUS_UNCONFIGURED
from providers.base import _now_iso


class QianwenProvider(Provider):
    key = "qianwen"
    name = "千问 Token Plan"
    refresh_interval = 120

    def fetch(self) -> dict:
        # TODO(抓包后): 用 cookie_jar.get_cookiejar 读 Edge cookie 调控制台接口
        return {
            "key": self.key,
            "name": self.name,
            "status": STATUS_UNCONFIGURED,
            "data": {"note": "待抓包实现"},
            "updated_at": _now_iso(),
        }
