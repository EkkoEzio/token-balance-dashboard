"""定时拉取调度器。结果缓存内存,前端轮询读缓存。

刷新时机:
- 定时兜底:每 REFRESH_INTERVAL 秒(10 分钟)统一刷新全部,避免过频触发风控
- 配置变更:设置 key / 切换供应商时由 app 层主动调 refresh_now()
- 手动触发:用户点「刷新」调 /api/refresh → refresh_now()
"""
import threading
import time
import json
import os
from concurrent.futures import ThreadPoolExecutor

from providers.deepseek import DeepSeekProvider
from providers.zhipu import ZhipuProvider
from providers.qianwen import QianwenProvider
from providers.minimax import MiniMaxProvider
from providers.tavly import TavlyProvider
from providers.siliconflow import SiliconFlowProvider
from providers.kimi import KimiProvider
import config

# 所有可用的 provider 类(顺序即展示顺序)
_ALL_CLASSES = [DeepSeekProvider, ZhipuProvider, TavlyProvider,
                QianwenProvider, MiniMaxProvider, SiliconFlowProvider,
                KimiProvider]

# 统一定时刷新间隔(秒)。10 分钟,避免过频触发各平台风控/限流。
REFRESH_INTERVAL = 600

_providers: dict = {}
_results: list = []
_last_refresh_ts: float = 0.0
_results_lock = threading.Lock()
_started = False
_persist_lock = threading.Lock()  # 串行化磁盘写(与 _results_lock 独立)

# 告警通知去重:指纹 -> 已通知的周期(period)。
# 窗口型(5h/7天/月度):period=窗口 reset_at,同一周期内最多通知一次,
#   周期内告警抖动/消失再现不重发;窗口重置后(新 reset_at)再耗尽才算新事件。
# 余额型(period 为空):事件语义 —— 告警在数据可用时真正消失才允许下次重新通知。
# 状态持久化到 data/notified.json,重启不失忆(否则每次重启重弹存量告警)。
_notified: dict = {}
_notified_lock = threading.Lock()


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


def _fetch_all_concurrent() -> list:
    """并发拉取 _providers 中所有 provider,按 _ALL_CLASSES 顺序返回结果。
    假设 _providers 已通过 _init_providers() 初始化。每家独立 try/except,
    异常转成 error 结果,不影响其他家。"""
    # 按 _ALL_CLASSES 的定义顺序取(展示顺序稳定)
    ordered_keys = [cls.key for cls in _ALL_CLASSES if cls.key in _providers]
    providers_in_order = [_providers[k] for k in ordered_keys]

    def _safe_fetch(p):
        try:
            return p.fetch()
        except Exception as e:
            return p.error(str(e), "unknown")

    # ThreadPoolExecutor.map 保证返回顺序与输入顺序一致
    with ThreadPoolExecutor(max_workers=5) as ex:
        fetched = list(ex.map(_safe_fetch, providers_in_order))
    return fetched


def refresh_now(notify: bool = True) -> list:
    """立刻并发拉取所有 provider,返回最新结果。记录刷新时间戳并落盘。
    失败保护:某 provider 本次 fetch 失败(error)时,保留缓存的旧成功数据,
    避免偶发网络抖动把好数据冲掉(前端显示上次成功值)。
    notify=True 时触发告警判定/桌面通知;启动时传 False 避免弹历史告警。"""
    global _results, _last_refresh_ts
    _init_providers()
    fresh = _fetch_all_concurrent()

    # 失败保护:error/expired 且有旧成功数据 → 保留旧的
    with _results_lock:
        old_by_key = {r["key"]: r for r in _results}
        merged = []
        for r in fresh:
            if r.get("status") in ("error", "expired") and r["key"] in old_by_key:
                old = old_by_key[r["key"]]
                # 旧数据若也是失败态,直接用新的
                if old.get("status") not in ("error", "expired"):
                    merged.append(old)
                    continue
            merged.append(r)
        _results = merged
        _last_refresh_ts = time.time()
    _persist()
    if notify:
        _check_and_notify(_results)
    return list(_results)


def get_all() -> list:
    """返回缓存的最新结果(快照),按 config 的 order 排序(未配置用默认顺序)。
    不在空时触发 refresh —— start() 已在后台异步拉取,首次访问可读到磁盘缓存。"""
    with _results_lock:
        results = list(_results)
    order = config.get_order()
    if order:
        rank = {k: i for i, k in enumerate(order)}
        results.sort(key=lambda r: rank.get(r["key"], 999))
    return results


def refresh_one(key: str) -> dict | None:
    """只刷新单个 provider,更新该家在缓存中的结果并落盘,返回新结果。
    用于卡片单独刷新(不影响其他家,不触发全量请求,避免风控)。"""
    global _last_refresh_ts
    _init_providers()
    p = _providers.get(key)
    if not p:
        return None
    result = p.fetch()
    with _results_lock:
        # 替换缓存中该 key 的结果(若存在)
        for i, r in enumerate(_results):
            if r.get("key") == key:
                _results[i] = result
                break
        else:
            _results.append(result)
        # 单卡刷新也推进全局时间戳,保持 /api/usage.last_refresh 与前端一致
        _last_refresh_ts = time.time()
    _persist()
    # 单家刷新也走告警判定(通知)
    _check_and_notify(list(_results))
    return result


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

        if key in ("deepseek", "siliconflow"):
            alerts.extend(_alert_deepseek(key, name, data, thr_balance))
        elif key in ("zhipu", "kimi"):
            alerts.extend(_alert_window(key, name, data.get("windows", []), thr_pct, critical_pct))
        elif key == "tavly":
            alerts.extend(_alert_tavly(name, data.get("accounts", []), thr_pct, critical_pct))
    return alerts


def _level_from_pct(remain_pct: int, thr_pct: int, critical_pct: int) -> str:
    return "critical" if remain_pct <= critical_pct else "warning"


def _alert_deepseek(key, name, data, thr_balance):
    out = []
    if "total_balance" not in data:
        # 数据缺余额字段:不可信,不告警(防把异常数据当 ¥0 误报)
        return out
    try:
        bal = float(data.get("total_balance", "0"))
    except (TypeError, ValueError):
        bal = 0.0
    if data.get("is_available") is False:
        out.append({"key": key, "name": name, "level": "critical",
                    "window": "余额", "msg": f"余额不足(¥{bal:.2f})"})
    elif bal < thr_balance:
        lvl = "critical" if bal < thr_balance / 2 else "warning"
        out.append({"key": key, "name": name, "level": lvl,
                    "window": "余额", "msg": f"余额仅剩 ¥{bal:.2f}"})
    return out


def _alert_window(key, name, windows, thr_pct, critical_pct):
    out = []
    for w in windows:
        total = w.get("total", 0)
        if not total:
            continue
        remain_pct = round(w.get("remaining", 0) / total * 100)
        if remain_pct < thr_pct:
            lvl = _level_from_pct(remain_pct, thr_pct, critical_pct)
            out.append({"key": key, "name": name, "level": lvl,
                        "window": w.get("label", "窗口"),
                        "period": w.get("reset_at") or "",
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
                        "period": acc.get("reset_at") or "",
                        "msg": f"{label}仅剩 {remain_pct}%({acc.get('remaining',0)}/{total})"})
    return out


def _check_and_notify(results: list):
    """判定告警并发 macOS 桌面通知(周期感知去重)。

    窗口型告警(带 period=reset_at):同一周期只通知一次,周期内告警抖动/
    消失再现不重发;窗口重置(新 reset_at)后再耗尽才算新事件、可再通知。
    余额型告警(无 period):无记录才通知;数据可用且告警真正消失时清除记录,
    「充值恢复→再次跌破」可重新通知。fetch 偶发失败(status!=ok)不清除
    记录,防「失败→恢复」抖动重弹。状态持久化,重启不失忆。"""
    global _notified
    try:
        alerts = evaluate_alerts(results)
    except Exception:
        return
    changed = False
    with _notified_lock:
        cur_fps = set()
        for a in alerts:
            fp = f"{a['key']}:{a['window']}"
            period = a.get("period") or ""
            cur_fps.add(fp)
            rec = _notified.get(fp)
            if not rec or (period and rec.get("period") != period):
                _send_notification(a["name"], a["msg"], a["level"])
                _notified[fp] = {"period": period}
                changed = True
        # 清除:数据可用且告警真正消失时,仅余额型(无周期)清除;
        # 窗口型记录保留到周期自然轮换(新 reset_at 覆盖),防周期内抖动重弹
        ok_keys = {r.get("key") for r in results if r.get("status") == "ok"}
        for fp in list(_notified):
            if fp not in cur_fps and fp.split(":", 1)[0] in ok_keys:
                if not _notified[fp].get("period"):
                    del _notified[fp]
                    changed = True
    if changed:
        _persist_notified()


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


def _persist_notified():
    """把通知去重状态原子写入 data/notified.json。失败静默(下次再试)。"""
    path = config.DATA_DIR / "notified.json"
    with _notified_lock:
        snapshot = dict(_notified)
    with _persist_lock:
        tmp = path.with_suffix(".json.tmp")
        try:
            tmp.write_text(json.dumps(snapshot, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, path)
        except Exception:
            pass


def _load_notified_from_disk():
    """启动时从 data/notified.json 恢复通知去重状态(重启不重弹存量告警)。
    文件不存在/损坏时静默跳过。"""
    global _notified
    path = config.DATA_DIR / "notified.json"
    if not path.exists():
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            with _notified_lock:
                _notified = {k: v for k, v in data.items() if isinstance(v, dict)}
    except Exception:
        pass


def _load_cache_from_disk():
    """启动时从 data/cache.json 读上次结果。文件不存在/损坏时静默跳过。
    在 start() 中、任何后台线程启动前同步调用 —— 保证首次 /api/usage 即可拿到磁盘数据。"""
    global _results, _last_refresh_ts
    path = config.DATA_DIR / "cache.json"
    if not path.exists():
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        results = data.get("results", [])
        last_refresh = float(data.get("last_refresh", 0.0))
        if isinstance(results, list):
            with _results_lock:
                _results = results
                _last_refresh_ts = last_refresh
    except Exception:
        # 损坏文件:静默丢弃,等后台刷新
        pass


def _persist():
    """把当前 _results + _last_refresh_ts 原子写入 data/cache.json。
    锁内取快照、锁外写盘;_persist_lock 串行化多次写,防互相截断。失败静默。"""
    path = config.DATA_DIR / "cache.json"
    with _results_lock:
        snapshot = {"results": list(_results), "last_refresh": _last_refresh_ts}
    with _persist_lock:
        tmp = path.with_suffix(".json.tmp")
        try:
            tmp.write_text(json.dumps(snapshot, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, path)
        except Exception:
            # 写盘失败不影响内存与请求,下次再试
            pass


def _loop():
    """后台循环:每 REFRESH_INTERVAL 秒并发刷新全部 provider,并落盘。"""
    while True:
        global _results, _last_refresh_ts
        try:
            # 单次循环异常不杀死兜底线程
            time.sleep(REFRESH_INTERVAL)
            _init_providers()  # 配置可能运行时变化(开关 provider)
            fresh = _fetch_all_concurrent()
            with _results_lock:
                _results = fresh
                _last_refresh_ts = time.time()
            _persist()
            _check_and_notify(fresh)
        except Exception:
            pass


def _startup_refresh():
    """启动后台并发刷新(不阻塞 start())。拉完更新缓存+落盘,并抑制历史告警通知。
    逻辑等价于旧 refresh_now(notify=False),但被放进后台线程异步执行。"""
    global _results, _last_refresh_ts
    try:
        # 启动刷新失败不影响服务（磁盘缓存仍可用，_loop 兜底）
        _init_providers()
        fresh = _fetch_all_concurrent()
        with _results_lock:
            _results = fresh
            _last_refresh_ts = time.time()
        _persist()
    except Exception:
        pass


def start():
    """启动后台拉取线程(仅一次)。
    流程:同步读磁盘缓存 → 启动 10 分钟兜底循环 → 启动一次性的启动并发刷新。
    读盘同步完成,保证首次 /api/usage 即可拿到磁盘数据(不等网络)。"""
    global _started
    if _started:
        return
    _started = True
    _init_providers()
    _load_cache_from_disk()
    _load_notified_from_disk()  # 恢复通知去重状态,重启不重弹存量告警
    threading.Thread(target=_loop, daemon=True).start()
    threading.Thread(target=_startup_refresh, daemon=True).start()
