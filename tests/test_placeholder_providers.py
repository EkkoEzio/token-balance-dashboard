from providers.qianwen import QianwenProvider
from providers.minimax import MiniMaxProvider


def test_qianwen_placeholder():
    p = QianwenProvider()
    r = p.fetch()
    assert r["status"] == "unconfigured"
    assert r["key"] == "qianwen"
    assert r["name"] == "千问 Token Plan"


def test_minimax_placeholder():
    p = MiniMaxProvider()
    r = p.fetch()
    assert r["status"] == "unconfigured"
    assert r["key"] == "minimax"
    assert r["name"] == "MiniMax Token Plan"
