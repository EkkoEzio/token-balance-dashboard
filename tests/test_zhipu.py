import providers.zhipu as zp
from providers.zhipu import ZhipuProvider, parse_quota, UNIT_5H, UNIT_WEEK


SAMPLE = {
    "code": 200,
    "data": {
        "level": "lite",
        "limits": [
            {"type": "CREDIT_LIMIT", "unit": UNIT_5H, "number": 5,
             "usage": 2000, "currentValue": 300, "remaining": 1699,
             "percentage": 15, "nextResetTime": 1786257130291},
            {"type": "CREDIT_LIMIT", "unit": UNIT_WEEK, "number": 1,
             "usage": 10000, "currentValue": 1233, "remaining": 8766,
             "percentage": 12, "nextResetTime": 1786759307984},
            {"type": "TIME_LIMIT", "unit": 0, "percentage": 10},  # 应被过滤
        ],
    },
}


def test_parse_quota_windows():
    d = parse_quota(SAMPLE)
    assert d["level"] == "lite"
    assert len(d["windows"]) == 2  # TIME_LIMIT 被过滤
    w5 = d["windows"][0]
    assert w5["label"] == "5小时"
    assert w5["total"] == 2000
    assert w5["used"] == 300
    assert w5["remaining"] == 1699
    # 毫秒时间戳应转成 ISO 字符串
    assert w5["reset_at"].startswith("2026-") and "T" in w5["reset_at"]
    ww = d["windows"][1]
    assert ww["label"] == "7天"
    assert ww["total"] == 10000


def test_parse_quota_empty_limits():
    d = parse_quota({"data": {"level": "lite", "limits": []}})
    assert d["level"] == "lite"
    assert d["windows"] == []


def test_parse_quota_timestamp_ms_to_iso():
    """nextResetTime 是毫秒时间戳,需转 ISO;为0或缺失时不崩。"""
    d = parse_quota({"data": {"level": "x", "limits": [
        {"type": "CREDIT_LIMIT", "unit": UNIT_5H, "usage": 100, "currentValue": 10,
         "remaining": 90, "nextResetTime": 0}]}})
    assert d["windows"][0]["reset_at"] == ""


def test_fetch_unconfigured(monkeypatch):
    monkeypatch.setattr(zp.config, "get_api_keys", lambda: {})
    p = ZhipuProvider()
    r = p.fetch()
    assert r["status"] == "unconfigured"


def test_fetch_success(monkeypatch):
    monkeypatch.setattr(zp.config, "get_api_keys", lambda: {"zhipu": "abc.def"})
    captured = {}

    class FakeResp:
        status_code = 200
        def json(self):
            return SAMPLE
        def raise_for_status(self):
            pass

    def fake_get(url, headers, timeout):
        captured["headers"] = headers
        return FakeResp()

    monkeypatch.setattr(zp, "_http_get", fake_get)
    p = ZhipuProvider()
    r = p.fetch()
    assert r["status"] == "ok"
    assert r["data"]["level"] == "lite"
    assert len(r["data"]["windows"]) == 2
    # 关键:鉴权头不加 Bearer
    assert captured["headers"]["Authorization"] == "abc.def"


def test_fetch_business_401_returns_expired(monkeypatch):
    """接口 HTTP 200 但 body code=401(令牌过期)→ expired 状态 + expired kind。"""
    monkeypatch.setattr(zp.config, "get_api_keys", lambda: {"zhipu": "expired"})

    class FakeResp:
        status_code = 200
        def json(self):
            return {"code": 401, "msg": "令牌已过期或验证不正确", "success": False}
        def raise_for_status(self):
            pass

    monkeypatch.setattr(zp, "_http_get", lambda url, headers, timeout: FakeResp())
    p = ZhipuProvider()
    r = p.fetch()
    assert r["status"] == "expired"  # 专门状态,非通用 error
    assert r["error_kind"] == "expired"
    assert "令牌已过期" in r["error"]


def test_fetch_business_other_code_returns_auth(monkeypatch):
    """body code 非 401(如 403)→ auth kind。"""
    monkeypatch.setattr(zp.config, "get_api_keys", lambda: {"zhipu": "bad"})

    class FakeResp:
        status_code = 200
        def json(self):
            return {"code": 403, "msg": "无权限", "success": False}
        def raise_for_status(self):
            pass

    monkeypatch.setattr(zp, "_http_get", lambda url, headers, timeout: FakeResp())
    p = ZhipuProvider()
    r = p.fetch()
    assert r["status"] == "error"
    assert r["error_kind"] == "auth"
