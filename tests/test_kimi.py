from datetime import datetime, timezone

SAMPLE = {
    "limits": [
        {"detail": {"limit": 120, "remaining": 45,
                    "resetTime": 1787000000}},  # 秒级时间戳
    ],
    "usage": {"limit": 1000, "remaining": 950,
              "resetTime": "2026-08-18T00:00:00Z"},  # ISO 字符串
}


def test_parse_basic():
    from providers.kimi import parse_usages
    d = parse_usages(SAMPLE)
    ws = d["windows"]
    assert len(ws) == 2
    assert ws[0]["label"] == "5小时"
    assert ws[0]["total"] == 120
    assert ws[0]["used"] == 75
    assert ws[0]["remaining"] == 45
    assert ws[0]["percentage"] == 62.5
    assert ws[0]["unit"] == "次"
    assert ws[1]["label"] == "7天"
    assert ws[1]["used"] == 50
    # 秒级时间戳转成了 ISO(带时区)
    assert ws[0]["reset_at"].startswith("2026-")
    assert "+" in ws[0]["reset_at"] or ws[0]["reset_at"].endswith("Z")
    # ISO 字符串直传
    assert ws[1]["reset_at"] == "2026-08-18T00:00:00Z"


def test_parse_reset_ms_and_negative():
    """毫秒时间戳可判别;-1 视为无重置时间。"""
    from providers.kimi import _parse_reset
    ms = 1787000000000
    iso = _parse_reset(ms)
    assert iso.startswith("2026-")
    assert _parse_reset(-1) == ""
    assert _parse_reset(None) == ""


def test_parse_string_numbers():
    """limit/remaining 为字符串数字时兼容。"""
    from providers.kimi import parse_usages
    d = parse_usages({"limits": [{"detail": {"limit": "100", "remaining": "30",
                                             "resetTime": 0}}],
                      "usage": {"limit": "500", "remaining": 400}})
    assert d["windows"][0]["used"] == 70
    assert d["windows"][0]["reset_at"] == ""
    assert d["windows"][1]["used"] == 100


def test_fetch_unconfigured(monkeypatch):
    import providers.kimi as km
    monkeypatch.setattr(km.config, "get_api_keys", lambda: {})
    r = km.KimiProvider().fetch()
    assert r["status"] == "unconfigured"
    assert r["key"] == "kimi"


def test_fetch_success(monkeypatch):
    import providers.kimi as km
    monkeypatch.setattr(km.config, "get_api_keys", lambda: {"kimi": "sk-xxx"})

    class FakeResp:
        status_code = 200
        def json(self):
            return SAMPLE
        def raise_for_status(self):
            pass

    monkeypatch.setattr(km, "_http_get", lambda url, headers, timeout: FakeResp())
    r = km.KimiProvider().fetch()
    assert r["status"] == "ok"
    assert len(r["data"]["windows"]) == 2


def test_fetch_empty_windows_error(monkeypatch):
    """limits/usage 全空 → error(而非 ok 空卡片)。"""
    import providers.kimi as km
    monkeypatch.setattr(km.config, "get_api_keys", lambda: {"kimi": "sk-x"})

    class FakeResp:
        status_code = 200
        def json(self):
            return {}
        def raise_for_status(self):
            pass

    monkeypatch.setattr(km, "_http_get", lambda url, headers, timeout: FakeResp())
    r = km.KimiProvider().fetch()
    assert r["status"] == "error"


def test_fetch_401_auth(monkeypatch):
    import providers.kimi as km
    monkeypatch.setattr(km.config, "get_api_keys", lambda: {"kimi": "sk-bad"})

    class FakeResp:
        status_code = 401
        text = "unauthorized"

    monkeypatch.setattr(km, "_http_get", lambda url, headers, timeout: FakeResp())
    r = km.KimiProvider().fetch()
    assert r["status"] == "error"
    assert r["error_kind"] == "auth"
