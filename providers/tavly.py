"""Tavly 用量查询。公开 API,GET /usage,Bearer 鉴权。
支持多 key(逗号分隔),同卡片展示多个账号额度。每月1号重置。

国内网络注意:api.tavly.com 常被 Clash 的 fake-ip 规则拦截(SSL EOF)。
本 provider 在普通请求失败时,自动用 DoH 解析真实 IP,用 IP+Host 头走代理重试。

限流防护(Tavly 文档未公开 rate limit,但实测 429 触发后会封一段时间):
- 同一 key 60 秒内复用缓存(MIN_INTERVAL)
- 任一 key 触发 429 后整 provider 进入 10 分钟惩罚期(PENALTY)
- 惩罚期内 fetch 返回「上次成功数据 + 警告字段」,不打 API
- 多 key 串行请求,key 间 sleep 2 秒避免并发撞限流
- 启动首 fetch 故意延迟 5 秒(避开开发反复重启的密集触发)
"""
import time
import threading
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


def _next_month_first_iso() -> str:
    """Tavly 每月 1 号重置。返回下个月 1 号 00:00 的 ISO 时间。"""
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    # 下个月 1 号:本月 1 号 + 1 个月
    if now.month == 12:
        next_first = datetime(now.year + 1, 1, 1, tzinfo=timezone.utc)
    else:
        next_first = datetime(now.year, now.month + 1, 1, tzinfo=timezone.utc)
    return next_first.isoformat()


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
        "reset_at": _next_month_first_iso(),  # 下个月1号倒计时
    }


class TavlyProvider(Provider):
    key = "tavly"
    name = "Tavly"
    refresh_interval = 300  # 5 分钟

    # 限流防护参数(类级别,所有实例共享一份状态)
    MIN_INTERVAL = 60        # 同一 key 60 秒内复用缓存
    PENALTY_SECONDS = 600    # 触发 429 后整 provider 冷却 10 分钟
    KEY_INTERVAL = 2         # 多 key 串行,key 间间隔(秒)
    STARTUP_DELAY = 5        # 启动首 fetch 延迟,避开开发反复重启

    # 类级别状态(进程内单实例,无需 Lock 严格同步——fetch 失败/成功标志用原子赋值即可)
    _cache: dict = {}              # {key: {"data": dict, "ts": float}}
    _last_fetch_ts: float = 0.0    # 任一 key 上次实际打 API 的时间
    _penalty_until: float = 0.0    # 惩罚期截止时间戳
    _lock = threading.Lock()
    _started_at: float = time.monotonic()  # 模块导入时间,用于 STARTUP_DELAY

    def _get_keys(self) -> list:
        """返回 tavly 的 key 列表(支持逗号分隔多 key)。"""
        val = config.get_api_keys().get("tavly", "")
        if not val:
            return []
        # 支持逗号或换行分隔多个 key
        return [k.strip() for k in val.replace("\n", ",").split(",") if k.strip()]

    def _in_penalty(self) -> bool:
        return time.monotonic() < self._penalty_until

    def _enter_penalty(self):
        self._penalty_until = time.monotonic() + self.PENALTY_SECONDS

    def fetch(self) -> dict:
        keys = self._get_keys()
        if not keys:
            return self.unconfigured()

        now = time.monotonic()

        # 启动延迟:模块刚加载时(进程刚起),故意等待 STARTUP_DELAY 秒
        # 防止开发时反复重启 Flask 触发密集 fetch
        startup_elapsed = now - self._started_at
        if startup_elapsed < self.STARTUP_DELAY:
            time.sleep(self.STARTUP_DELAY - startup_elapsed)
            now = time.monotonic()

        with self._lock:
            in_penalty = self._in_penalty()
            cache_snapshot = dict(self._cache)  # 拷贝出来,锁外用

        # 惩罚期:不打 API,直接基于上次缓存构建 fallback 响应
        if in_penalty:
            return self._build_fallback(keys, cache_snapshot, in_penalty=True)

        # 抑制 IP 直连时的 SSL 证书警告
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

        accounts = []
        errors = []
        last_kind = "unknown"
        any_fetched = False

        for i, key in enumerate(keys, 1):
            label = f"账号{i}" if len(keys) > 1 else ""

            # per-key 缓存:距上次成功 < MIN_INTERVAL → 复用
            with self._lock:
                cached = self._cache.get(key)
                need_fetch = (
                    cached is None
                    or (time.monotonic() - cached["ts"]) >= self.MIN_INTERVAL
                )

            if not need_fetch and cached:
                # 复用缓存,跳过 HTTP 请求
                data = dict(cached["data"])
                data["label"] = label
                accounts.append(data)
                continue

            # 多 key 串行,key 间 sleep
            if i > 1 and any_fetched:
                time.sleep(self.KEY_INTERVAL)

            try:
                data = parse_usage(_fetch_one(key))
                data["label"] = label
                accounts.append(data)
                # 写入 per-key 缓存
                with self._lock:
                    self._cache[key] = {"data": data, "ts": time.monotonic()}
                    self._last_fetch_ts = time.monotonic()
                any_fetched = True
            except requests.exceptions.HTTPError as e:
                code = e.response.status_code if e.response is not None else 0
                last_kind = classify_request_exc(e)
                if code == 429:
                    # 触发限流:进入惩罚期,后续 key 直接走 fallback
                    with self._lock:
                        self._enter_penalty()
                    errors.append(f"{label or '账号'}: 请求过频,已暂停 {self.PENALTY_SECONDS//60} 分钟")
                    # 用已缓存的 fallback 兜底填剩余 key
                    for j, k2 in enumerate(keys[i:], start=i):
                        label2 = f"账号{j+1}" if len(keys) > 1 else ""
                        with self._lock:
                            c = self._cache.get(k2)
                        if c:
                            d = dict(c["data"])
                            d["label"] = label2
                            accounts.append(d)
                    break
                errors.append(f"{label or '账号'}: {_human_for_kind(last_kind)}")
                # 用缓存兜底这一格(让 UI 不至于全空白)
                with self._lock:
                    c = self._cache.get(key)
                if c:
                    d = dict(c["data"])
                    d["label"] = label
                    accounts.append(d)
            except Exception as e:
                last_kind = classify_request_exc(e)
                errors.append(f"{label or '账号'}: {_human_for_kind(last_kind)}")
                # 用缓存兜底
                with self._lock:
                    c = self._cache.get(key)
                if c:
                    d = dict(c["data"])
                    d["label"] = label
                    accounts.append(d)

        if not accounts:
            # 所有 key 都没数据(也没缓存):返回错误
            return self.error("; ".join(errors) or "查询失败", kind=last_kind)

        data = {"accounts": accounts}
        if errors:
            data["errors"] = errors
        # 在惩罚期:status 仍标 ok(数据是真实的),但加个 warning 让前端知道
        with self._lock:
            if self._in_penalty():
                data["warning"] = "rate_limited"
        return self._wrap(STATUS_OK, data)

    def _build_fallback(self, keys: list, cache_snapshot: dict, in_penalty: bool) -> dict:
        """惩罚期:不打 API,从缓存拼一份 fallback 响应(每个 key 一格)。"""
        accounts = []
        missing = []
        for i, key in enumerate(keys, 1):
            label = f"账号{i}" if len(keys) > 1 else ""
            c = cache_snapshot.get(key)
            if c:
                d = dict(c["data"])
                d["label"] = label
                accounts.append(d)
            else:
                missing.append(label or f"账号{i}")

        data: dict = {"accounts": accounts}
        if in_penalty:
            with self._lock:
                remain = max(0, int(self._penalty_until - time.monotonic()))
            data["warning"] = "rate_limited"
            data["warning_msg"] = f"Tavly 限流冷却中,剩余 {remain // 60} 分 {remain % 60} 秒"
        if missing:
            data["errors"] = [f"{m}: 无缓存数据" for m in missing]
        return self._wrap(STATUS_OK, data)
