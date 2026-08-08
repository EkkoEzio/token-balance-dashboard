from providers.base import Provider, STATUS_OK


def test_provider_base_fetch_contract():
    """Provider 子类 fetch 必须返回带 name/key/status/updated_at 的 dict。"""

    class Dummy(Provider):
        key = "dummy"
        name = "测试"
        refresh_interval = 120

        def fetch(self):
            return self._wrap(STATUS_OK, {"foo": 1})

    d = Dummy()
    r = d.fetch()
    assert r["key"] == "dummy"
    assert r["name"] == "测试"
    assert r["status"] == "ok"
    assert r["data"] == {"foo": 1}
    assert "updated_at" in r


def test_provider_unconfigured_when_no_key():
    class Dummy(Provider):
        key = "dummy"
        name = "测试"
        refresh_interval = 120

    d = Dummy()
    r = d.unconfigured()
    assert r["status"] == "unconfigured"
    assert r["key"] == "dummy"


def test_provider_error():
    class Dummy(Provider):
        key = "dummy"
        name = "测试"

    d = Dummy()
    r = d.error("boom")
    assert r["status"] == "error"
    assert r["error"] == "boom"
