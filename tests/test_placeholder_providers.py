from providers.minimax import MiniMaxProvider


def test_minimax_placeholder():
    p = MiniMaxProvider()
    r = p.fetch()
    assert r["status"] == "unconfigured"
    assert r["key"] == "minimax"
    assert r["name"] == "MiniMax Token Plan"
