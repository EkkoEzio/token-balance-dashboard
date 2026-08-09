"""定时拉取调度器。结果缓存内存,前端轮询读缓存。

刷新时机:
- 定时兜底:每 REFRESH_INTERVAL 秒(10 分钟)统一刷新全部,避免过频触发风控
- 配置变更:设置 key / 切换供应商时由 app 层主动调 refresh_now()
- 手动触发:用户点「刷新」调 /api/refresh → refresh_now()
"""
import threading
import time

from providers.deepseek import DeepSeekProvider
from providers.zhipu import ZhipuProvider
from providers.qianwen import QianwenProvider
from providers.minimax import MiniMaxProvider
from providers.tavly import TavlyProvider
import config

# 所有可用的 provider 类(顺序即展示顺序)
_ALL_CLASSES = [DeepSeekProvider, ZhipuProvider, TavlyProvider,
                QianwenProvider, MiniMaxProvider]

# 统一定时刷新间隔(秒)。10 分钟,避免过频触发各平台风控/限流。
REFRESH_INTERVAL = 600

_providers: dict = {}
_results: list = []
_last_refresh_ts: float = 0.0
_results_lock = threading.Lock()
_started = False


def _init_providers():
    """实例化未被关闭的 provider。关闭的 provider 完全跳过。"""
    global _providers
    disabled = config.get_disabled()
    _providers = {}
    for cls in _ALL_CLASSES:
        if cls.key in disabled:
            continue
        p = cls()
        _providers[p.key] = p


def refresh_now() -> list:
    """立刻拉取所有 provider,返回最新结果。记录刷新时间戳。"""
    global _results, _last_refresh_ts
    _init_providers()
    fresh = [p.fetch() for p in _providers.values()]
    with _results_lock:
        _results = fresh
        _last_refresh_ts = time.time()
    return fresh


def get_all() -> list:
    """返回缓存的最新结果。首次调用会触发一次 refresh。"""
    if not _results:
        refresh_now()
    with _results_lock:
        return list(_results)


def last_refresh_ts() -> float:
    """返回上次刷新的时间戳(供前端展示「X分钟前更新」)。"""
    return _last_refresh_ts


def _loop():
    """后台循环:每 REFRESH_INTERVAL 秒统一刷新全部 provider。"""
    global _results
    while True:
        time.sleep(REFRESH_INTERVAL)
        _init_providers()  # 配置可能运行时变化(开关 provider)
        # 只保留仍启用的 provider 的结果,再刷新
        fresh = [p.fetch() for p in _providers.values()]
        with _results_lock:
            _results = fresh
            global _last_refresh_ts
            _last_refresh_ts = time.time()


def start():
    """启动后台拉取线程(仅一次)。"""
    global _started
    if _started:
        return
    _started = True
    refresh_now()  # 启动即拉一次
    t = threading.Thread(target=_loop, daemon=True)
    t.start()
