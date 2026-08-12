from providers.minimax import parse_remains, MiniMaxProvider


# 真实结构:model_remains 数组,general + video
SAMPLE = {
    "model_remains": [
        {
            "start_time": 1786518000000, "end_time": 1786536000000,
            "current_interval_total_count": 0, "current_interval_usage_count": 0,
            "model_name": "general",
            "current_weekly_total_count": 0, "current_weekly_usage_count": 0,
            "weekly_start_time": 1786291200000, "weekly_end_time": 1786896000000,
            "current_interval_remaining_percent": 99,
            "current_weekly_remaining_percent": 98,
            "weekly_boost_permille": 1500,
        },
        {
            "model_name": "video",
            "current_interval_total_count": 3, "current_interval_remaining_percent": 100,
            "current_weekly_remaining_percent": 100,
        },
    ],
    "base_resp": {"status_code": 0, "status_msg": ""},
}


def test_parse_general_model_with_boost():
    """取 general 模型,周窗口含 boost 加成(1500 → 总额150%)。"""
    d = parse_remains(SAMPLE)
    assert len(d["windows"]) == 2
    w5 = d["windows"][0]
    assert w5["label"] == "5小时"
    assert w5["total"] == 100
    assert w5["percentage"] == 1.0  # 100 - 99 = 已用1%
    assert w5["remaining"] == 99
    ww = d["windows"][1]
    assert ww["label"] == "7天"
    assert ww["total"] == 150  # 100 + 50 boost
    assert ww["percentage"] == 2.0  # 100 - 98 = 已用2%
    assert ww["remaining"] == 148  # 150 - 2
    assert ww["unit"] == "%"


def test_parse_no_boost():
    """无 boost 字段(默认1000)时,周额度 = 100%。"""
    d = parse_remains({"model_remains": [{"model_name": "general",
        "current_interval_remaining_percent": 50, "current_weekly_remaining_percent": 50,
        "end_time": 1786536000000, "weekly_end_time": 1786896000000}]})
    ww = d["windows"][1]
    assert ww["total"] == 100
    assert ww["remaining"] == 50
    assert ww["percentage"] == 50


def test_parse_has_video_flag():
    """有 video 模型时 has_video=True。"""
    d = parse_remains(SAMPLE)
    assert d["has_video"] is True


def test_parse_no_video():
    """只有 general 时 has_video=False。"""
    d = parse_remains({"model_remains": [{"model_name": "general",
        "current_interval_remaining_percent": 50, "current_weekly_remaining_percent": 50,
        "end_time": 1786536000000, "weekly_end_time": 1786896000000}]})
    assert d["has_video"] is False


def test_parse_empty():
    d = parse_remains({})
    assert d["windows"] == []
    assert d["has_video"] is False


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
        def json(self): return SAMPLE
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