"""MiniMax Token Plan。接口鉴权有矛盾,字段漂移。
一期占位:返回 unconfigured。抓包后补实现。"""
from providers.base import Provider, STATUS_UNCONFIGURED
from providers.base import _now_iso


class MiniMaxProvider(Provider):
    key = "minimax"
    name = "MiniMax Token Plan"
    refresh_interval = 120

    def fetch(self) -> dict:
        # TODO(抓包后): 试 /v1/token_plan/remains 和 /v1/api/openplatform/coding_plan/remains
        return {
            "key": self.key,
            "name": self.name,
            "status": STATUS_UNCONFIGURED,
            "data": {"note": "待抓包实现"},
            "updated_at": _now_iso(),
        }
