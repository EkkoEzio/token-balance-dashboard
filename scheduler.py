"""定时拉取调度器。每家 provider 独立间隔,结果缓存内存。
后台线程拉取,前端轮询读缓存。"""
import threading
import time

from providers.deepseek import DeepSeekProvider
from providers.zhipu import ZhipuProvider
from providers.qianwen import QianwenProvider
from providers.minimax import MiniMaxProvider

_providers: dict = {}
_results: list = []
_results_lock = threading.Lock()
_started = False


def _init_providers():
    """实例化所有 provider。测试可单独调。"""
    global _providers
    if _providers:
        return
    for cls in (DeepSeekProvider, ZhipuProvider, QianwenProvider, MiniMaxProvider):
        p = cls()
        _providers[p.key] = p


def refresh_now() -> list:
    """立刻拉取所有 provider,返回最新结果。"""
    global _results
    _init_providers()
    fresh = [p.fetch() for p in _providers.values()]
    with _results_lock:
        _results = fresh
    return fresh


def get_all() -> list:
    """返回缓存的最新结果。首次调用会触发一次 refresh。"""
    if not _results:
        refresh_now()
    with _results_lock:
        return list(_results)


def _loop():
    """后台循环:每家按自己的间隔拉取。"""
    _init_providers()
    last_pull = {}
    while True:
        now = time.time()
        fresh = list(get_all())  # 拿当前快照
        idx = {r["key"]: i for i, r in enumerate(fresh)}
        changed = False
        for key, p in _providers.items():
            if now - last_pull.get(key, 0) >= p.refresh_interval:
                result = p.fetch()
                if key in idx:
                    fresh[idx[key]] = result
                else:
                    fresh.append(result)
                last_pull[key] = now
                changed = True
        if changed:
            with _results_lock:
                global _results
                _results = fresh
        time.sleep(10)  # 每 10 秒检查一次


def start():
    """启动后台拉取线程(仅一次)。"""
    global _started
    if _started:
        return
    _started = True
    refresh_now()  # 启动即拉一次
    t = threading.Thread(target=_loop, daemon=True)
    t.start()
