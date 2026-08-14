SAMPLE = {
    "code": 20000,
    "message": "OK",
    "status": True,
    "data": {
        "id": "sf-xxx",
        "name": "hushen",
        "balance": 8.5,
        "chargeBalance": 10.0,
        "totalBalance": 18.5,
        "status": "normal",
    },
}


def test_parse_basic():
    from providers.siliconflow import parse_user_info
    d = parse_user_info(SAMPLE)
    assert d["total_balance"] == "18.50"
    assert d["topped_up_balance"] == "10.00"
    assert d["granted_balance"] == "8.50"   # 总 - 充值 = 赠送
    assert d["currency"] == "CNY"
    assert d["is_available"] is True


def test_parse_string_numbers():
    """字段为字符串数字时兼容。"""
    from providers.siliconflow import parse_user_info
    d = parse_user_info({"data": {"totalBalance": "3.5", "chargeBalance": "5"}})
    assert d["total_balance"] == "3.50"
    assert d["granted_balance"] == "0.00"  # 充值>总 → 赠送钳为 0,不出负数


def test_parse_missing_data_raises():
    """data 缺失 = 接口异常,禁止静默归零(防误报余额不足通知)。"""
    from providers.siliconflow import parse_user_info
    import pytest
    with pytest.raises(ValueError):
        parse_user_info({"code": 20000})
    with pytest.raises(ValueError):
        parse_user_info({"data": {"chargeBalance": 1}})  # 缺 totalBalance


def test_fetch_unconfigured(monkeypatch):
    import providers.siliconflow as sf
    monkeypatch.setattr(sf.config, "get_api_keys", lambda: {})
    r = sf.SiliconFlowProvider().fetch()
    assert r["status"] == "unconfigured"
    assert r["key"] == "siliconflow"


def test_fetch_success(monkeypatch):
    import providers.siliconflow as sf
    monkeypatch.setattr(sf.config, "get_api_keys", lambda: {"siliconflow": "sk-xxx"})

    class FakeResp:
        status_code = 200
        def json(self):
            return SAMPLE
        def raise_for_status(self):
            pass

    monkeypatch.setattr(sf, "_http_get", lambda url, headers, timeout: FakeResp())
    r = sf.SiliconFlowProvider().fetch()
    assert r["status"] == "ok"
    assert r["data"]["total_balance"] == "18.50"


def test_fetch_401_auth(monkeypatch):
    import providers.siliconflow as sf
    monkeypatch.setattr(sf.config, "get_api_keys", lambda: {"siliconflow": "sk-bad"})

    class FakeResp:
        status_code = 401
        text = "unauthorized"

    monkeypatch.setattr(sf, "_http_get", lambda url, headers, timeout: FakeResp())
    r = sf.SiliconFlowProvider().fetch()
    assert r["status"] == "error"
    assert r["error_kind"] == "auth"
