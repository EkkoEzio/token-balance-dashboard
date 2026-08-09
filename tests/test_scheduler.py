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
