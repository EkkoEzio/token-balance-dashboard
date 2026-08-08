"""读 Edge 浏览器 cookie。复用 B站项目模式:yt-dlp cookiesfrombrowser。
带 5 分钟缓存(Edge 读 cookie 较耗时)。"""
import time

# 缓存: {domain: {"cj": ..., "ts": ...}}
_cache = {}


def _read_from_edge(domain: str):
    """实际读 Edge cookie。返回 cookiejar 或抛异常。
    用 yt-dlp 提取后过滤目标 domain。"""
    import yt_dlp
    import http.cookiejar
    cj_all = http.cookiejar.CookieJar()
    with yt_dlp.YoutubeDL({"quiet": True, "cookiesfrombrowser": ("edge",)}) as ydl:
        # ydl.cookiejar 已加载所有 Edge cookie
        for c in ydl.cookiejar:
            if domain in (c.domain or ""):
                cj_all.set_cookie(c)
    return cj_all if any(True for _ in cj_all) else None


def get_cookiejar(domain: str):
    """返回目标 domain 的 cookiejar,5 分钟缓存。失败返回 None。"""
    cached = _cache.get(domain)
    if cached and time.time() - cached["ts"] < 300:
        return cached["cj"]
    try:
        cj = _read_from_edge(domain)
        _cache[domain] = {"cj": cj, "ts": time.time()}
        return cj
    except Exception:
        return None


def clear_cache(domain: str = None) -> None:
    """清缓存(登录失效后强制重读)。domain=None 清全部。"""
    if domain:
        _cache.pop(domain, None)
    else:
        _cache.clear()
