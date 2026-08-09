from providers.base import Provider, STATUS_OK, ERROR_KINDS, classify_request_exc


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


def test_provider_error_default_kind():
    class Dummy(Provider):
        key = "dummy"
        name = "测试"

    d = Dummy()
    r = d.error("boom")
    assert r["status"] == "error"
    assert r["error"] == ERROR_KINDS["unknown"]  # 人话,非原始 boom
    assert r["error_kind"] == "unknown"
    assert r["error_detail"] == "boom"  # 原始保留


def test_provider_error_classified_kind():
    class Dummy(Provider):
        key = "dummy"
        name = "测试"

    d = Dummy()
    r = d.error("401 Client Error", kind="auth")
    assert r["error"] == ERROR_KINDS["auth"]
    assert r["error_kind"] == "auth"
    assert r["error_detail"] == "401 Client Error"


def test_classify_dns_failure_is_blocked():
    """DNS 解析失败(域名被墙)归为 blocked,不是普通 network。"""
    import requests
    # 构造一个带 NameResolution 字样的 ConnectionError
    e = requests.exceptions.ConnectionError(
        "HTTPSConnection: Failed to resolve 'api.tavly.com' "
        "(NameResolutionError / gaierror / NXDOMAIN)")
    assert classify_request_exc(e) == "blocked"


def test_classify_ssl_reset_is_blocked():
    """TLS 握手被重置(GFW 干扰)也归为 blocked。"""
    import requests
    e = requests.exceptions.SSLError(
        "HTTPSConnectionPool: Max retries exceeded "
        "(Caused by SSLError(SSLEOFError(8, '[SSL: UNEXPECTED_EOF_WHILE_READING]')))")
    assert classify_request_exc(e) == "blocked"


def test_classify_timeout_is_network():
    import requests
    assert classify_request_exc(requests.exceptions.Timeout("timed out")) == "network"


def test_classify_http_401_is_auth():
    import requests
    err = requests.exceptions.HTTPError("401")
    err.response = type("R", (), {"status_code": 401})()
    assert classify_request_exc(err) == "auth"
