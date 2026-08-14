import scheduler
import config


def test_get_all_returns_active_providers(monkeypatch, tmp_path):
    """默认所有 provider 都返回(get_all 不再隐式 refresh,需显式 refresh_now)。"""
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    scheduler._providers = {}
    scheduler._results = []
    monkeypatch.setattr(scheduler.config, "get_disabled", lambda: set())
    # stub 所有 fetch,不打网络
    for cls in scheduler._ALL_CLASSES:
        monkeypatch.setattr(cls, "fetch",
                            lambda self: {"key": self.key, "name": self.name,
                                          "status": "ok", "data": {}, "updated_at": "t"})
    scheduler.refresh_now()
    results = scheduler.get_all()
    keys = {r["key"] for r in results}
    assert keys == {cls.key for cls in scheduler._ALL_CLASSES}


def test_disabled_provider_skipped(monkeypatch, tmp_path):
    """关闭的 provider 不出现在结果里。"""
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    scheduler._providers = {}
    scheduler._results = []
    # stub 所有 fetch,不打网络(关闭的 provider 不会被实例化,也不会被 fetch)
    for cls in scheduler._ALL_CLASSES:
        monkeypatch.setattr(cls, "fetch",
                            lambda self: {"key": self.key, "name": self.name,
                                          "status": "ok", "data": {}, "updated_at": "t"})
    monkeypatch.setattr(scheduler.config, "get_disabled", lambda: {"minimax", "qianwen", "kimi"})
    scheduler._init_providers()
    results = scheduler.refresh_now()
    keys = {r["key"] for r in results}
    assert keys == {cls.key for cls in scheduler._ALL_CLASSES} - {"minimax", "qianwen", "kimi"}


def test_refresh_now_records_timestamp(monkeypatch, tmp_path):
    """refresh_now 后 last_refresh_ts 应被更新(大于0)。"""
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    scheduler._providers = {}
    scheduler._results = []
    scheduler._last_refresh_ts = 0.0
    monkeypatch.setattr(scheduler.config, "get_disabled", lambda: set())
    # stub 所有 fetch,不打网络
    for cls in scheduler._ALL_CLASSES:
        monkeypatch.setattr(cls, "fetch",
                            lambda self: {"key": self.key, "name": self.name,
                                          "status": "ok", "data": {}, "updated_at": "t"})
    scheduler.refresh_now()
    assert scheduler.last_refresh_ts() > 0


def test_refresh_now_updates_results(monkeypatch, tmp_path):
    """refresh_now 对每个 provider 调 fetch 并更新结果。"""
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    scheduler._providers = {}
    scheduler._results = []
    monkeypatch.setattr(scheduler.config, "get_disabled", lambda: set())
    from providers.deepseek import DeepSeekProvider
    monkeypatch.setattr(DeepSeekProvider, "fetch",
                        lambda self: {"key": "deepseek", "name": "DeepSeek",
                                      "status": "ok", "data": {"x": 1}, "updated_at": "t"})
    # 其余四家 stub 成通用 ok dict,保持 hermetic(不打网络)
    from providers.zhipu import ZhipuProvider
    from providers.tavly import TavlyProvider
    from providers.qianwen import QianwenProvider
    from providers.minimax import MiniMaxProvider
    _generic = lambda self: {"key": self.key, "name": self.name,
                             "status": "ok", "data": {}, "updated_at": "t"}
    for cls in (ZhipuProvider, TavlyProvider, QianwenProvider, MiniMaxProvider):
        monkeypatch.setattr(cls, "fetch", _generic)
    results = scheduler.refresh_now()
    ds = next(r for r in results if r["key"] == "deepseek")
    assert ds["data"] == {"x": 1}


def test_refresh_keeps_old_data_on_error(monkeypatch, tmp_path):
    """fetch 失败返回 error 时,保留缓存里的旧成功数据(不冲掉)。"""
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    scheduler._providers = {}
    scheduler._results = [{"key": "deepseek", "name": "DeepSeek", "status": "ok",
                           "data": {"total_balance": "10.00"}, "updated_at": "old"}]
    monkeypatch.setattr(scheduler.config, "get_disabled", lambda: set())
    scheduler._init_providers()
    # 本次 fetch 全部返回 error
    from providers.deepseek import DeepSeekProvider
    from providers.zhipu import ZhipuProvider
    from providers.tavly import TavlyProvider
    from providers.qianwen import QianwenProvider
    from providers.minimax import MiniMaxProvider
    _err = lambda self: {"key": self.key, "name": self.name, "status": "error",
                         "data": {}, "error": "网络错误", "updated_at": "new"}
    for cls in (DeepSeekProvider, ZhipuProvider, TavlyProvider,
                QianwenProvider, MiniMaxProvider):
        monkeypatch.setattr(cls, "fetch", _err)
    results = scheduler.refresh_now()
    ds = next(r for r in results if r["key"] == "deepseek")
    # 旧成功数据被保留
    assert ds["status"] == "ok"
    assert ds["data"]["total_balance"] == "10.00"
    assert ds["updated_at"] == "old"


def test_refresh_overwrites_on_real_error(monkeypatch, tmp_path):
    """连续失败(缓存里也是 error)时,用新的 error 更新。"""
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    scheduler._providers = {}
    scheduler._results = [{"key": "deepseek", "name": "DeepSeek", "status": "error",
                           "data": {}, "error": "旧错误", "updated_at": "old"}]
    monkeypatch.setattr(scheduler.config, "get_disabled", lambda: set())
    scheduler._init_providers()
    from providers.deepseek import DeepSeekProvider
    from providers.zhipu import ZhipuProvider
    from providers.tavly import TavlyProvider
    from providers.qianwen import QianwenProvider
    from providers.minimax import MiniMaxProvider
    _err = lambda self: {"key": self.key, "name": self.name, "status": "error",
                         "data": {}, "error": "新错误", "updated_at": "new"}
    for cls in (DeepSeekProvider, ZhipuProvider, TavlyProvider,
                QianwenProvider, MiniMaxProvider):
        monkeypatch.setattr(cls, "fetch", _err)
    results = scheduler.refresh_now()
    ds = next(r for r in results if r["key"] == "deepseek")
    assert ds["error"] == "新错误"


# ---------- 告警判定 ----------
def _ok(key, data):
    return {"key": key, "name": key, "status": "ok", "data": data, "updated_at": "t"}


def test_alert_zhipu_window_low(monkeypatch):
    """智谱某窗口剩余<阈值 → 触发 warning。"""
    monkeypatch.setattr(scheduler.config, "get_alerts_config",
                        lambda: {"enabled": True, "threshold_pct": 20, "threshold_balance": 10})
    results = [_ok("zhipu", {"level": "lite", "windows": [
        {"label": "5小时", "total": 2000, "used": 1900, "remaining": 100, "percentage": 95, "reset_at": "x"},
        {"label": "7天", "total": 10000, "used": 3000, "remaining": 7000, "percentage": 30, "reset_at": "y"},
    ]})]
    alerts = scheduler.evaluate_alerts(results)
    assert len(alerts) == 1
    assert alerts[0]["key"] == "zhipu"
    assert alerts[0]["window"] == "5小时"
    assert alerts[0]["level"] == "critical"  # 5% 剩余 → critical


def test_alert_deepseek_balance_low(monkeypatch):
    """DeepSeek 余额低于阈值 → warning;is_available=False → critical。"""
    monkeypatch.setattr(scheduler.config, "get_alerts_config",
                        lambda: {"enabled": True, "threshold_pct": 20, "threshold_balance": 10})
    # 8元 < 10阈值 但 > 5(阈值/2) → warning
    results = [_ok("deepseek", {"is_available": True, "total_balance": "8.00"})]
    alerts = scheduler.evaluate_alerts(results)
    assert len(alerts) == 1
    assert alerts[0]["key"] == "deepseek"
    assert alerts[0]["level"] == "warning"

    # 3元 < 5(阈值/2) → critical
    results_low = [_ok("deepseek", {"is_available": True, "total_balance": "3.00"})]
    alerts_low = scheduler.evaluate_alerts(results_low)
    assert alerts_low[0]["level"] == "critical"

    # is_available=False → critical
    results2 = [_ok("deepseek", {"is_available": False, "total_balance": "8.00"})]
    alerts2 = scheduler.evaluate_alerts(results2)
    assert alerts2[0]["level"] == "critical"


def test_alert_tavly_skips_unlimited(monkeypatch):
    """Tavly unlimited 账号(total=None)不触发;有限账号低则触发。"""
    monkeypatch.setattr(scheduler.config, "get_alerts_config",
                        lambda: {"enabled": True, "threshold_pct": 20, "threshold_balance": 10})
    results = [_ok("tavly", {"accounts": [
        {"label": "账号1", "plan": "Free", "used": 10, "total": None, "remaining": None},  # 无限,跳过
        {"label": "账号2", "plan": "Boot", "used": 900, "total": 1000, "remaining": 100},  # 10% 剩余
    ]})]
    alerts = scheduler.evaluate_alerts(results)
    assert len(alerts) == 1
    assert alerts[0]["key"] == "tavly"
    assert alerts[0]["window"] == "账号2"


def test_alert_disabled_no_alerts(monkeypatch):
    """告警关闭时返回空。"""
    monkeypatch.setattr(scheduler.config, "get_alerts_config",
                        lambda: {"enabled": False, "threshold_pct": 20, "threshold_balance": 10})
    results = [_ok("deepseek", {"is_available": False, "total_balance": "0"})]
    assert scheduler.evaluate_alerts(results) == []


def test_alert_ignores_error_status(monkeypatch):
    """error/expired 状态不参与额度告警(那是 key 问题,不是额度低)。"""
    monkeypatch.setattr(scheduler.config, "get_alerts_config",
                        lambda: {"enabled": True, "threshold_pct": 20, "threshold_balance": 10})
    results = [{"key": "zhipu", "name": "智谱", "status": "expired", "data": {}, "updated_at": "t"}]
    assert scheduler.evaluate_alerts(results) == []


# ---------- 告警去重(_check_and_notify) ----------
def _stub_notifier():
    """把 _send_notification 替换成计数收集器,返回 (sent, restore)。
    sent 是已发出通知列表;调用 restore() 还原原函数。"""
    original = scheduler._send_notification
    sent = []
    scheduler._send_notification = lambda t, m, l: sent.append((t, m, l))
    return sent, (lambda: setattr(scheduler, "_send_notification", original))


def test_notify_dedup_survives_fetch_failure(monkeypatch):
    """fetch 偶发失败(error)不应清掉已通知的告警指纹 —— 否则「失败→恢复」
    抖动会让同一告警被反复弹出(用户实测 DeepSeek 短时间弹一堆 ¥0.00)。"""
    monkeypatch.setattr(scheduler.config, "get_alerts_config",
                        lambda: {"enabled": True, "threshold_pct": 20, "threshold_balance": 10})
    sent, restore = _stub_notifier()
    try:
        scheduler._last_notified = set()
        # 1) DeepSeek ok 余额 0 → 触发,通知 1 次
        scheduler._check_and_notify(
            [_ok("deepseek", {"is_available": True, "total_balance": "0.00"})])
        assert len(sent) == 1
        # 2) fetch 失败 status=error → 不新增通知
        scheduler._check_and_notify(
            [{"key": "deepseek", "name": "DeepSeek", "status": "error",
              "data": {}, "updated_at": "t"}])
        assert len(sent) == 1
        # 3) 恢复 ok 余额仍 0 → 不应重复通知(指纹在失败期间被保留)
        scheduler._check_and_notify(
            [_ok("deepseek", {"is_available": True, "total_balance": "0.00"})])
        assert len(sent) == 1
    finally:
        restore()


def test_notify_retriggers_after_real_recovery(monkeypatch):
    """告警在数据可用(status ok)时真正消失 → 清除指纹,下次重新降到阈值会再通知。"""
    monkeypatch.setattr(scheduler.config, "get_alerts_config",
                        lambda: {"enabled": True, "threshold_pct": 20, "threshold_balance": 10})
    sent, restore = _stub_notifier()
    try:
        scheduler._last_notified = set()
        scheduler._check_and_notify(
            [_ok("deepseek", {"is_available": True, "total_balance": "0.00"})])
        assert len(sent) == 1
        # 充值后余额充足,status 仍 ok → 告警真正消失
        scheduler._check_and_notify(
            [_ok("deepseek", {"is_available": True, "total_balance": "100.00"})])
        assert len(sent) == 1
        # 再次降到 0 → 视为新触发,重新通知
        scheduler._check_and_notify(
            [_ok("deepseek", {"is_available": True, "total_balance": "0.00"})])
        assert len(sent) == 2
    finally:
        restore()


def test_persist_writes_cache_file(monkeypatch, tmp_path):
    """_persist 把 _results + _last_refresh_ts 写入 cache.json。"""
    import scheduler
    import config
    cache_file = tmp_path / "cache.json"
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    scheduler._results = [{"key": "deepseek", "name": "DeepSeek",
                           "status": "ok", "data": {"x": 1}, "updated_at": "t"}]
    scheduler._last_refresh_ts = 12345.6
    scheduler._persist()
    import json
    data = json.loads(cache_file.read_text(encoding="utf-8"))
    assert data["last_refresh"] == 12345.6
    assert data["results"][0]["key"] == "deepseek"


def test_load_cache_from_disk_fills_results(monkeypatch, tmp_path):
    """_load_cache_from_disk 把磁盘数据填到 _results / _last_refresh_ts。"""
    import scheduler
    import config
    import json
    cache_file = tmp_path / "cache.json"
    cache_file.write_text(json.dumps({
        "results": [{"key": "zhipu", "status": "ok", "data": {}, "updated_at": "t"}],
        "last_refresh": 999.0,
    }), encoding="utf-8")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    scheduler._results = []
    scheduler._last_refresh_ts = 0.0
    scheduler._load_cache_from_disk()
    assert len(scheduler._results) == 1
    assert scheduler._results[0]["key"] == "zhipu"
    assert scheduler._last_refresh_ts == 999.0


def test_load_cache_silent_on_missing_file(monkeypatch, tmp_path):
    """文件不存在时不报错,保持 _results 为空。"""
    import scheduler
    import config
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    scheduler._results = []
    scheduler._last_refresh_ts = 0.0
    scheduler._load_cache_from_disk()  # 不应抛异常
    assert scheduler._results == []
    assert scheduler._last_refresh_ts == 0.0


def test_load_cache_silent_on_corrupt_json(monkeypatch, tmp_path):
    """JSON 损坏时不报错,保持 _results 为空。"""
    import scheduler
    import config
    (tmp_path / "cache.json").write_text("{不是合法json", encoding="utf-8")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    scheduler._results = []
    scheduler._last_refresh_ts = 0.0
    scheduler._load_cache_from_disk()
    assert scheduler._results == []
    assert scheduler._last_refresh_ts == 0.0


# ---------- 并发拉取 ----------
def test_fetch_all_concurrent_preserves_order(monkeypatch):
    """_fetch_all_concurrent 按 _ALL_CLASSES 顺序返回,即使并发完成顺序乱。"""
    import scheduler
    scheduler._providers = {}
    monkeypatch.setattr(scheduler.config, "get_disabled", lambda: set())
    scheduler._init_providers()
    # 把每家 fetch 替换成「sleep 随机时长后返回自己的 key」,模拟并发完成顺序乱
    import time as _t, random as _r
    def make_fetch(name):
        def _f(self):
            _t.sleep(_r.uniform(0, 0.05))
            return {"key": self.key, "name": name, "status": "ok", "data": {}, "updated_at": "t"}
        return _f
    for cls in scheduler._ALL_CLASSES:
        monkeypatch.setattr(cls, "fetch", make_fetch(cls.__name__))
    results = scheduler._fetch_all_concurrent()
    keys = [r["key"] for r in results]
    expected = [cls.key for cls in scheduler._ALL_CLASSES]
    assert keys == expected


def test_fetch_all_concurrent_isolates_failure(monkeypatch):
    """某家 fetch 抛异常时,该家返回 error 结果,其他家不受影响。"""
    import scheduler
    scheduler._providers = {}
    monkeypatch.setattr(scheduler.config, "get_disabled", lambda: set())
    scheduler._init_providers()
    from providers.deepseek import DeepSeekProvider
    def boom(self):
        raise RuntimeError("炸了")
    # 所有五家都打桩,保持测试 hermetic(其余四家返回正常 stub,不打网络)
    def stub_fetch(self):
        return {"key": self.key, "name": self.name, "status": "ok",
                "data": {}, "updated_at": "t"}
    for cls in scheduler._ALL_CLASSES:
        if cls is DeepSeekProvider:
            monkeypatch.setattr(cls, "fetch", boom)
        else:
            monkeypatch.setattr(cls, "fetch", stub_fetch)
    results = scheduler._fetch_all_concurrent()
    ds = next(r for r in results if r["key"] == "deepseek")
    assert ds["status"] == "error"  # 异常被捕获,转成 error
    assert "炸了" in ds.get("error_detail", "")


def test_refresh_now_persists_to_disk(monkeypatch, tmp_path):
    """refresh_now 成功后 cache.json 被写入。"""
    import scheduler
    import config
    import json
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    scheduler._providers = {}
    scheduler._results = []
    scheduler._last_refresh_ts = 0.0
    monkeypatch.setattr(scheduler.config, "get_disabled", lambda: set())
    # stub 所有 fetch,不打网络
    for cls in scheduler._ALL_CLASSES:
        monkeypatch.setattr(cls, "fetch",
                            lambda self: {"key": self.key, "name": self.name,
                                          "status": "ok", "data": {}, "updated_at": "t"})
    scheduler.refresh_now()
    cache_file = tmp_path / "cache.json"
    assert cache_file.exists()
    data = json.loads(cache_file.read_text(encoding="utf-8"))
    assert data["last_refresh"] > 0
    assert len(data["results"]) == len(scheduler._ALL_CLASSES)


def test_refresh_one_persists_to_disk(monkeypatch, tmp_path):
    """refresh_one 单家刷新后,该家结果在磁盘上更新。"""
    import scheduler
    import config
    import json
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    # 预置一份磁盘缓存
    cache_file = tmp_path / "cache.json"
    cache_file.write_text(json.dumps({
        "results": [{"key": "deepseek", "status": "ok", "data": {"old": True}, "updated_at": "old"}],
        "last_refresh": 1.0,
    }), encoding="utf-8")
    scheduler._results = []
    scheduler._last_refresh_ts = 0.0
    scheduler._load_cache_from_disk()
    scheduler._providers = {}
    monkeypatch.setattr(scheduler.config, "get_disabled", lambda: set())
    scheduler._init_providers()
    from providers.deepseek import DeepSeekProvider
    monkeypatch.setattr(DeepSeekProvider, "fetch",
                        lambda self: {"key": "deepseek", "name": "DeepSeek",
                                      "status": "ok", "data": {"new": True}, "updated_at": "new"})
    scheduler.refresh_one("deepseek")
    data = json.loads(cache_file.read_text(encoding="utf-8"))
    ds = next(r for r in data["results"] if r["key"] == "deepseek")
    assert ds["data"] == {"new": True}


def test_get_all_does_not_trigger_refresh(monkeypatch):
    """get_all 在 _results 为空时直接返回 [],不再隐式触发 refresh_now。"""
    import scheduler
    scheduler._results = []
    scheduler._last_refresh_ts = 0.0
    called = {"n": 0}
    def _boom(*a, **kw):
        called["n"] += 1
        raise AssertionError("refresh_now 不应被 get_all 触发")
    monkeypatch.setattr(scheduler, "refresh_now", _boom)
    result = scheduler.get_all()
    assert result == []
    assert called["n"] == 0


def test_get_all_returns_disk_cache_after_load(monkeypatch, tmp_path):
    """_load_cache_from_disk 之后,get_all 直接返回磁盘数据(不等后台)。"""
    import scheduler
    import config
    import json
    (tmp_path / "cache.json").write_text(json.dumps({
        "results": [{"key": "x", "status": "ok", "data": {}, "updated_at": "t"}],
        "last_refresh": 5.0,
    }), encoding="utf-8")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    scheduler._results = []
    scheduler._last_refresh_ts = 0.0
    scheduler._load_cache_from_disk()
    # get_all 不该再触发 refresh,这里 _results 已有数据
    result = scheduler.get_all()
    assert len(result) == 1
    assert result[0]["key"] == "x"


def test_startup_refresh_runs_in_background(monkeypatch, tmp_path):
    """_startup_refresh 并发拉取并更新内存 + 落盘 + 抑制告警指纹。"""
    import scheduler
    import config
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    scheduler._providers = {}
    scheduler._results = []
    scheduler._last_refresh_ts = 0.0
    scheduler._last_notified = set()
    monkeypatch.setattr(scheduler.config, "get_disabled", lambda: set())
    for cls in scheduler._ALL_CLASSES:
        monkeypatch.setattr(cls, "fetch",
                            lambda self: {"key": self.key, "name": self.name,
                                          "status": "ok", "data": {}, "updated_at": "t"})
    scheduler._startup_refresh()
    assert len(scheduler._results) == len(scheduler._ALL_CLASSES)
    assert scheduler._last_refresh_ts > 0
    assert (tmp_path / "cache.json").exists()
