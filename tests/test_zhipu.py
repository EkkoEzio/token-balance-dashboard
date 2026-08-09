import providers.zhipu as zp
from providers.zhipu import ZhipuProvider, parse_quota, UNIT_5H, UNIT_WEEK


SAMPLE = {
    "code": 200,
    "data": {
        "level": "pro",
        "limits": [
            {"type": "TOKENS_LIMIT", "unit": UNIT_5H, "percentage": 75,
             "usage": 2000, "currentValue": 1500, "remaining": 500,
             "nextResetTime": "2026-08-09T15:00:00+08:00"},
            {"type": "TOKENS_LIMIT", "unit": UNIT_WEEK, "percentage": 80,
             "usage": 10000, "currentValue": 8000, "remaining": 2000,
             "nextResetTime": "2026-08-14T10:00:00+08:00"},
            {"type": "TIME_LIMIT", "unit": 0, "percentage": 10},  # 应被过滤
        ],
    },
}


def test_parse_quota_windows():
    d = parse_quota(SAMPLE)
    assert d["level"] == "pro"
    assert len(d["windows"]) == 2  # TIME_LIMIT 被过滤
    w5 = d["windows"][0]
    assert w5["label"] == "5小时"
    assert w5["total"] == 2000
    assert w5["used"] == 1500
    assert w5["remaining"] == 500
    assert w5["reset_at"] == "2026-08-09T15:00:00+08:00"
    ww = d["windows"][1]
    assert ww["label"] == "7天"
    assert ww["total"] == 10000


def test_parse_quota_empty_limits():
    d = parse_quota({"data": {"level": "lite", "limits": []}})
    assert d["level"] == "lite"
    assert d["windows"] == []


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
    assert r["data"]["level"] == "pro"
    # 关键:鉴权头不加 Bearer
    assert captured["headers"]["Authorization"] == "abc.def"


def test_fetch_business_error_returns_error(monkeypatch):
    """接口 HTTP 200 但 body code=401(令牌过期)时,应返回 error + msg。"""
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
    assert r["status"] == "error"
    assert "令牌已过期" in r["error"]
