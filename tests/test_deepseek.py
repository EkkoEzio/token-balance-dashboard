SAMPLE = {
    "is_available": True,
    "balance_infos": [
        {"currency": "CNY", "total_balance": "110.00",
         "granted_balance": "10.00", "topped_up_balance": "100.00"}
    ],
}


def test_parse_balance_basic():
    from providers.deepseek import parse_balance
    d = parse_balance(SAMPLE)
    assert d["is_available"] is True
    assert d["total_balance"] == "110.00"
    assert d["granted_balance"] == "10.00"
    assert d["topped_up_balance"] == "100.00"
    assert d["currency"] == "CNY"


def test_parse_balance_missing_infos():
    from providers.deepseek import parse_balance
    d = parse_balance({"is_available": False, "balance_infos": []})
    assert d["is_available"] is False
    assert d["total_balance"] == "0"


def test_fetch_unconfigured_without_key(monkeypatch):
    """没存 key 时返回 unconfigured。"""
    import providers.deepseek as ds
    monkeypatch.setattr(ds.config, "get_api_keys", lambda: {})
    p = ds.DeepSeekProvider()
    r = p.fetch()
    assert r["status"] == "unconfigured"
    assert r["key"] == "deepseek"


def test_fetch_success(monkeypatch):
    """有 key 时调网络(注入 mock)并返回 ok。"""
    import providers.deepseek as ds
    monkeypatch.setattr(ds.config, "get_api_keys", lambda: {"deepseek": "sk-xxx"})

    class FakeResp:
        status_code = 200
        def json(self):
            return SAMPLE
        def raise_for_status(self):
            pass

    monkeypatch.setattr(ds, "_http_get", lambda url, headers, timeout: FakeResp())
    p = ds.DeepSeekProvider()
    r = p.fetch()
    assert r["status"] == "ok"
    assert r["data"]["total_balance"] == "110.00"


def test_fetch_http_error(monkeypatch):
    """接口 401 时返回 error。"""
    import providers.deepseek as ds
    monkeypatch.setattr(ds.config, "get_api_keys", lambda: {"deepseek": "sk-bad"})

    class FakeResp:
        status_code = 401
        text = "unauthorized"
        def json(self):
            return {}
        def raise_for_status(self):
            raise Exception("401")

    monkeypatch.setattr(ds, "_http_get", lambda url, headers, timeout: FakeResp())
    p = ds.DeepSeekProvider()
    r = p.fetch()
    assert r["status"] == "error"
