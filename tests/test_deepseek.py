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
    """is_available=false 且 balance_infos 为空:真实余额不足,返回零值(合法)。"""
    from providers.deepseek import parse_balance
    d = parse_balance({"is_available": False, "balance_infos": []})
    assert d["is_available"] is False
    assert d["total_balance"] == "0"


def test_parse_balance_infos_empty_raises():
    """is_available=true 但 balance_infos 为空:API 异常,禁止静默归零(防止误报余额不足)。"""
    from providers.deepseek import parse_balance
    import pytest
    with pytest.raises(ValueError):
        parse_balance({"is_available": True, "balance_infos": []})
    with pytest.raises(ValueError):
        parse_balance({"is_available": True})  # 键缺失同样视为异常


def test_parse_balance_multi_currency_prefers_cny():
    """多币种时优先取 CNY:USD 0 排在前也不误显示。"""
    from providers.deepseek import parse_balance
    raw = {
        "is_available": True,
        "balance_infos": [
            {"currency": "USD", "total_balance": "0.00",
             "granted_balance": "0.00", "topped_up_balance": "0.00"},
            {"currency": "CNY", "total_balance": "10.00",
             "granted_balance": "0.00", "topped_up_balance": "10.00"},
        ],
    }
    d = parse_balance(raw)
    assert d["currency"] == "CNY"
    assert d["total_balance"] == "10.00"


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
    """接口 401 时返回 error,kind=auth。"""
    import providers.deepseek as ds
    from providers.base import ERROR_KINDS
    monkeypatch.setattr(ds.config, "get_api_keys", lambda: {"deepseek": "sk-bad"})

    class FakeResp:
        status_code = 401
        text = "unauthorized"
        def json(self):
            return {}
        def raise_for_status(self):
            import requests
            err = requests.exceptions.HTTPError("401 Client Error")
            err.response = type("R", (), {"status_code": 401})()
            raise err

    monkeypatch.setattr(ds, "_http_get", lambda url, headers, timeout: FakeResp())
    p = ds.DeepSeekProvider()
    r = p.fetch()
    assert r["status"] == "error"
    assert r["error_kind"] == "auth"
    assert r["error"] == ERROR_KINDS["auth"]  # 人话
    assert "401" in r["error_detail"]  # 原始保留


def test_fetch_timeout_classified_network(monkeypatch):
    """超时归为 network。"""
    import providers.deepseek as ds
    monkeypatch.setattr(ds.config, "get_api_keys", lambda: {"deepseek": "sk-x"})

    def boom(url, headers, timeout):
        raise ds.requests.exceptions.Timeout("timed out")

    monkeypatch.setattr(ds, "_http_get", boom)
    p = ds.DeepSeekProvider()
    r = p.fetch()
    assert r["status"] == "error"
    assert r["error_kind"] == "network"
