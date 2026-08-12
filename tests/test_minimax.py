from providers.minimax import parse_remains, MiniMaxProvider


# 旧版字段(count 值有效)
OLD_VERSION = {
    "data": {
        "current_interval_total_count": 1500,
        "current_interval_usage_count": 600,
        "current_interval_reset_time": 1786886760,
        "current_weekly_total_count": 15000,
        "current_weekly_usage_count": 3000,
        "current_weekly_reset_time": 1787491560,
    }
}

# 新版字段(count 恒为0,用 remaining_percent)
NEW_VERSION = {
    "data": {
        "current_interval_remaining_percent": 60.0,
        "current_interval_reset_time": 1786886760,
        "current_weekly_remaining_percent": 80.0,
        "current_weekly_reset_time": 1787491560,
    }
}


def test_parse_old_version():
    """旧版:用 count,total/used/remaining 都有绝对值。"""
    d = parse_remains(OLD_VERSION)
    assert len(d["windows"]) == 2
    w5 = d["windows"][0]
    assert w5["label"] == "5小时"
    assert w5["total"] == 1500
    assert w5["used"] == 600
    assert w5["remaining"] == 900
    assert w5["unit"] == "次"
    assert w5["reset_at"].startswith("2026-")
    ww = d["windows"][1]
    assert ww["total"] == 15000
    assert ww["remaining"] == 12000


def test_parse_new_version():
    """新版:count 为0,用 remaining_percent 反推百分比。"""
    d = parse_remains(NEW_VERSION)
    assert len(d["windows"]) == 2
    w5 = d["windows"][0]
    assert w5["total"] == 0  # 无绝对值
    assert w5["percentage"] == 40.0  # 100 - 60
    assert w5["unit"] == "%"
    ww = d["windows"][1]
    assert ww["percentage"] == 20.0  # 100 - 80


def test_parse_empty():
    d = parse_remains({})
    assert d["windows"] == []
    assert d["level"] == ""


def test_fetch_unconfigured(monkeypatch):
    import providers.minimax as mm
    monkeypatch.setattr(mm.config, "get_api_keys", lambda: {})
    p = MiniMaxProvider()
    r = p.fetch()
    assert r["status"] == "unconfigured"


def test_fetch_401_is_auth(monkeypatch):
    import providers.minimax as mm
    monkeypatch.setattr(mm.config, "get_api_keys", lambda: {"minimax": "sk-wrong"})

    class FakeResp:
        status_code = 401
        text = "unauthorized"
        def raise_for_status(self): pass
        def json(self): return {}

    monkeypatch.setattr(mm, "_http_get", lambda url, headers, timeout: FakeResp())
    p = MiniMaxProvider()
    r = p.fetch()
    assert r["status"] == "error"
    assert r["error_kind"] == "auth"


def test_fetch_success(monkeypatch):
    import providers.minimax as mm
    monkeypatch.setattr(mm.config, "get_api_keys", lambda: {"minimax": "sk-cp-xxx"})

    class FakeResp:
        status_code = 200
        def json(self): return OLD_VERSION
        def raise_for_status(self): pass

    captured = {}
    def fake_get(url, headers, timeout):
        captured["headers"] = headers
        return FakeResp()
    monkeypatch.setattr(mm, "_http_get", fake_get)
    p = MiniMaxProvider()
    r = p.fetch()
    assert r["status"] == "ok"
    assert captured["headers"]["Authorization"] == "Bearer sk-cp-xxx"
    assert len(r["data"]["windows"]) == 2
