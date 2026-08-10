"""Tavly 用量查询。公开 API,GET /usage,Bearer 鉴权。
支持多 key(逗号分隔),同卡片展示多个账号额度。每月1号重置。

国内网络注意:api.tavly.com 常被 Clash 的 fake-ip 规则拦截(SSL EOF)。
本 provider 在普通请求失败时,自动用 DoH 解析真实 IP,用 IP+Host 头走代理重试。
"""
import requests
import config
from providers.base import Provider, STATUS_OK, ERROR_KINDS, classify_request_exc

USAGE_HOST = "api.tavly.com"
USAGE_URL = f"https://{USAGE_HOST}/usage"
# DoH 端点:用 Google(8.8.8.8),对 tavly 解析比 Cloudflare 稳定
DOH_URL = "https://8.8.8.8/resolve"
# Tavly DNS(eftydns)极不稳定,常间歇性 NXDOMAIN。
# 这里硬编码已知的真实 AWS IP 作为兜底(DoH 失败时用)。IP 变动频率低,定期更新。
_FALLBACK_IPS = ["52.206.237.53", "52.44.243.183", "100.25.28.241"]

_http_get = requests.get


def _human_for_kind(kind: str) -> str:
    """卡片内展示的人话(DNS 失败特别提示代理)。"""
    if kind == "blocked":
        return "无法连接(域名被墙,需开代理/VPN)"
    return ERROR_KINDS.get(kind, ERROR_KINDS["unknown"])


def _looks_blocked(e) -> bool:
    """判断异常是否是「被墙」(fake-ip 拦截 / DNS 污染 / TLS 重置)。
    这类值得用 DoH 真实 IP 重试;普通超时/401 不值得。"""
    msg = str(e)
    return ("NameResolution" in msg or "gaierror" in msg or "NXDOMAIN" in msg
            or "UNEXPECTED_EOF" in msg or "SSLEOFError" in msg
            or "SSLError" in msg or "ConnectionResetError" in msg
            or "Connection reset" in msg)


def _proxy_dict() -> dict | None:
    """探测本地 Clash 代理端口,返回 requests proxies 字典。无代理返回 None。"""
    import socket
    for port in (7890, 7891, 7892):
        s = socket.socket(); s.settimeout(0.3)
        try:
            s.connect(("127.0.0.1", port))
            s.close()
            return {"http": f"http://127.0.0.1:{port}", "https": f"http://127.0.0.1:{port}"}
        except Exception:
            pass
        finally:
            try: s.close()
            except Exception: pass
    return None


def _resolve_real_ip() -> str | None:
    """用 Google DoH 解析 api.tavly.com 的真实 IP,绕过本地 fake-ip。
    走代理(若有)。DoH 偶发 NXDOMAIN,重试 3 次。失败返回 None。"""
    px = _proxy_dict()
    for attempt in range(3):
        try:
            r = requests.get(DOH_URL, params={"name": USAGE_HOST, "type": "A"},
                             headers={"accept": "application/dns-json"},
                             proxies=px, timeout=6)
            d = r.json()
            if d.get("Status") == 0:
                for ans in d.get("Answer", []):
                    if ans.get("type") == 1:  # A 记录
                        return ans.get("data")
        except Exception:
            pass
    return None


def _fetch_one(key: str) -> dict:
    """查单个 key 的用量。先正常域名请求;若被墙,依次试:
    DoH 真实 IP → 硬编码兜底 IP,均配合代理 + Host 头。失败抛异常。"""
    headers = {"Authorization": f"Bearer {key}"}
    try:
        resp = _http_get(USAGE_URL, headers=headers, timeout=15)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        if not _looks_blocked(e):
            raise  # 非被墙错误(401/超时)直接抛
    # 被墙:收集候选 IP(DoH + 硬编码兜底)
    candidates = []
    doh_ip = _resolve_real_ip()
    if doh_ip:
        candidates.append(doh_ip)
    for ip in _FALLBACK_IPS:
        if ip not in candidates:
            candidates.append(ip)
    px = _proxy_dict()
    last_err = None
    for ip in candidates:
        try:
            resp = _http_get(f"https://{ip}/usage",
                             headers={**headers, "Host": USAGE_HOST},
                             proxies=px, timeout=12, verify=False)
            resp.raise_for_status()
            return resp.json()
        except Exception as e2:
            last_err = e2
            continue
    raise last_err or Exception("所有 IP 均失败")


def parse_usage(raw: dict) -> dict:
    """把 /usage 响应解析成单个账号的展示数据。
    remaining = limit - usage(limit 为 null 视为无限)。"""
    account = raw.get("account") or {}
    plan_usage = account.get("plan_usage", 0)
    plan_limit = account.get("plan_limit")
    remaining = (plan_limit - plan_usage) if plan_limit is not None else None
    return {
        "plan": account.get("current_plan", ""),
        "used": plan_usage,
        "total": plan_limit,  # None = 无限
        "remaining": remaining,
        "reset_note": "每月1号重置",
    }


class TavlyProvider(Provider):
    key = "tavly"
    name = "Tavly"
    refresh_interval = 300  # 5 分钟

    def _get_keys(self) -> list:
        """返回 tavly 的 key 列表(支持逗号分隔多 key)。"""
        val = config.get_api_keys().get("tavly", "")
        if not val:
            return []
        # 支持逗号或换行分隔多个 key
        return [k.strip() for k in val.replace("\n", ",").split(",") if k.strip()]

    def fetch(self) -> dict:
        keys = self._get_keys()
        if not keys:
            return self.unconfigured()
        # 抑制 IP 直连时的 SSL 证书警告(证书是签给域名的)
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        accounts = []
        errors = []
        last_kind = "unknown"
        for i, key in enumerate(keys, 1):
            label = f"账号{i}" if len(keys) > 1 else ""
            try:
                data = parse_usage(_fetch_one(key))
                data["label"] = label
                accounts.append(data)
            except Exception as e:
                # 归类成人话(卡片内展示)
                last_kind = classify_request_exc(e)
                errors.append(f"{label or '账号'}: {_human_for_kind(last_kind)}")
        if not accounts:
            # 全部失败:用最后一个错误的 kind 定主状态
            return self.error("; ".join(errors) or "查询失败", kind=last_kind)
        data = {"accounts": accounts}
        if errors:
            data["errors"] = errors
        return self._wrap(STATUS_OK, data)
