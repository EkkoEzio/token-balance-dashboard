import providers.tavly as tv
from providers.tavly import TavlyProvider, parse_usage


SAMPLE = {
    "key": {"usage": 150, "limit": 1000, "search_usage": 100},
    "account": {
        "current_plan": "Bootstrap",
        "plan_usage": 500,
        "plan_limit": 15000,
        "paygo_usage": 25,
        "paygo_limit": 100,
    },
}


def test_parse_usage_basic():
    d = parse_usage(SAMPLE)
    assert d["plan"] == "Bootstrap"
    assert d["used"] == 500
    assert d["total"] == 15000
    assert d["remaining"] == 14500
    assert d["reset_note"] == "每月1号重置"


def test_parse_usage_unlimited():
    """plan_limit 为 null(无限)时,remaining 为 None。"""
    d = parse_usage({"account": {"current_plan": "Free", "plan_usage": 5, "plan_limit": None}})
    assert d["total"] is None
    assert d["remaining"] is None


def test_fetch_unconfigured(monkeypatch):
    monkeypatch.setattr(tv.config, "get_api_keys", lambda: {})
    p = TavlyProvider()
    r = p.fetch()
    assert r["status"] == "unconfigured"


def test_fetch_single_key(monkeypatch):
    monkeypatch.setattr(tv.config, "get_api_keys", lambda: {"tavly": "tvly-abc"})

    class FakeResp:
        status_code = 200
        def json(self):
            return SAMPLE
        def raise_for_status(self):
            pass

    monkeypatch.setattr(tv, "_http_get", lambda url, headers, timeout: FakeResp())
    p = TavlyProvider()
    r = p.fetch()
    assert r["status"] == "ok"
    assert len(r["data"]["accounts"]) == 1
    assert r["data"]["accounts"][0]["remaining"] == 14500


def test_fetch_double_key(monkeypatch):
    """逗号分隔两个 key,应分别查询,返回两个账号。"""
    monkeypatch.setattr(tv.config, "get_api_keys", lambda: {"tavly": "tvly-a, tvly-b"})
    calls = []

    class FakeResp:
        status_code = 200
        def json(self):
            return SAMPLE
        def raise_for_status(self):
            pass

    def fake_get(url, headers, timeout):
        calls.append(headers["Authorization"])
        return FakeResp()

    monkeypatch.setattr(tv, "_http_get", fake_get)
    p = TavlyProvider()
    r = p.fetch()
    assert r["status"] == "ok"
    assert len(r["data"]["accounts"]) == 2
    assert r["data"]["accounts"][0]["label"] == "账号1"
    assert r["data"]["accounts"][1]["label"] == "账号2"
    assert calls == ["Bearer tvly-a", "Bearer tvly-b"]


def test_fetch_one_key_fails(monkeypatch):
    """两个 key 一个成功一个失败,应返回成功的 + errors。"""
    monkeypatch.setattr(tv.config, "get_api_keys", lambda: {"tavly": "good,bad"})
    n = [0]

    class FakeResp:
        status_code = 200
        def json(self):
            return SAMPLE
        def raise_for_status(self):
            pass

    def fake_get(url, headers, timeout):
        n[0] += 1
        if n[0] == 2:
            raise Exception("401 Unauthorized")
        return FakeResp()

    monkeypatch.setattr(tv, "_http_get", fake_get)
    p = TavlyProvider()
    r = p.fetch()
    assert r["status"] == "ok"
    assert len(r["data"]["accounts"]) == 1
    assert len(r["data"]["errors"]) == 1


def test_fetch_all_keys_fail(monkeypatch):
    monkeypatch.setattr(tv.config, "get_api_keys", lambda: {"tavly": "bad1,bad2"})

    def fake_get(url, headers, timeout):
        raise Exception("401 Unauthorized")

    monkeypatch.setattr(tv, "_http_get", fake_get)
    p = TavlyProvider()
    r = p.fetch()
    assert r["status"] == "error"
