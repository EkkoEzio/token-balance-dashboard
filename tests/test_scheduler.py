import scheduler


def test_get_all_returns_all_providers():
    """初始化后 get_all 返回4家(即使占位也返回)。"""
    scheduler._init_providers()
    results = scheduler.get_all()
    keys = {r["key"] for r in results}
    assert keys == {"deepseek", "zhipu", "qianwen", "minimax"}


def test_refresh_now_calls_each_provider_fetch(monkeypatch):
    """refresh_now 对每个 provider 调 fetch 并更新结果。"""
    scheduler._init_providers()
    monkeypatch.setattr(scheduler._providers["deepseek"], "fetch",
                        lambda: {"key": "deepseek", "name": "DeepSeek",
                                 "status": "ok", "data": {"x": 1}, "updated_at": "t"})
    results = scheduler.refresh_now()
    ds = next(r for r in results if r["key"] == "deepseek")
    assert ds["data"] == {"x": 1}
    # get_all 能拿到刷新后的值
    assert scheduler.get_all() == results
