from providers.qianwen import parse_qianwen


USAGE = {"per1WeekResetTime": 1786886760000, "per1WeekPercentage": 0.043377078}
SUBSCRIPTION = {"specCode": "standard", "remainingDays": 14,
                "startTime": 1784871977000, "endTime": 1787587200000,
                "autoRenewFlag": False, "status": "VALID"}
QUOTA_CONFIG = {
    "standard": {"five_hour": 3000.0, "weekly": 10000.0},
    "lite": {"five_hour": 700.0, "weekly": 2500.0},
    "pro": {"five_hour": 12000.0, "weekly": 40000.0},
    "addon_quota": {"extrabundle": 20000.0},
}


def test_parse_qianwen_standard():
    d = parse_qianwen(USAGE, SUBSCRIPTION, QUOTA_CONFIG)
    assert d["level"] == "standard"
    assert d["remaining_days"] == 14
    assert d["status"] == "VALID"
    assert d["expires_at"].startswith("2026-")
    # 周窗口:standard weekly=10000, 4.3% used
    assert len(d["windows"]) == 1
    w = d["windows"][0]
    assert w["label"] == "7天"
    assert w["total"] == 10000
    assert w["used"] == 434  # round(10000 * 0.043377078)
    assert w["remaining"] == 9566
    assert w["percentage"] == 4.3
    assert w["reset_at"].startswith("2026-")


def test_parse_qianwen_no_quota():
    """quota-config 缺失时,total=0,不崩。"""
    d = parse_qianwen(USAGE, SUBSCRIPTION, {})
    w = d["windows"][0]
    assert w["total"] == 0
    assert w["used"] == 0


def test_parse_qianwen_empty():
    """三个接口都没数据(全空)。"""
    d = parse_qianwen({}, {}, {})
    assert d["level"] == ""
    assert d["windows"] == []
    assert d["remaining_days"] is None
