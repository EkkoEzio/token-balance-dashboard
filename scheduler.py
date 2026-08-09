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

# 告警通知去重:记录上次已通知的告警指纹集合,只在新增时弹通知
_last_notified: set = set()


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


def refresh_now(notify: bool = True) -> list:
    """立刻拉取所有 provider,返回最新结果。记录刷新时间戳。
    notify=True 时触发告警判定/桌面通知;启动时传 False 避免弹历史告警。"""
    global _results, _last_refresh_ts
    _init_providers()
    fresh = [p.fetch() for p in _providers.values()]
    with _results_lock:
        _results = fresh
        _last_refresh_ts = time.time()
    if notify:
        _check_and_notify(fresh)
    else:
        # 启动:把当前告警指纹计入已通知集合,这样后续只在"新增"时弹
        try:
            global _last_notified
            _last_notified = {f"{a['key']}:{a['window']}" for a in evaluate_alerts(fresh)}
        except Exception:
            pass
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


# ---------- 告警判定 ----------
def evaluate_alerts(results: list) -> list:
    """根据当前 provider 结果计算额度告警。返回告警列表。
    每条:{key, name, level(critical/warning), window, msg}
    纯函数,读 config.get_alerts_config() 取阈值。"""
    cfg = config.get_alerts_config()
    if not cfg.get("enabled", True):
        return []
    thr_pct = cfg.get("threshold_pct", 20)
    thr_balance = cfg.get("threshold_balance", 10)
    critical_pct = max(1, thr_pct // 4)  # 阈值的1/4为 critical 线
    alerts = []
    for r in results:
        if r.get("status") != "ok":
            continue  # error/expired 是 key 问题,不算额度告警
        key = r["key"]
        name = r.get("name", key)
        data = r.get("data") or {}

        if key == "deepseek":
            alerts.extend(_alert_deepseek(name, data, thr_balance))
        elif key == "zhipu":
            alerts.extend(_alert_window(name, data.get("windows", []), thr_pct, critical_pct))
        elif key == "tavly":
            alerts.extend(_alert_tavly(name, data.get("accounts", []), thr_pct, critical_pct))
    return alerts


def _level_from_pct(remain_pct: int, thr_pct: int, critical_pct: int) -> str:
    return "critical" if remain_pct <= critical_pct else "warning"


def _alert_deepseek(name, data, thr_balance):
    out = []
    try:
        bal = float(data.get("total_balance", "0"))
    except (TypeError, ValueError):
        bal = 0.0
    if data.get("is_available") is False:
        out.append({"key": "deepseek", "name": name, "level": "critical",
                    "window": "余额", "msg": f"余额不足(¥{bal:.2f})"})
    elif bal < thr_balance:
        lvl = "critical" if bal < thr_balance / 2 else "warning"
        out.append({"key": "deepseek", "name": name, "level": lvl,
                    "window": "余额", "msg": f"余额仅剩 ¥{bal:.2f}"})
    return out


def _alert_window(name, windows, thr_pct, critical_pct):
    out = []
    for w in windows:
        total = w.get("total", 0)
        if not total:
            continue
        remain_pct = round(w.get("remaining", 0) / total * 100)
        if remain_pct < thr_pct:
            lvl = _level_from_pct(remain_pct, thr_pct, critical_pct)
            out.append({"key": "zhipu", "name": name, "level": lvl,
                        "window": w.get("label", "窗口"),
                        "msg": f"{w.get('label','')}窗口仅剩 {remain_pct}%({w.get('remaining',0)}/{total})"})
    return out


def _alert_tavly(name, accounts, thr_pct, critical_pct):
    out = []
    for acc in accounts:
        total = acc.get("total")
        if total is None:
            continue  # 无限账号跳过
        if not total:
            continue
        remain_pct = round(acc.get("remaining", 0) / total * 100)
        if remain_pct < thr_pct:
            lvl = _level_from_pct(remain_pct, thr_pct, critical_pct)
            label = acc.get("label") or "账号"
            out.append({"key": "tavly", "name": name, "level": lvl,
                        "window": label,
                        "msg": f"{label}仅剩 {remain_pct}%({acc.get('remaining',0)}/{total})"})
    return out


def _check_and_notify(results: list):
    """判定告警,对新增告警发 macOS 桌面通知(去重:同一指纹只弹一次)。"""
    global _last_notified
    try:
        alerts = evaluate_alerts(results)
    except Exception:
        return
    cur_fps = {f"{a['key']}:{a['window']}" for a in alerts}
    new_fps = cur_fps - _last_notified
    _last_notified = cur_fps
    if not new_fps:
        return
    # 只通知新触发的
    for a in alerts:
        fp = f"{a['key']}:{a['window']}"
        if fp in new_fps:
            _send_notification(a["name"], a["msg"], a["level"])


def _send_notification(title: str, msg: str, level: str):
    """发 macOS 桌面通知(osascript)。失败静默(非关键路径)。"""
    import subprocess
    icon = "🔴" if level == "critical" else "🟡"
    # 转义双引号
    safe_title = title.replace('"', "'")
    safe_msg = (icon + " " + msg).replace('"', "'")
    try:
        subprocess.run(
            ["osascript", "-e",
             f'display notification "{safe_msg}" with title "Token看板:{safe_title}"'],
            timeout=5, capture_output=True,
        )
    except Exception:
        pass


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
        _check_and_notify(fresh)


def start():
    """启动后台拉取线程(仅一次)。"""
    global _started
    if _started:
        return
    _started = True
    refresh_now(notify=False)  # 启动即拉一次,但不弹历史告警通知
    t = threading.Thread(target=_loop, daemon=True)
    t.start()
