import cookie_jar


def test_get_cookiejar_returns_none_on_failure(monkeypatch):
    """ytdlp 不可用/读取失败时返回 None,不抛异常。"""
    def boom(domain):
        raise RuntimeError("simulated")
    monkeypatch.setattr(cookie_jar, "_read_from_edge", boom)
    monkeypatch.setattr(cookie_jar, "_cache", {})
    assert cookie_jar.get_cookiejar("qianwenai.com") is None


def test_get_cookiejar_caches(monkeypatch):
    """5 分钟内同一域名只读一次 Edge。"""
    calls = []

    def fake_read(domain):
        calls.append(domain)
        return {"SESSDATA": "x"}  # 非None即视为成功

    monkeypatch.setattr(cookie_jar, "_read_from_edge", fake_read)
    monkeypatch.setattr(cookie_jar, "_cache", {})
    cookie_jar.get_cookiejar("a.com")
    cookie_jar.get_cookiejar("a.com")
    assert len(calls) == 1  # 第二次走缓存


def test_clear_cache(monkeypatch):
    monkeypatch.setattr(cookie_jar, "_read_from_edge", lambda d: {"x": 1})
    monkeypatch.setattr(cookie_jar, "_cache", {})
    cookie_jar.get_cookiejar("a.com")
    assert "a.com" in cookie_jar._cache
    cookie_jar.clear_cache("a.com")
    assert "a.com" not in cookie_jar._cache
