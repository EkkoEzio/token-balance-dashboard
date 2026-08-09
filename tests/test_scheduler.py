import scheduler


def test_get_all_returns_active_providers(monkeypatch):
    """默认所有 provider 都返回。"""
    scheduler._providers = {}
    scheduler._results = []
    monkeypatch.setattr(scheduler.config, "get_disabled", lambda: set())
    scheduler._init_providers()
    results = scheduler.get_all()
    keys = {r["key"] for r in results}
    assert keys == {"deepseek", "zhipu", "tavly", "qianwen", "minimax"}


def test_disabled_provider_skipped(monkeypatch):
    """关闭的 provider 不出现在结果里。"""
    scheduler._providers = {}
    scheduler._results = []
    monkeypatch.setattr(scheduler.config, "get_disabled", lambda: {"minimax", "qianwen"})
    scheduler._init_providers()
    results = scheduler.refresh_now()
    keys = {r["key"] for r in results}
    assert keys == {"deepseek", "zhipu", "tavly"}


def test_refresh_now_records_timestamp(monkeypatch):
    """refresh_now 后 last_refresh_ts 应被更新(大于0)。"""
    scheduler._providers = {}
    scheduler._results = []
    scheduler._last_refresh_ts = 0.0
    monkeypatch.setattr(scheduler.config, "get_disabled", lambda: set())
    scheduler.refresh_now()
    assert scheduler.last_refresh_ts() > 0


def test_refresh_now_updates_results(monkeypatch):
    """refresh_now 对每个 provider 调 fetch 并更新结果。"""
    scheduler._providers = {}
    scheduler._results = []
    monkeypatch.setattr(scheduler.config, "get_disabled", lambda: set())
    from providers.deepseek import DeepSeekProvider
    monkeypatch.setattr(DeepSeekProvider, "fetch",
                        lambda self: {"key": "deepseek", "name": "DeepSeek",
                                      "status": "ok", "data": {"x": 1}, "updated_at": "t"})
    results = scheduler.refresh_now()
    ds = next(r for r in results if r["key"] == "deepseek")
    assert ds["data"] == {"x": 1}


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
