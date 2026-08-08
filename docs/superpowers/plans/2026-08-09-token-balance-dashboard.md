# Token 余额看板 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 本地 Flask 看板,一眼同时看到 DeepSeek 余额和智谱 Coding Plan 的 5h/7d 额度,取代开多个平台网页反复刷新。

**Architecture:** Flask 后端 + Alpine.js 前端(无构建)。每家平台一个 provider 适配器,各吐自己的原生字段。DeepSeek/智谱走 API Key;千问/MiniMax 预留位走 Edge cookie(yt-dlp cookiesfrombrowser)。调度器按各家独立间隔定时拉取,缓存到内存,前端轮询展示。

**Tech Stack:** Python 3 / Flask / requests / yt-dlp / Alpine.js(CDN) / pytest

## Global Constraints

- 端口固定 5060(挨着 B站项目的 5050)
- 沿用用户 B站项目风格:Python + Flask + `config.py` 热更新 + `data/` 存 JSON + `.command` 启动 + `import config` 实时读运行时变量 + light/dark/auto 主题
- API Key 存 `data/config.json`,看板设置页可改(热更新,不重启)
- provider 各吐原生字段,不强统一 schema。base.py 只约束最小接口
- 一期只实现 DeepSeek + 智谱两家;千问/MiniMax 是占位 provider(返回 unconfigured),抓包后再补
- 智谱鉴权:`Authorization: <KEY>` **不加 Bearer 前缀**;DeepSeek 鉴权:`Authorization: Bearer <KEY>`
- TDD:每个解析/逻辑函数先写测试。网络函数可注入替换(`_http_get` 模式,沿用 B站)
- 中文注释,与 B站项目一致

## File Structure

```
token余额看板/
├─ app.py                  Flask 路由层(薄)
├─ config.py               配置 + API Key 热更新(复用 B站模式)
├─ cookie_jar.py           读 Edge cookie(yt-dlp,通用)
├─ scheduler.py            定时拉取 + 内存缓存
├─ 启动.command            双击启动(复用 B站 .command)
├─ requirements.txt
├─ .gitignore
├─ data/                   config.json + 历史快照(运行时生成)
├─ providers/
│   ├─ __init__.py
│   ├─ base.py             Provider 基类(最小契约)
│   ├─ deepseek.py         /user/balance
│   ├─ zhipu.py            /monitor/usage/quota/limit
│   ├─ qianwen.py          占位(unconfigured)
│   └─ minimax.py          占位(unconfigured)
├─ templates/
│   └─ index.html          看板(Alpine.js,内联 CSS/JS)
└─ tests/
    ├─ test_deepseek.py
    ├─ test_zhipu.py
    ├─ test_config.py
    └─ test_scheduler.py
```

**职责边界:**
- `config.py` — 唯一的配置读写出口。API Key、主题、端口。热更新。
- `cookie_jar.py` — 唯一读 Edge cookie 的地方。带 5 分钟缓存。
- `providers/base.py` — Provider 抽象,只定义 `fetch()` 契约和 status 枚举。
- `providers/<name>.py` — 每家解析逻辑 + 网络调用。网络函数可注入。
- `scheduler.py` — 拉取调度 + 缓存。不知道各家的字段细节。
- `app.py` — 路由,委托给 scheduler/config,不含业务逻辑。

---

### Task 1: 项目骨架 + config.py

**Files:**
- Create: `config.py`
- Create: `requirements.txt`
- Create: `.gitignore`
- Create: `tests/test_config.py`
- Create: `tests/__init__.py`(空)

**Interfaces:**
- Produces: `config.PORT` (int, 5060), `config.DATA_DIR` (Path), `config.CONFIG_FILE` (Path), `config.get_api_keys() -> dict`, `config.set_api_key(provider, key) -> dict`, `config.get_theme() -> str`, `config.set_theme(theme) -> dict`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_config.py
import json
from pathlib import Path
import config


def test_get_api_keys_default_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    assert config.get_api_keys() == {}


def test_set_and_get_api_key(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    r = config.set_api_key("deepseek", "sk-abc123")
    assert r["ok"] is True
    assert config.get_api_keys() == {"deepseek": "sk-abc123"}
    # 持久化
    raw = json.loads((tmp_path / "config.json").read_text("utf-8"))
    assert raw["api_keys"]["deepseek"] == "sk-abc123"


def test_set_theme_validates(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    assert config.set_theme("dark")["ok"] is True
    assert config.set_theme("purple")["ok"] is False
    assert config.get_theme() == "dark"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/hushen/Desktop/token余额看板 && python -m pytest tests/test_config.py -v`
Expected: FAIL — `config` 模块属性/函数未定义或 ModuleNotFoundError。

- [ ] **Step 3: Write minimal implementation**

```python
# config.py
"""路径与配置。所有模块统一从这里取路径,避免硬编码。
沿用 B站项目模式:运行时可变配置用 config.XXX 实时读。
"""
import json
import threading
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

CONFIG_FILE = DATA_DIR / "config.json"
PORT = 5060

_DEFAULT_THEME = "auto"
VALID_THEMES = ("light", "dark", "auto")


def _read() -> dict:
    if not CONFIG_FILE.exists():
        return {}
    try:
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write(d: dict) -> None:
    CONFIG_FILE.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")


_lock = threading.Lock()


def get_api_keys() -> dict:
    """返回所有 provider 的 API Key。"""
    return _read().get("api_keys", {})


def set_api_key(provider: str, key: str) -> dict:
    """设置某 provider 的 API Key,持久化。"""
    with _lock:
        cur = _read()
        cur.setdefault("api_keys", {})[provider] = key.strip()
        try:
            _write(cur)
        except Exception as e:
            return {"ok": False, "error": str(e)}
    return {"ok": True}


def get_theme() -> str:
    t = _read().get("theme", _DEFAULT_THEME)
    return t if t in VALID_THEMES else _DEFAULT_THEME


def set_theme(theme: str) -> dict:
    if theme not in VALID_THEMES:
        return {"ok": False, "error": "主题无效(light/dark/auto)"}
    with _lock:
        cur = _read()
        cur["theme"] = theme
        try:
            _write(cur)
        except Exception as e:
            return {"ok": False, "error": str(e)}
    return {"ok": True}
```

```python
# tests/__init__.py
# (空文件)
```

```python
# requirements.txt
flask>=3.0
requests>=2.31
yt-dlp>=2026.0
pytest>=8.0
```

```python
# .gitignore
.venv/
__pycache__/
.pytest_cache/
data/config.json
data/*.json
.DS_Store
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/hushen/Desktop/token余额看板 && python -m pytest tests/test_config.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
cd /Users/hushen/Desktop/token余额看板
git init
git add config.py requirements.txt .gitignore tests/__init__.py tests/test_config.py
git commit -m "feat: 项目骨架 + config.py(API Key/主题热更新)"
```

---

### Task 2: Provider 基类

**Files:**
- Create: `providers/__init__.py`(空)
- Create: `providers/base.py`
- Create: `tests/test_providers_base.py`

**Interfaces:**
- Produces: `providers.base.Provider` (基类), 属性 `key/name/refresh_interval`, 方法 `fetch() -> dict`, status 常量 `STATUS_OK/UNCONFIGURED/EXPIRED/ERROR`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_providers_base.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_providers_base.py -v`
Expected: FAIL — ModuleNotFoundError

- [ ] **Step 3: Write minimal implementation**

```python
# providers/__init__.py
# (空)
```

```python
# providers/base.py
"""Provider 基类:各家适配器的最小契约。
不强统一字段,每家 fetch 返回自己的原生数据。"""
import time
from datetime import datetime, timezone


STATUS_OK = "ok"
STATUS_UNCONFIGURED = "unconfigured"
STATUS_EXPIRED = "expired"
STATUS_ERROR = "error"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class Provider:
    """子类需设置 key/name/refresh_interval,并实现 fetch()。"""
    key: str = ""
    name: str = ""
    refresh_interval: int = 300  # 秒,子类可覆盖

    def fetch(self) -> dict:
        """子类实现。返回 _wrap(status, data) 或 unconfigured()/error(e)。"""
        raise NotImplementedError

    def _wrap(self, status: str, data: dict) -> dict:
        """统一外层壳:每家数据塞进 data,各家自由。"""
        return {
            "key": self.key,
            "name": self.name,
            "status": status,
            "data": data,
            "updated_at": _now_iso(),
        }

    def unconfigured(self) -> dict:
        return {
            "key": self.key,
            "name": self.name,
            "status": STATUS_UNCONFIGURED,
            "data": {},
            "updated_at": _now_iso(),
        }

    def error(self, message: str) -> dict:
        return {
            "key": self.key,
            "name": self.name,
            "status": STATUS_ERROR,
            "data": {},
            "error": message,
            "updated_at": _now_iso(),
        }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_providers_base.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add providers/__init__.py providers/base.py tests/test_providers_base.py
git commit -m "feat: Provider 基类(最小契约 + status 枚举)"
```

---

### Task 3: DeepSeek provider

**Files:**
- Create: `providers/deepseek.py`
- Create: `tests/test_deepseek.py`

**Interfaces:**
- Consumes: `config.get_api_keys()` (Task 1), `providers.base.Provider` (Task 2)
- Produces: `providers.deepseek.DeepSeekProvider`, 方法 `fetch()`, `parse_balance(raw_json) -> dict`

**端点**: `GET https://api.deepseek.com/user/balance`, header `Authorization: Bearer <KEY>`
响应示例:
```json
{"is_available": true, "balance_infos": [{"currency": "CNY", "total_balance": "110.00", "granted_balance": "10.00", "topped_up_balance": "100.00"}]}
```

- [ ] **Step 1: Write the failing test**

```python
# tests/test_deepseek.py
from providers.deepseek import DeepSeekProvider, parse_balance


SAMPLE = {
    "is_available": True,
    "balance_infos": [
        {"currency": "CNY", "total_balance": "110.00",
         "granted_balance": "10.00", "topped_up_balance": "100.00"}
    ],
}


def test_parse_balance_basic():
    d = parse_balance(SAMPLE)
    assert d["is_available"] is True
    assert d["total_balance"] == "110.00"
    assert d["granted_balance"] == "10.00"
    assert d["topped_up_balance"] == "100.00"
    assert d["currency"] == "CNY"


def test_parse_balance_missing_infos():
    """balance_infos 为空时不崩,返回 is_available=False。"""
    d = parse_balance({"is_available": False, "balance_infos": []})
    assert d["is_available"] is False
    assert d["total_balance"] == "0"


def test_fetch_unconfigured_without_key(monkeypatch):
    """没存 key 时返回 unconfigured。"""
    monkeypatch.setattr("providers.deepseek.config.get_api_keys", lambda: {})
    p = DeepSeekProvider()
    r = p.fetch()
    assert r["status"] == "unconfigured"
    assert r["key"] == "deepseek"


def test_fetch_success(monkeypatch):
    """有 key 时调网络(注入 mock)并返回 ok。"""
    monkeypatch.setattr("providers.deepseek.config.get_api_keys",
                        lambda: {"deepseek": "sk-xxx"})

    class FakeResp:
        status_code = 200
        def json(self):
            return SAMPLE
        def raise_for_status(self):
            pass

    monkeypatch.setattr("providers.deepseek._http_get",
                        lambda url, headers, timeout: FakeResp())
    p = DeepSeekProvider()
    r = p.fetch()
    assert r["status"] == "ok"
    assert r["data"]["total_balance"] == "110.00"


def test_fetch_http_error(monkeypatch):
    """接口 401 时返回 error。"""
    monkeypatch.setattr("providers.deepseek.config.get_api_keys",
                        lambda: {"deepseek": "sk-bad"})

    class FakeResp:
        status_code = 401
        text = "unauthorized"
        def json(self):
            return {}
        def raise_for_status(self):
            raise Exception("401")

    monkeypatch.setattr("providers.deepseek._http_get",
                        lambda url, headers, timeout: FakeResp())
    p = DeepSeekProvider()
    r = p.fetch()
    assert r["status"] == "error"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_deepseek.py -v`
Expected: FAIL — ModuleNotFoundError

- [ ] **Step 3: Write minimal implementation**

```python
# providers/deepseek.py
"""DeepSeek 余额查询。公开 API,GET /user/balance,Bearer 鉴权。"""
import requests
import config
from providers.base import Provider, STATUS_OK

BALANCE_URL = "https://api.deepseek.com/user/balance"

# 网络函数可被测试替换(沿用 B站 _http_get 模式)
_http_get = requests.get


def parse_balance(raw: dict) -> dict:
    """把 /user/balance 响应解析成展示数据。balance_infos 为空时给默认零值。"""
    infos = raw.get("balance_infos") or []
    info = infos[0] if infos else {}
    return {
        "is_available": raw.get("is_available", False),
        "total_balance": str(info.get("total_balance", "0")),
        "granted_balance": str(info.get("granted_balance", "0")),
        "topped_up_balance": str(info.get("topped_up_balance", "0")),
        "currency": info.get("currency", "CNY"),
    }


class DeepSeekProvider(Provider):
    key = "deepseek"
    name = "DeepSeek"
    refresh_interval = 300  # 5 分钟

    def fetch(self) -> dict:
        keys = config.get_api_keys()
        key = keys.get("deepseek")
        if not key:
            return self.unconfigured()
        try:
            resp = _http_get(
                BALANCE_URL,
                headers={"Authorization": f"Bearer {key}"},
                timeout=10,
            )
            resp.raise_for_status()
            data = parse_balance(resp.json())
            return self._wrap(STATUS_OK, data)
        except Exception as e:
            return self.error(f"查询失败: {e}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_deepseek.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add providers/deepseek.py tests/test_deepseek.py
git commit -m "feat: DeepSeek provider(/user/balance 余额查询)"
```

---

### Task 4: 智谱 provider

**Files:**
- Create: `providers/zhipu.py`
- Create: `tests/test_zhipu.py`

**Interfaces:**
- Consumes: `config.get_api_keys()` (Task 1), `providers.base.Provider` (Task 2)
- Produces: `providers.zhipu.ZhipuProvider`, `parse_quota(raw_json) -> dict`

**端点**: `GET https://open.bigmodel.cn/api/monitor/usage/quota/limit`, header `Authorization: <KEY>`(**不加 Bearer**)
响应结构(社区逆向):
```json
{"code":200,"data":{"level":"pro","limits":[
  {"type":"TOKENS_LIMIT","unit":3,"percentage":75,"usage":2000,"currentValue":1500,"remaining":500,"nextResetTime":"..."},
  {"type":"TOKENS_LIMIT","unit":6,"percentage":80,"usage":10000,"currentValue":8000,"remaining":2000,"nextResetTime":"..."}
]}}
```
`unit`: 3=5小时窗口, 6=周窗口。需过滤 `type==="TOKENS_LIMIT"`。

- [ ] **Step 1: Write the failing test**

```python
# tests/test_zhipu.py
from providers.zhipu import ZhipuProvider, parse_quota, UNIT_5H, UNIT_WEEK


SAMPLE = {
    "code": 200,
    "data": {
        "level": "pro",
        "limits": [
            {"type": "TOKENS_LIMIT", "unit": UNIT_5H, "percentage": 75,
             "usage": 2000, "currentValue": 1500, "remaining": 500,
             "nextResetTime": "2026-08-09T15:00:00+08:00"},
            {"type": "TOKENS_LIMIT", "unit": UNIT_WEEK, "percentage": 80,
             "usage": 10000, "currentValue": 8000, "remaining": 2000,
             "nextResetTime": "2026-08-14T10:00:00+08:00"},
            {"type": "TIME_LIMIT", "unit": 0, "percentage": 10},  # 应被过滤
        ],
    },
}


def test_parse_quota_windows():
    d = parse_quota(SAMPLE)
    assert d["level"] == "pro"
    assert len(d["windows"]) == 2  # TIME_LIMIT 被过滤
    w5 = d["windows"][0]
    assert w5["label"] == "5小时"
    assert w5["total"] == 2000
    assert w5["used"] == 1500
    assert w5["remaining"] == 500
    assert w5["reset_at"] == "2026-08-09T15:00:00+08:00"
    ww = d["windows"][1]
    assert ww["label"] == "7天"
    assert ww["total"] == 10000


def test_parse_quota_empty_limits():
    d = parse_quota({"data": {"level": "lite", "limits": []}})
    assert d["level"] == "lite"
    assert d["windows"] == []


def test_fetch_unconfigured(monkeypatch):
    monkeypatch.setattr("providers.zhipu.config.get_api_keys", lambda: {})
    p = ZhipuProvider()
    r = p.fetch()
    assert r["status"] == "unconfigured"


def test_fetch_success(monkeypatch):
    monkeypatch.setattr("providers.zhipu.config.get_api_keys",
                        lambda: {"zhipu": "abc.def"})
    captured = {}

    class FakeResp:
        status_code = 200
        def json(self):
            return SAMPLE
        def raise_for_status(self):
            pass

    def fake_get(url, headers, timeout):
        captured["headers"] = headers
        return FakeResp()

    monkeypatch.setattr("providers.zhipu._http_get", fake_get)
    p = ZhipuProvider()
    r = p.fetch()
    assert r["status"] == "ok"
    assert r["data"]["level"] == "pro"
    # 关键:鉴权头不加 Bearer
    assert captured["headers"]["Authorization"] == "abc.def"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_zhipu.py -v`
Expected: FAIL — ModuleNotFoundError

- [ ] **Step 3: Write minimal implementation**

```python
# providers/zhipu.py
"""智谱 BigModel Coding Plan 额度查询。
内部接口 /api/monitor/usage/quota/limit,API Key 鉴权(不加 Bearer)。
unit 字段区分窗口:3=5小时,6=周。"""
import requests
import config
from providers.base import Provider, STATUS_OK

QUOTA_URL = "https://open.bigmodel.cn/api/monitor/usage/quota/limit"
UNIT_5H = 3
UNIT_WEEK = 6
_UNIT_LABEL = {UNIT_5H: "5小时", UNIT_WEEK: "7天"}

_http_get = requests.get


def parse_quota(raw: dict) -> dict:
    """把 monitor 接口响应解析成展示数据。
    过滤 type==TOKENS_LIMIT,按 unit 区分窗口。"""
    data = raw.get("data") or {}
    windows = []
    for lim in data.get("limits") or []:
        if lim.get("type") != "TOKENS_LIMIT":
            continue
        unit = lim.get("unit")
        windows.append({
            "label": _UNIT_LABEL.get(unit, f"窗口{unit}"),
            "total": lim.get("usage", 0),
            "used": lim.get("currentValue", 0),
            "remaining": lim.get("remaining", 0),
            "percentage": lim.get("percentage", 0),
            "reset_at": lim.get("nextResetTime", ""),
        })
    return {"level": data.get("level", ""), "windows": windows}


class ZhipuProvider(Provider):
    key = "zhipu"
    name = "智谱 Coding Plan"
    refresh_interval = 120  # 2 分钟,窗口型需更勤

    def fetch(self) -> dict:
        keys = config.get_api_keys()
        key = keys.get("zhipu")
        if not key:
            return self.unconfigured()
        try:
            resp = _http_get(
                QUOTA_URL,
                headers={"Authorization": key},  # 不加 Bearer
                timeout=10,
            )
            resp.raise_for_status()
            data = parse_quota(resp.json())
            return self._wrap(STATUS_OK, data)
        except Exception as e:
            return self.error(f"查询失败: {e}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_zhipu.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add providers/zhipu.py tests/test_zhipu.py
git commit -m "feat: 智谱 provider(monitor 接口 5h/7d 额度)"
```

---

### Task 5: 千问/MiniMax 占位 provider

**Files:**
- Create: `providers/qianwen.py`
- Create: `providers/minimax.py`
- Create: `tests/test_placeholder_providers.py`

**Interfaces:**
- Consumes: `providers.base.Provider` (Task 2)
- Produces: `providers.qianwen.QianwenProvider`, `providers.minimax.MiniMaxProvider`(都返回 unconfigured)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_placeholder_providers.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_placeholder_providers.py -v`
Expected: FAIL — ModuleNotFoundError

- [ ] **Step 3: Write minimal implementation**

```python
# providers/qianwen.py
"""千问 Token Plan。无公开 API,需走控制台内部接口 + Edge cookie。
一期占位:返回 unconfigured。抓包后补实现。"""
from providers.base import Provider, STATUS_UNCONFIGURED


class QianwenProvider(Provider):
    key = "qianwen"
    name = "千问 Token Plan"
    refresh_interval = 120

    def fetch(self) -> dict:
        # TODO(抓包后): 用 cookie_jar.get_cookiejar 读 Edge cookie 调控制台接口
        return {
            "key": self.key,
            "name": self.name,
            "status": STATUS_UNCONFIGURED,
            "data": {"note": "待抓包实现"},
            "updated_at": "",
        }
```

```python
# providers/minimax.py
"""MiniMax Token Plan。接口鉴权有矛盾,字段漂移。
一期占位:返回 unconfigured。抓包后补实现。"""
from providers.base import Provider, STATUS_UNCONFIGURED


class MiniMaxProvider(Provider):
    key = "minimax"
    name = "MiniMax Token Plan"
    refresh_interval = 120

    def fetch(self) -> dict:
        # TODO(抓包后): 试 /v1/token_plan/remains 和 /v1/api/openplatform/coding_plan/remains
        return {
            "key": self.key,
            "name": self.name,
            "status": STATUS_UNCONFIGURED,
            "data": {"note": "待抓包实现"},
            "updated_at": "",
        }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_placeholder_providers.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add providers/qianwen.py providers/minimax.py tests/test_placeholder_providers.py
git commit -m "feat: 千问/MiniMax 占位 provider(待抓包)"
```

---

### Task 6: cookie_jar.py(读 Edge cookie)

**Files:**
- Create: `cookie_jar.py`
- Create: `tests/test_cookie_jar.py`

**Interfaces:**
- Produces: `cookie_jar.get_cookiejar(domain: str)` — 返回 http.cookiejar 或 None。带 5 分钟缓存。用 yt-dlp `cookiesfrombrowser=("edge",)`。
- 注:千问/MiniMax 一期是占位,本 Task 为二期抓包做准备,但基础设施先到位。

- [ ] **Step 1: Write the failing test**

```python
# tests/test_cookie_jar.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_cookie_jar.py -v`
Expected: FAIL — ModuleNotFoundError

- [ ] **Step 3: Write minimal implementation**

```python
# cookie_jar.py
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_cookie_jar.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add cookie_jar.py tests/test_cookie_jar.py
git commit -m "feat: cookie_jar(读 Edge cookie + 缓存)"
```

---

### Task 7: scheduler.py(定时拉取 + 缓存)

**Files:**
- Create: `scheduler.py`
- Create: `tests/test_scheduler.py`

**Interfaces:**
- Consumes: 所有 provider (Task 2-5)
- Produces: `scheduler.start()`, `scheduler.get_all() -> list[dict]`, `scheduler.refresh_now() -> list[dict]`
- 后台线程按各 provider 的 refresh_interval 独立拉取。结果存内存 `_results`。

- [ ] **Step 1: Write the failing test**

```python
# tests/test_scheduler.py
import scheduler


def test_get_all_returns_all_providers():
    """初始化后 get_all 返回4家(即使占位也返回)。"""
    scheduler._init_providers()
    results = scheduler.get_all()
    keys = {r["key"] for r in results}
    assert keys == {"deepseek", "zhipu", "qianwen", "minimax"}


def test_refresh_now_calls_each_provider_fetch(monkeypatch):
    """refresh_now 对每个 provider 调 fetch 并更新结果。"""
    scheduler._init_providers()
    monkeypatch.setattr(scheduler._providers["deepseek"], "fetch",
                        lambda: {"key": "deepseek", "name": "DeepSeek",
                                 "status": "ok", "data": {"x": 1}, "updated_at": "t"})
    results = scheduler.refresh_now()
    ds = next(r for r in results if r["key"] == "deepseek")
    assert ds["data"] == {"x": 1}
    # get_all 能拿到刷新后的值
    assert scheduler.get_all() == results
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_scheduler.py -v`
Expected: FAIL — ModuleNotFoundError

- [ ] **Step 3: Write minimal implementation**

```python
# scheduler.py
"""定时拉取调度器。每家 provider 独立间隔,结果缓存内存。
后台线程拉取,前端轮询读缓存。"""
import threading
import time

from providers.deepseek import DeepSeekProvider
from providers.zhipu import ZhipuProvider
from providers.qianwen import QianwenProvider
from providers.minimax import MiniMaxProvider

_providers: dict[str, object] = {}
_results: list[dict] = []
_results_lock = threading.Lock()
_started = False


def _init_providers():
    """实例化所有 provider。测试可单独调。"""
    global _providers
    if _providers:
        return
    for cls in (DeepSeekProvider, ZhipuProvider, QianwenProvider, MiniMaxProvider):
        p = cls()
        _providers[p.key] = p


def refresh_now() -> list[dict]:
    """立刻拉取所有 provider,返回最新结果。"""
    global _results
    _init_providers()
    fresh = [p.fetch() for p in _providers.values()]
    with _results_lock:
        _results = fresh
    return fresh


def get_all() -> list[dict]:
    """返回缓存的最新结果。首次调用会触发一次 refresh。"""
    if not _results:
        refresh_now()
    with _results_lock:
        return list(_results)


def _loop():
    """后台循环:每家按自己的间隔拉取。"""
    _init_providers()
    last_pull = {}
    while True:
        now = time.time()
        changed = False
        fresh = list(get_all())  # 拿当前快照
        idx = {r["key"]: i for i, r in enumerate(fresh)}
        for key, p in _providers.items():
            if now - last_pull.get(key, 0) >= p.refresh_interval:
                result = p.fetch()
                if key in idx:
                    fresh[idx[key]] = result
                else:
                    fresh.append(result)
                last_pull[key] = now
                changed = True
        if changed:
            with _results_lock:
                global _results
                _results = fresh
        time.sleep(10)  # 每 10 秒检查一次


def start():
    """启动后台拉取线程(仅一次)。"""
    global _started
    if _started:
        return
    _started = True
    refresh_now()  # 启动即拉一次
    t = threading.Thread(target=_loop, daemon=True)
    t.start()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_scheduler.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add scheduler.py tests/test_scheduler.py
git commit -m "feat: scheduler(各 provider 独立间隔拉取 + 缓存)"
```

---

### Task 8: app.py(Flask 路由)

**Files:**
- Create: `app.py`

**Interfaces:**
- Consumes: `config` (Task 1), `scheduler` (Task 7)
- Produces: Flask app,路由:
  - `GET /` → 看板页
  - `GET /api/usage` → 全部 provider 结果
  - `GET /api/config` → 当前主题 + 已配置哪些 key(不返回 key 值)
  - `POST /api/config/api-key` → 设置某 provider 的 key
  - `POST /api/config/theme` → 设置主题
  - `POST /api/refresh` → 手动刷新

- [ ] **Step 1: Write minimal implementation**

注:app.py 是薄路由层,主要委托给已测的 config/scheduler,不单列测试(沿用 B站 app.py 不测路由的惯例)。手动验证。

```python
# app.py
"""Flask 路由层。薄,业务委托给 scheduler / config。"""
from flask import Flask, request, jsonify, render_template

import config
import scheduler

app = Flask(__name__)


@app.route("/")
def index():
    return render_template("index.html", theme=config.get_theme())


@app.route("/api/usage")
def usage():
    return jsonify({"providers": scheduler.get_all()})


@app.route("/api/refresh", methods=["POST"])
def refresh():
    return jsonify({"providers": scheduler.refresh_now()})


@app.route("/api/config")
def get_config():
    """返回主题 + 各 provider 是否已配置 key(不泄露 key 值)。"""
    keys = config.get_api_keys()
    return jsonify({
        "theme": config.get_theme(),
        "configured": {k: bool(v) for k, v in keys.items()},
    })


@app.route("/api/config/api-key", methods=["POST"])
def set_api_key():
    body = request.get_json(force=True)
    provider = body.get("provider", "")
    key = body.get("key", "")
    if not provider:
        return jsonify({"ok": False, "error": "缺少 provider"}), 400
    r = config.set_api_key(provider, key)
    if r["ok"]:
        # 改了 key 立刻刷新该家
        scheduler.refresh_now()
    return jsonify(r)


@app.route("/api/config/theme", methods=["POST"])
def set_theme():
    body = request.get_json(force=True)
    theme = body.get("theme", "")
    return jsonify(config.set_theme(theme))


if __name__ == "__main__":
    scheduler.start()
    app.run(host="127.0.0.1", port=config.PORT, debug=False)
```

- [ ] **Step 2: 手动冒烟测试**

Run: `cd /Users/hushen/Desktop/token余额看板 && source .venv/bin/activate && python app.py`
另开终端:
```bash
curl -s localhost:5060/api/usage | python -m json.tool
curl -s localhost:5060/api/config | python -m json.tool
```
Expected: usage 返回4家(2家unconfigured+2家占位),config 返回 theme=auto。Ctrl+C 停止。

- [ ] **Step 3: Commit**

```bash
git add app.py
git commit -m "feat: Flask 路由(usage/refresh/config 端点)"
```

---

### Task 9: 看板前端(Alpine.js)

**Files:**
- Create: `templates/index.html`

**Interfaces:**
- Consumes: `/api/usage`, `/api/config`, `/api/refresh`, `/api/config/api-key`, `/api/config/theme`

- [ ] **Step 1: Write implementation**

完整单页:Alpine.js(CDN) + 内联 CSS/JS。4 张卡片网格,各按自己类型渲染。DeepSeek 余额型,智谱窗口型(进度条+倒计时),千问/MiniMax 占位(灰显"待配置")。含设置抽屉(填 key、切主题)。60 秒轮询 + 每秒倒计时 tick。

```html
<!-- templates/index.html -->
<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Token 余额看板</title>
<script defer src="https://unpkg.com/alpinejs@3.x.x/dist/cdn.min.js"></script>
<style>
  :root {
    --bg:#0f1117; --card:#1a1d27; --text:#e4e6eb; --muted:#8b8fa3;
    --accent:#4f8cff; --green:#34d399; --red:#f87171; --amber:#fbbf24;
    --border:#2a2e3a;
  }
  [data-theme="light"] {
    --bg:#f5f6fa; --card:#fff; --text:#1a1d27; --muted:#6b7280;
    --accent:#2563eb; --green:#10b981; --red:#ef4444; --amber:#f59e0b;
    --border:#e5e7eb;
  }
  *{box-sizing:border-box;margin:0;padding:0}
  body{background:var(--bg);color:var(--text);font-family:-apple-system,"PingFang SC",sans-serif;min-height:100vh;padding:24px}
  .header{display:flex;justify-content:space-between;align-items:center;margin-bottom:24px}
  h1{font-size:20px;font-weight:600}
  .toolbar{display:flex;gap:8px}
  .btn{background:var(--card);color:var(--text);border:1px solid var(--border);padding:8px 14px;border-radius:8px;cursor:pointer;font-size:13px}
  .btn:hover{border-color:var(--accent)}
  .grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(340px,1fr));gap:16px}
  .card{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:20px}
  .card-head{display:flex;justify-content:space-between;align-items:center;margin-bottom:14px}
  .card-name{font-size:15px;font-weight:600}
  .tier{font-size:12px;color:var(--muted)}
  .balance{font-size:32px;font-weight:700;margin:8px 0}
  .balance-sub{font-size:12px;color:var(--muted);margin-bottom:12px}
  .window{margin-bottom:14px}
  .window-label{display:flex;justify-content:space-between;font-size:13px;margin-bottom:4px}
  .bar{height:8px;background:var(--border);border-radius:4px;overflow:hidden}
  .bar-fill{height:100%;border-radius:4px;transition:width .4s}
  .bar-fill.ok{background:var(--green)}
  .bar-fill.warn{background:var(--amber)}
  .bar-fill.empty{background:var(--red)}
  .reset{font-size:11px;color:var(--muted);margin-top:3px}
  .status{display:flex;align-items:center;gap:6px;font-size:12px;color:var(--muted);margin-top:8px}
  .dot{width:8px;height:8px;border-radius:50%}
  .dot.ok{background:var(--green)} .dot.err{background:var(--red)} .dot.warn{background:var(--amber)}
  .placeholder{color:var(--muted);font-size:13px;text-align:center;padding:24px 0}
  .drawer{position:fixed;top:0;right:-360px;width:340px;height:100vh;background:var(--card);border-left:1px solid var(--border);padding:24px;transition:right .3s;overflow-y:auto;z-index:10}
  .drawer.open{right:0}
  .overlay{position:fixed;inset:0;background:rgba(0,0,0,.4);z-index:9;display:none}
  .overlay.show{display:block}
  .field{margin-bottom:16px}
  .field label{display:block;font-size:13px;margin-bottom:4px}
  .field input{width:100%;background:var(--bg);color:var(--text);border:1px solid var(--border);padding:8px 10px;border-radius:6px;font-size:13px}
  .seg{display:flex;gap:0;border:1px solid var(--border);border-radius:6px;overflow:hidden}
  .seg button{flex:1;background:var(--bg);color:var(--text);border:none;padding:8px;cursor:pointer;font-size:12px}
  .seg button.active{background:var(--accent);color:#fff}
</style>
</head>
<body data-theme="dark" x-data="board()" x-init="init()" :data-theme="theme">

<div class="header">
  <h1>Token 余额看板</h1>
  <div class="toolbar">
    <button class="btn" @click="refresh()" :disabled="loading">{{ loading ? '刷新中…' : '刷新' }}</button>
    <button class="btn" @click="settingsOpen=true">设置</button>
  </div>
</div>

<div class="grid">
  <template x-for="p in providers" :key="p.key">
    <div class="card">
      <div class="card-head">
        <span class="card-name" x-text="p.name"></span>
        <span class="tier" x-show="p.data && p.data.level" x-text="'Pro套餐'"></span>
      </div>

      <!-- 余额型:DeepSeek -->
      <template x-if="p.key==='deepseek' && p.status==='ok'">
        <div>
          <div class="balance" x-text="'¥ '+p.data.total_balance"></div>
          <div class="balance-sub">
            充值 ¥<span x-text="p.data.topped_up_balance"></span> · 赠金 ¥<span x-text="p.data.granted_balance"></span>
          </div>
          <div class="status">
            <span class="dot" :class="p.data.is_available ? 'ok' : 'err'"></span>
            <span x-text="p.data.is_available ? '可调用 · ' : '余额不足 · '"></span>
            <span x-text="ago(p.updated_at)"></span>
          </div>
        </div>
      </template>

      <!-- 窗口型:智谱 -->
      <template x-if="p.key==='zhipu' && p.status==='ok'">
        <div>
          <template x-for="(w,i) in p.data.windows" :key="i">
            <div class="window">
              <div class="window-label">
                <span x-text="w.label+'窗口'"></span>
                <span x-text="w.used+'/'+w.total+' 积分'"></span>
              </div>
              <div class="bar">
                <div class="bar-fill" :class="barClass(w)"
                     :style="'width:'+pct(w)+'%'"></div>
              </div>
              <div class="reset" x-text="pct(w)+'% · '+countdown(w.reset_at)+'后重置'"></div>
            </div>
          </template>
          <div class="status">
            <span class="dot ok"></span><span x-text="'刚刚更新 · '+ago(p.updated_at)"></span>
          </div>
        </div>
      </template>

      <!-- 未配置/占位 -->
      <template x-if="p.status!=='ok'">
        <div class="placeholder">
          <span x-text="p.status==='unconfigured' ? '未配置 API Key' : (p.data.note||'加载中')"></span><br>
          <span style="font-size:11px">点右上「设置」填入</span>
        </div>
      </template>
    </div>
  </template>
</div>

<!-- 设置抽屉 -->
<div class="overlay" :class="settingsOpen?'show':''" @click="settingsOpen=false"></div>
<div class="drawer" :class="settingsOpen?'open':''">
  <h2 style="margin-bottom:20px">设置</h2>
  <div class="field">
    <label>主题</label>
    <div class="seg">
      <template x-for="t in ['auto','light','dark']" :key="t">
        <button :class="theme===t?'active':''" @click="setTheme(t)" x-text="t"></button>
      </template>
    </div>
  </div>
  <div class="field">
    <label>DeepSeek API Key</label>
    <input type="password" placeholder="sk-..." x-model="keyInputs.deepseek">
    <button class="btn" style="margin-top:6px;width:100%" @click="saveKey('deepseek')">保存</button>
  </div>
  <div class="field">
    <label>智谱 API Key</label>
    <input type="password" placeholder="xxx.yyy" x-model="keyInputs.zhipu">
    <button class="btn" style="margin-top:6px;width:100%" @click="saveKey('zhipu')">保存</button>
  </div>
  <p style="font-size:12px;color:var(--muted);margin-top:16px">
    千问 / MiniMax 走 Edge 登录态,无需填 Key。打开 Edge 登录对应平台即可。
  </p>
</div>

<script>
function board(){
  return {
    providers:[], loading:false, settingsOpen:false, theme:'dark',
    keyInputs:{deepseek:'',zhipu:''},
    async init(){
      await this.loadConfig();
      await this.refresh();
      setInterval(()=>this.refresh(),60000);  // 60秒轮询
      setInterval(()=>{this.$forceUpdate?.()},1000);  // 倒计时每秒tick
    },
    async loadConfig(){
      const r=await fetch('/api/config');const d=await r.json();this.theme=d.theme;
    },
    async refresh(){
      this.loading=true;
      try{const r=await fetch('/api/refresh',{method:'POST'});const d=await r.json();this.providers=d.providers;}
      finally{this.loading=false;}
    },
    pct(w){return w.total?Math.round((w.used/w.total)*100):0},
    barClass(w){const p=this.pct(w);return p>=90?'empty':p>=70?'warn':'ok'},
    countdown(iso){if(!iso)return'未知';const ms=new Date(iso)-new Date();if(ms<=0)return'已重置';
      const h=Math.floor(ms/36e5),m=Math.floor(ms%36e5/6e4);
      return h>0?h+'小时'+m+'分':m+'分'},
    ago(iso){if(!iso)return'';const s=Math.floor((Date.now()-new Date(iso))/1000);
      return s<60?'刚刚':Math.floor(s/60)+'分钟前'},
    async saveKey(p){
      const r=await fetch('/api/config/api-key',{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({provider:p,key:this.keyInputs[p]})});
      const d=await r.json();if(d.ok){this.keyInputs[p]='';await this.refresh();}
    },
    async setTheme(t){this.theme=t;await fetch('/api/config/theme',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({theme:t})});},
  }
}
</script>
</body>
</html>
```

- [ ] **Step 2: 手动验证**

Run: `source .venv/bin/activate && python app.py`,浏览器打开 localhost:5060。
Expected: 看到 4 张卡片,DeepSeek/智谱显示"未配置",千问/MiniMax 显示占位。点设置,填入真实 key,卡片刷新出数据。

- [ ] **Step 3: Commit**

```bash
git add templates/index.html
git commit -m "feat: 看板前端(Alpine.js,4卡片网格,设置抽屉,倒计时)"
```

---

### Task 10: 启动脚本 + 首次运行验证

**Files:**
- Create: `启动.command`

- [ ] **Step 1: Write implementation**

```bash
#!/bin/bash
# token 余额看板启动脚本
# 双击此文件即可启动,浏览器打开 http://localhost:5060
cd "$(dirname "$0")"

if [ ! -d ".venv" ]; then
  echo "首次运行,正在创建虚拟环境并安装依赖..."
  python3 -m venv .venv
  source .venv/bin/activate
  pip install --upgrade pip -q 2>&1
  if ! pip install -r requirements.txt 2>&1; then
    echo ""
    echo "============================================"
    echo "依赖安装失败!请看上面的错误信息。"
    echo "============================================"
    read -n 1 -s -r -p "按任意键关闭窗口..."
    exit 1
  fi
else
  source .venv/bin/activate
fi

( sleep 2 && open "http://localhost:5060" ) &
python3 app.py
read -n 1 -s -r -p "按任意键关闭窗口..."
```

- [ ] **Step 2: 设可执行权限**

Run: `chmod +x /Users/hushen/Desktop/token余额看板/启动.command`

- [ ] **Step 3: 全量测试**

Run: `cd /Users/hushen/Desktop/token余额看板 && source .venv/bin/activate && python -m pytest -v`
Expected: 全部测试通过。

- [ ] **Step 4: 手动端到端验证**

双击 `启动.command`(或 `python app.py`),浏览器打开 localhost:5060:
1. 看到 4 张卡片,DeepSeek/智谱显示"未配置"
2. 点设置 → 填 DeepSeek key → 卡片显示真实余额
3. 填智谱 key → 卡片显示 5h/7d 额度 + 倒计时
4. 切换主题正常
5. 关页面重开 → 数据从缓存恢复

- [ ] **Step 5: Commit**

```bash
git add 启动.command
git commit -m "feat: 启动脚本(.command 双击启动)"
```

---

## 实施完成标准(对应 spec 第9节)

- [ ] 双击 `启动.command` → 浏览器打开 localhost:5060 → 看到 4 张卡片
- [ ] 填入 DeepSeek key → 卡片显示真实余额
- [ ] 填入智谱 key → 卡片显示真实 5h/7d 额度 + 倒计时
- [ ] 关闭页面重开 → 数据从缓存恢复,60s 内自动刷新
- [ ] 拔 key/改错 → 卡片标红"未配置",不崩
- [ ] `python -m pytest -v` 全绿
