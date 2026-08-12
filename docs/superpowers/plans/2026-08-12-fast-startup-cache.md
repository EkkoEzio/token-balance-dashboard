# 启动即出结果 + 缓存驱动刷新 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让看板启动瞬间展示上一次的磁盘缓存结果,后台并发拉取新数据;前端在数据超过 10 分钟时自动分卡片触发刷新,期间旧数据保持可见。

**Architecture:** scheduler 层新增 `data/cache.json` 持久化(`_load_cache_from_disk` / `_persist`)与并发拉取(`_fetch_all_concurrent` via `ThreadPoolExecutor`),启动改为「同步读盘 + 后台异步并发刷新」。前端 `init()` 改为先 `poll()` 渲染磁盘缓存,再按 staleness(>600s)分卡片并发调 `/api/refresh/<key>`。

**Tech Stack:** Python 3 (Flask, 标准库 `threading` / `concurrent.futures` / `json` / `os`),pytest,Alpine.js 3 (CDN),无前端测试框架(手动验证)。

## Global Constraints

- 缓存路径:`data/cache.json`(已被 `.gitignore` 的 `data/*.json` 覆盖,不提交)。
- 原子写:`data/cache.json.tmp` → `os.replace()`,防半写。
- 并发拉取 `ThreadPoolExecutor(max_workers=5)`。
- staleness 阈值 `600` 秒(前端常量,与后端 `REFRESH_INTERVAL` 一致)。
- 不引入新 API 端点;`/api/usage` 不新增字段;`app.py` 不改动。
- 各 provider 的 `fetch()` 实现不动;单家卡片 cooldown(Tavly 30s / 其他 5s)不动。
- 中文注释 + 中文 commit message,沿用项目风格。

**参考 spec:** `docs/superpowers/specs/2026-08-12-fast-startup-cache-design.md`

---

## File Structure

- `scheduler.py` (modify) — 新增 `_load_cache_from_disk` / `_persist` / `_fetch_all_concurrent` / `_startup_refresh`;改写 `start` / `refresh_now` / `refresh_one` / `_loop` / `get_all`。
- `tests/test_scheduler.py` (modify) — 新增缓存/并发相关测试;更新受影响的 `test_get_all_returns_active_providers`。
- `templates/index.html` (modify) — 改 `init`;新增 `checkStaleAndRefresh` / `refreshAllStale` / `_refreshOneSilent` / `pollThenMaybeRefresh` / `anyRefreshing` / `staleHint`;删除 `nextRefreshIn`;改顶部时间提示绑定。

---

## Task 1: 缓存读写函数(`_load_cache_from_disk` + `_persist`)

**Files:**
- Modify: `scheduler.py` (顶部 import 区 + 文件尾部新增函数)
- Test: `tests/test_scheduler.py` (追加)

**Interfaces:**
- Consumes: `config.DATA_DIR`(`Path` 对象,已存在)、`_results`(list)、`_last_refresh_ts`(float)、`_results_lock`(`threading.Lock`,已存在)
- Produces:
  - `_load_cache_from_disk() -> None`:读 `data/cache.json`,填充 `_results` / `_last_refresh_ts`;文件不存在或损坏时静默跳过
  - `_persist() -> None`:把当前 `_results` + `_last_refresh_ts` 原子写入 `data/cache.json`;失败静默
  - `_persist_lock`(`threading.Lock`,模块级,串行化写盘)

- [ ] **Step 1: 在 `scheduler.py` 顶部补 import**

在现有 `import threading` / `import time` 之后追加:

```python
import json
import os
from concurrent.futures import ThreadPoolExecutor
```

- [ ] **Step 2: 在 `scheduler.py` 模块级变量区(`_started = False` 那一带)追加 `_persist_lock`**

```python
_persist_lock = threading.Lock()  # 串行化磁盘写(与 _results_lock 独立)
```

- [ ] **Step 3: 写失败测试 — 缓存写入后能读回**

追加到 `tests/test_scheduler.py` 末尾:

```python
def test_persist_writes_cache_file(monkeypatch, tmp_path):
    """_persist 把 _results + _last_refresh_ts 写入 cache.json。"""
    import scheduler
    import config
    cache_file = tmp_path / "cache.json"
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    scheduler._results = [{"key": "deepseek", "name": "DeepSeek",
                           "status": "ok", "data": {"x": 1}, "updated_at": "t"}]
    scheduler._last_refresh_ts = 12345.6
    scheduler._persist()
    import json
    data = json.loads(cache_file.read_text(encoding="utf-8"))
    assert data["last_refresh"] == 12345.6
    assert data["results"][0]["key"] == "deepseek"


def test_load_cache_from_disk_fills_results(monkeypatch, tmp_path):
    """_load_cache_from_disk 把磁盘数据填到 _results / _last_refresh_ts。"""
    import scheduler
    import config
    import json
    cache_file = tmp_path / "cache.json"
    cache_file.write_text(json.dumps({
        "results": [{"key": "zhipu", "status": "ok", "data": {}, "updated_at": "t"}],
        "last_refresh": 999.0,
    }), encoding="utf-8")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    scheduler._results = []
    scheduler._last_refresh_ts = 0.0
    scheduler._load_cache_from_disk()
    assert len(scheduler._results) == 1
    assert scheduler._results[0]["key"] == "zhipu"
    assert scheduler._last_refresh_ts == 999.0


def test_load_cache_silent_on_missing_file(monkeypatch, tmp_path):
    """文件不存在时不报错,保持 _results 为空。"""
    import scheduler
    import config
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    scheduler._results = []
    scheduler._last_refresh_ts = 0.0
    scheduler._load_cache_from_disk()  # 不应抛异常
    assert scheduler._results == []
    assert scheduler._last_refresh_ts == 0.0


def test_load_cache_silent_on_corrupt_json(monkeypatch, tmp_path):
    """JSON 损坏时不报错,保持 _results 为空。"""
    import scheduler
    import config
    (tmp_path / "cache.json").write_text("{不是合法json", encoding="utf-8")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    scheduler._results = []
    scheduler._last_refresh_ts = 0.0
    scheduler._load_cache_from_disk()
    assert scheduler._results == []
```

- [ ] **Step 4: 运行测试,确认全部 FAIL(函数未定义)**

Run: `.venv/bin/pytest tests/test_scheduler.py::test_persist_writes_cache_file tests/test_scheduler.py::test_load_cache_from_disk_fills_results tests/test_scheduler.py::test_load_cache_silent_on_missing_file tests/test_scheduler.py::test_load_cache_silent_on_corrupt_json -v`
Expected: 4 个 FAIL,错误信息含 `AttributeError: module 'scheduler' has no attribute '_persist'` / `'_load_cache_from_disk'`

- [ ] **Step 5: 在 `scheduler.py` 文件末尾(`_send_notification` 之后、`_loop` 之前)实现两个函数**

```python
def _load_cache_from_disk():
    """启动时从 data/cache.json 读上次结果。文件不存在/损坏时静默跳过。
    在 start() 中、任何后台线程启动前同步调用 —— 保证首次 /api/usage 即可拿到磁盘数据。"""
    global _results, _last_refresh_ts
    path = config.DATA_DIR / "cache.json"
    if not path.exists():
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        results = data.get("results", [])
        last_refresh = float(data.get("last_refresh", 0.0))
        if isinstance(results, list):
            with _results_lock:
                _results = results
                _last_refresh_ts = last_refresh
    except Exception:
        # 损坏文件:静默丢弃,等后台刷新
        pass


def _persist():
    """把当前 _results + _last_refresh_ts 原子写入 data/cache.json。
    锁内取快照、锁外写盘;_persist_lock 串行化多次写,防互相截断。失败静默。"""
    path = config.DATA_DIR / "cache.json"
    with _results_lock:
        snapshot = {"results": list(_results), "last_refresh": _last_refresh_ts}
    with _persist_lock:
        tmp = path.with_suffix(".json.tmp")
        try:
            tmp.write_text(json.dumps(snapshot, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, path)
        except Exception:
            # 写盘失败不影响内存与请求,下次再试
            pass
```

- [ ] **Step 6: 运行测试,确认 4 个 PASS**

Run: `.venv/bin/pytest tests/test_scheduler.py::test_persist_writes_cache_file tests/test_scheduler.py::test_load_cache_from_disk_fills_results tests/test_scheduler.py::test_load_cache_silent_on_missing_file tests/test_scheduler.py::test_load_cache_silent_on_corrupt_json -v`
Expected: 4 PASSED

- [ ] **Step 7: 提交**

```bash
git add scheduler.py tests/test_scheduler.py
git commit -m "feat: scheduler 缓存落盘(_load_cache_from_disk + _persist)"
```

---

## Task 2: 并发拉取(`_fetch_all_concurrent`)

**Files:**
- Modify: `scheduler.py` (新增函数,位置紧邻 `_init_providers` 之后)
- Test: `tests/test_scheduler.py` (追加)

**Interfaces:**
- Consumes: `_providers`(dict,需已 `_init_providers()`)、`_ALL_CLASSES`(展示顺序)
- Produces:
  - `_fetch_all_concurrent() -> list[dict]`:并发 `p.fetch()` 所有 `_providers`,返回结果按 `_ALL_CLASSES` 顺序排列;每家独立 try/except,异常转成 `p.error(str(e), "unknown")` 不影响其他家

- [ ] **Step 1: 写失败测试 — 并发拉取按定义顺序返回**

追加到 `tests/test_scheduler.py` 末尾:

```python
def test_fetch_all_concurrent_preserves_order(monkeypatch):
    """_fetch_all_concurrent 按 _ALL_CLASSES 顺序返回,即使并发完成顺序乱。"""
    import scheduler
    scheduler._providers = {}
    monkeypatch.setattr(scheduler.config, "get_disabled", lambda: set())
    scheduler._init_providers()
    # 把每家 fetch 替换成「sleep 随机时长后返回自己的 key」,模拟并发完成顺序乱
    import time as _t, random as _r
    def make_fetch(name):
        def _f(self):
            _t.sleep(_r.uniform(0, 0.05))
            return {"key": self.key, "name": name, "status": "ok", "data": {}, "updated_at": "t"}
        return _f
    for cls in scheduler._ALL_CLASSES:
        monkeypatch.setattr(cls, "fetch", make_fetch(cls.__name__))
    results = scheduler._fetch_all_concurrent()
    keys = [r["key"] for r in results]
    expected = [cls.key for cls in scheduler._ALL_CLASSES]
    assert keys == expected


def test_fetch_all_concurrent_isolates_failure(monkeypatch):
    """某家 fetch 抛异常时,该家返回 error 结果,其他家不受影响。"""
    import scheduler
    scheduler._providers = {}
    monkeypatch.setattr(scheduler.config, "get_disabled", lambda: set())
    scheduler._init_providers()
    from providers.deepseek import DeepSeekProvider
    def boom(self):
        raise RuntimeError("炸了")
    monkeypatch.setattr(DeepSeekProvider, "fetch", boom)
    # 其他家用正常 fetch(会真打网络,所以替换成 stub)
    from providers.zhipu import ZhipuProvider
    monkeypatch.setattr(ZhipuProvider, "fetch",
                        lambda self: {"key": self.key, "status": "ok", "data": {}, "updated_at": "t"})
    results = scheduler._fetch_all_concurrent()
    ds = next(r for r in results if r["key"] == "deepseek")
    assert ds["status"] == "error"  # 异常被捕获,转成 error
    assert "炸了" in ds.get("error_detail", "")
```

- [ ] **Step 2: 运行测试,确认 FAIL**

Run: `.venv/bin/pytest tests/test_scheduler.py::test_fetch_all_concurrent_preserves_order tests/test_scheduler.py::test_fetch_all_concurrent_isolates_failure -v`
Expected: 2 个 FAIL,`AttributeError: module 'scheduler' has no attribute '_fetch_all_concurrent'`

- [ ] **Step 3: 在 `scheduler.py` 的 `_init_providers` 函数之后实现 `_fetch_all_concurrent`**

```python
def _fetch_all_concurrent() -> list:
    """并发拉取 _providers 中所有 provider,按 _ALL_CLASSES 顺序返回结果。
    假设 _providers 已通过 _init_providers() 初始化。每家独立 try/except,
    异常转成 error 结果,不影响其他家。"""
    # 按 _ALL_CLASSES 的定义顺序取(展示顺序稳定)
    ordered_keys = [cls.key for cls in _ALL_CLASSES if cls.key in _providers]
    providers_in_order = [_providers[k] for k in ordered_keys]

    def _safe_fetch(p):
        try:
            return p.fetch()
        except Exception as e:
            return p.error(str(e), "unknown")

    # ThreadPoolExecutor.map 保证返回顺序与输入顺序一致
    with ThreadPoolExecutor(max_workers=5) as ex:
        fetched = list(ex.map(_safe_fetch, providers_in_order))
    return fetched
```

- [ ] **Step 4: 运行测试,确认 2 个 PASS**

Run: `.venv/bin/pytest tests/test_scheduler.py::test_fetch_all_concurrent_preserves_order tests/test_scheduler.py::test_fetch_all_concurrent_isolates_failure -v`
Expected: 2 PASSED

- [ ] **Step 5: 提交**

```bash
git add scheduler.py tests/test_scheduler.py
git commit -m "feat: scheduler 并发拉取(_fetch_all_concurrent)"
```

---

## Task 3: `refresh_now` / `_loop` / `refresh_one` 接入并发 + 落盘

**Files:**
- Modify: `scheduler.py:47-94`(`refresh_now` / `refresh_one` 函数体)和 `_loop`
- Test: `tests/test_scheduler.py` (追加;现有 `test_refresh_now_*` 应继续通过)

**Interfaces:**
- Consumes: Task 1 的 `_persist()`、Task 2 的 `_fetch_all_concurrent()`
- Produces: `refresh_now` / `refresh_one` / `_loop` 在内存更新后额外调用 `_persist()`

- [ ] **Step 1: 写失败测试 — refresh_now 后磁盘被写入**

追加到 `tests/test_scheduler.py` 末尾:

```python
def test_refresh_now_persists_to_disk(monkeypatch, tmp_path):
    """refresh_now 成功后 cache.json 被写入。"""
    import scheduler
    import config
    import json
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    scheduler._providers = {}
    scheduler._results = []
    scheduler._last_refresh_ts = 0.0
    monkeypatch.setattr(scheduler.config, "get_disabled", lambda: set())
    # stub 所有 fetch,不打网络
    for cls in scheduler._ALL_CLASSES:
        monkeypatch.setattr(cls, "fetch",
                            lambda self: {"key": self.key, "name": self.name,
                                          "status": "ok", "data": {}, "updated_at": "t"})
    scheduler.refresh_now()
    cache_file = tmp_path / "cache.json"
    assert cache_file.exists()
    data = json.loads(cache_file.read_text(encoding="utf-8"))
    assert data["last_refresh"] > 0
    assert len(data["results"]) == 5


def test_refresh_one_persists_to_disk(monkeypatch, tmp_path):
    """refresh_one 单家刷新后,该家结果在磁盘上更新。"""
    import scheduler
    import config
    import json
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    # 预置一份磁盘缓存
    cache_file = tmp_path / "cache.json"
    cache_file.write_text(json.dumps({
        "results": [{"key": "deepseek", "status": "ok", "data": {"old": True}, "updated_at": "old"}],
        "last_refresh": 1.0,
    }), encoding="utf-8")
    scheduler._results = []
    scheduler._last_refresh_ts = 0.0
    scheduler._load_cache_from_disk()
    scheduler._providers = {}
    monkeypatch.setattr(scheduler.config, "get_disabled", lambda: set())
    scheduler._init_providers()
    from providers.deepseek import DeepSeekProvider
    monkeypatch.setattr(DeepSeekProvider, "fetch",
                        lambda self: {"key": "deepseek", "name": "DeepSeek",
                                      "status": "ok", "data": {"new": True}, "updated_at": "new"})
    scheduler.refresh_one("deepseek")
    data = json.loads(cache_file.read_text(encoding="utf-8"))
    ds = next(r for r in data["results"] if r["key"] == "deepseek")
    assert ds["data"] == {"new": True}
```

- [ ] **Step 2: 运行测试,确认 2 个 FAIL**

Run: `.venv/bin/pytest tests/test_scheduler.py::test_refresh_now_persists_to_disk tests/test_scheduler.py::test_refresh_one_persists_to_disk -v`
Expected: 2 个 FAIL(`cache_file` 不存在 → `FileNotFoundError` 或 `assert cache_file.exists()` 失败)

- [ ] **Step 3: 改写 `refresh_now`,把列表推导换成 `_fetch_all_concurrent`,内存更新后调 `_persist()`**

把 `scheduler.py` 中现有的 `refresh_now` 整体替换为:

```python
def refresh_now(notify: bool = True) -> list:
    """立刻并发拉取所有 provider,返回最新结果。记录刷新时间戳并落盘。
    notify=True 时触发告警判定/桌面通知;启动时传 False 避免弹历史告警。"""
    global _results, _last_refresh_ts
    _init_providers()
    fresh = _fetch_all_concurrent()
    with _results_lock:
        _results = fresh
        _last_refresh_ts = time.time()
    _persist()
    if notify:
        _check_and_notify(fresh)
    else:
        # 启动:把当前告警指纹计入已通知集合,这样后续只在"新增"时弹
        try:
            global _last_notified
            _last_notified = {f"{a['key']}:{a['window']}" for a in evaluate_alerts(fresh)}
        except Exception:
            pass
    return fresh
```

- [ ] **Step 4: 改写 `refresh_one`,内存更新后调 `_persist()`**

把 `scheduler.py` 中现有的 `refresh_one` 整体替换为:

```python
def refresh_one(key: str) -> dict | None:
    """只刷新单个 provider,更新该家在缓存中的结果并落盘,返回新结果。
    用于卡片单独刷新(不影响其他家,不触发全量请求,避免风控)。"""
    _init_providers()
    p = _providers.get(key)
    if not p:
        return None
    result = p.fetch()
    with _results_lock:
        # 替换缓存中该 key 的结果(若存在)
        for i, r in enumerate(_results):
            if r.get("key") == key:
                _results[i] = result
                break
        else:
            _results.append(result)
    _persist()
    # 单家刷新也走告警判定(通知)
    _check_and_notify(list(_results))
    return result
```

- [ ] **Step 5: 改写 `_loop`,用 `_fetch_all_concurrent` 并落盘**

把 `scheduler.py` 中现有的 `_loop` 整体替换为:

```python
def _loop():
    """后台循环:每 REFRESH_INTERVAL 秒并发刷新全部 provider,并落盘。"""
    while True:
        time.sleep(REFRESH_INTERVAL)
        _init_providers()  # 配置可能运行时变化(开关 provider)
        fresh = _fetch_all_concurrent()
        with _results_lock:
            global _results, _last_refresh_ts
            _results = fresh
            _last_refresh_ts = time.time()
        _persist()
        _check_and_notify(fresh)
```

- [ ] **Step 6: 运行新测试 + 现有 refresh 相关测试,确认全 PASS**

Run: `.venv/bin/pytest tests/test_scheduler.py -v -k "refresh or persist"`
Expected: 所有相关测试 PASSED(包括原有的 `test_refresh_now_records_timestamp` / `test_refresh_now_updates_results` / `test_disabled_provider_skipped`)

- [ ] **Step 7: 跑全量后端测试,确认没有回归**

Run: `.venv/bin/pytest tests/ -v`
Expected: 全部 PASSED

- [ ] **Step 8: 提交**

```bash
git add scheduler.py tests/test_scheduler.py
git commit -m "feat: refresh_now/refresh_one/_loop 接入并发拉取+落盘"
```

---

## Task 4: 启动流程改造(`start` + `_startup_refresh` + `get_all` 去 fallback)

**Files:**
- Modify: `scheduler.py:68-73`(`get_all`)和 `scheduler.py:234-242`(`start`)
- Test: `tests/test_scheduler.py` (追加 + 修一个现有测试)

**Interfaces:**
- Consumes: Task 1 的 `_load_cache_from_disk` / `_persist`、Task 2/3 的 `_fetch_all_concurrent`
- Produces:
  - `start() -> None`:同步读盘 → 启动 `_loop` 后台线程 → 启动 `_startup_refresh` 后台线程
  - `_startup_refresh() -> None`:后台并发拉一次,落盘,抑制历史告警(等价于旧 `refresh_now(notify=False)` 的副作用,但异步)
  - `get_all() -> list`:直接返回 `_results` 快照,不再在空时触发 `refresh_now`

- [ ] **Step 1: 写失败测试 — get_all 不再触发自动 refresh**

追加到 `tests/test_scheduler.py` 末尾:

```python
def test_get_all_does_not_trigger_refresh(monkeypatch):
    """get_all 在 _results 为空时直接返回 [],不再隐式触发 refresh_now。"""
    import scheduler
    scheduler._results = []
    scheduler._last_refresh_ts = 0.0
    called = {"n": 0}
    def _boom(*a, **kw):
        called["n"] += 1
        raise AssertionError("refresh_now 不应被 get_all 触发")
    monkeypatch.setattr(scheduler, "refresh_now", _boom)
    result = scheduler.get_all()
    assert result == []
    assert called["n"] == 0


def test_get_all_returns_disk_cache_after_load(monkeypatch, tmp_path):
    """_load_cache_from_disk 之后,get_all 直接返回磁盘数据(不等后台)。"""
    import scheduler
    import config
    import json
    (tmp_path / "cache.json").write_text(json.dumps({
        "results": [{"key": "x", "status": "ok", "data": {}, "updated_at": "t"}],
        "last_refresh": 5.0,
    }), encoding="utf-8")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    scheduler._results = []
    scheduler._last_refresh_ts = 0.0
    scheduler._load_cache_from_disk()
    # get_all 不该再触发 refresh,这里 _results 已有数据
    result = scheduler.get_all()
    assert len(result) == 1
    assert result[0]["key"] == "x"


def test_startup_refresh_runs_in_background(monkeypatch, tmp_path):
    """_startup_refresh 并发拉取并更新内存 + 落盘 + 抑制告警指纹。"""
    import scheduler
    import config
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    scheduler._providers = {}
    scheduler._results = []
    scheduler._last_refresh_ts = 0.0
    scheduler._last_notified = set()
    monkeypatch.setattr(scheduler.config, "get_disabled", lambda: set())
    for cls in scheduler._ALL_CLASSES:
        monkeypatch.setattr(cls, "fetch",
                            lambda self: {"key": self.key, "name": self.name,
                                          "status": "ok", "data": {}, "updated_at": "t"})
    scheduler._startup_refresh()
    assert len(scheduler._results) == 5
    assert scheduler._last_refresh_ts > 0
    assert (tmp_path / "cache.json").exists()
```

- [ ] **Step 2: 运行测试,确认新测试 FAIL + 一个旧测试 FAIL**

Run: `.venv/bin/pytest tests/test_scheduler.py -v -k "get_all or startup_refresh"`
Expected:
- `test_get_all_does_not_trigger_refresh` FAIL(当前 `get_all` 会调 `refresh_now` → 触发 AssertionError)
- `test_get_all_returns_disk_cache_after_load` PASS(已能正常返回)
- `test_startup_refresh_runs_in_background` FAIL(`_startup_refresh` 未定义)

另外 `test_get_all_returns_active_providers`(文件顶部那个旧测试)会 FAIL,因为它依赖 `get_all` 的隐式 refresh —— 这个在 Step 5 修。

- [ ] **Step 3: 改写 `get_all`,去掉隐式 refresh**

把 `scheduler.py` 中现有的 `get_all` 整体替换为:

```python
def get_all() -> list:
    """返回缓存的最新结果(快照)。
    不再在空时触发 refresh —— start() 已在后台异步拉取,首次访问可读到磁盘缓存。"""
    with _results_lock:
        return list(_results)
```

- [ ] **Step 4: 在 `scheduler.py` 的 `_loop` 之后、`start` 之前新增 `_startup_refresh`,并改写 `start`**

```python
def _startup_refresh():
    """启动后台并发刷新(不阻塞 start())。拉完更新缓存+落盘,并抑制历史告警通知。
    逻辑等价于旧 refresh_now(notify=False),但被放进后台线程异步执行。"""
    global _results, _last_refresh_ts, _last_notified
    fresh = _fetch_all_concurrent()
    with _results_lock:
        _results = fresh
        _last_refresh_ts = time.time()
    _persist()
    # 启动:把当前告警指纹计入已通知集合,后续只在"新增"时弹
    try:
        _last_notified = {f"{a['key']}:{a['window']}" for a in evaluate_alerts(fresh)}
    except Exception:
        pass


def start():
    """启动后台拉取线程(仅一次)。
    流程:同步读磁盘缓存 → 启动 10 分钟兜底循环 → 启动一次性的启动并发刷新。
    读盘同步完成,保证首次 /api/usage 即可拿到磁盘数据(不等网络)。"""
    global _started
    if _started:
        return
    _started = True
    _init_providers()
    _load_cache_from_disk()
    threading.Thread(target=_loop, daemon=True).start()
    threading.Thread(target=_startup_refresh, daemon=True).start()
```

- [ ] **Step 5: 修旧测试 `test_get_all_returns_active_providers`,显式触发 refresh**

把 `tests/test_scheduler.py` 开头的:

```python
def test_get_all_returns_active_providers(monkeypatch):
    """默认所有 provider 都返回。"""
    scheduler._providers = {}
    scheduler._results = []
    monkeypatch.setattr(scheduler.config, "get_disabled", lambda: set())
    scheduler._init_providers()
    results = scheduler.get_all()
    keys = {r["key"] for r in results}
    assert keys == {"deepseek", "zhipu", "tavly", "qianwen", "minimax"}
```

替换为(显式 refresh,并 stub 掉网络):

```python
def test_get_all_returns_active_providers(monkeypatch):
    """默认所有 provider 都返回(get_all 不再隐式 refresh,需显式 refresh_now)。"""
    scheduler._providers = {}
    scheduler._results = []
    monkeypatch.setattr(scheduler.config, "get_disabled", lambda: set())
    # stub 所有 fetch,不打网络
    for cls in scheduler._ALL_CLASSES:
        monkeypatch.setattr(cls, "fetch",
                            lambda self: {"key": self.key, "name": self.name,
                                          "status": "ok", "data": {}, "updated_at": "t"})
    scheduler.refresh_now()
    results = scheduler.get_all()
    keys = {r["key"] for r in results}
    assert keys == {"deepseek", "zhipu", "tavly", "qianwen", "minimax"}
```

- [ ] **Step 6: 运行全量测试,确认全 PASS**

Run: `.venv/bin/pytest tests/ -v`
Expected: 全部 PASSED(包括新 3 个 + 修复的 1 个旧测试)

- [ ] **Step 7: 提交**

```bash
git add scheduler.py tests/test_scheduler.py
git commit -m "feat: start 同步读盘+后台异步刷新;get_all 去隐式 refresh"
```

---

## Task 5: 前端 init 改造 + staleness 自动刷新

**Files:**
- Modify: `templates/index.html` (`init` / 顶部时间提示绑定 / 新增 6 个方法 / 删除 `nextRefreshIn`)
- Test: 手动浏览器验证(项目无前端测试框架)

**Interfaces:**
- Consumes: 现有 `poll()` / `refreshOne()` / `loadAlerts()` / `refreshing` / `cooldownMsg` / `lastRefresh` / `tick` / `providers`
- Produces:
  - `init()` 改造:先 `poll` 后 `checkStaleAndRefresh`,轮询改 `pollThenMaybeRefresh`
  - `anyRefreshing() -> bool`:是否有任意卡片正在刷新(`refreshing[k]===true`)
  - `checkStaleAndRefresh() -> void`:`lastRefresh>0 && age>600 && !anyRefreshing()` 时调 `refreshAllStale`
  - `refreshAllStale() -> Promise`:并发对每张可见卡片调 `_refreshOneSilent`,末尾统一 `loadAlerts`
  - `_refreshOneSilent(key) -> Promise`:`refreshOne` 的静默版(不重复 `loadAlerts`,刷新成功时同步更新 `lastRefresh`)
  - `pollThenMaybeRefresh() -> Promise`:`poll` + `checkStaleAndRefresh`
  - `staleHint() -> str`:顶部时间提示计算属性(新鲜时显示倒计时,stale 时显示「数据偏旧」/「后台刷新中」)
- [ ] **Step 1: 改顶部时间提示绑定,从 `nextRefreshIn` 切到 `staleHint`**

在 `templates/index.html` 第 110 行,把:

```html
<div class="last-update" x-show="lastRefresh"><span x-text="'上次更新 · ' + agoTs(lastRefresh)"></span><span class="next-hint" x-text="'  ·  下次自动刷新 ' + nextRefreshIn()"></span></div>
```

替换为:

```html
<div class="last-update" x-show="lastRefresh"><span x-text="'上次更新 · ' + agoTs(lastRefresh)"></span><span class="next-hint" x-text="'  ·  ' + staleHint()"></span></div>
```

- [ ] **Step 2: 改写 `init()`,把"首次拉取"换成"先读缓存 + staleness 检查"**

在 `<script>` 内 `board()` 里,把现有的:

```js
    async init(){
      await this.loadConfig();
      await this.forceRefresh();  // 首次加载拉一次
      await this.loadAlerts();
      setInterval(()=>this.poll(),30000);    // 30秒读缓存(不触发后端请求)
      setInterval(()=>{this.tick++},1000);   // 倒计时每秒tick
    },
```

替换为:

```js
    async init(){
      await this.loadConfig();
      await this.poll();                      // 立即读缓存 → 瞬间渲染磁盘数据(不等网络)
      this.checkStaleAndRefresh();            // mount 即检 staleness,旧则后台分卡片刷
      await this.loadAlerts();
      setInterval(()=>this.pollThenMaybeRefresh(),30000);  // 30秒轮询+staleness 复检
      setInterval(()=>{this.tick++},1000);   // 倒计时每秒tick
    },
```

- [ ] **Step 3: 新增 6 个方法,删除 `nextRefreshIn`**

在 `board()` 内,找到现有的 `nextRefreshIn(){...}` 那一行,把它整段删除,并在原位置(或 `agoTs` 之后)插入以下方法块:

```js
    anyRefreshing(){
      // 是否有任意卡片正在刷新(refreshing[k]===true,'ok' 不算)
      return Object.values(this.refreshing).some(v=>v===true);
    },
    checkStaleAndRefresh(){
      // staleness 检查:lastRefresh 存在 + 超过 600s + 当前没在刷 → 触发分卡片刷新
      if(!this.lastRefresh) return;            // 后端暂无数据,等后台并发拉
      const ageSec=Date.now()/1000-this.lastRefresh;
      if(ageSec<=600) return;                  // 新鲜
      if(this.anyRefreshing()) return;         // 已在刷新中,不重复触发
      this.refreshAllStale();
    },
    async refreshAllStale(){
      // 并发刷所有可见卡片;每家独立 try/catch,末尾统一 loadAlerts 一次
      const keys=this.providers.map(p=>p.key);
      await Promise.all(keys.map(k=>this._refreshOneSilent(k)));
      await this.loadAlerts();
    },
    async _refreshOneSilent(key){
      // refreshOne 的静默版:不重复 loadAlerts(refreshAllStale 末尾统一调)
      this.refreshing[key]=true;
      try{
        const r=await fetch('/api/refresh/'+key,{method:'POST'});
        const d=await r.json();
        if(d.ok&&d.result){
          this.providers=this.providers.map(p=>p.key===key?d.result:p);
          this.lastRefresh=Date.now()/1000;    // 后端已更新,前端同步时间戳
          this.refreshing[key]='ok';
          setTimeout(()=>{this.refreshing[key]=false},1200);
        }else if(d.cooldown_remaining){
          this.refreshing[key]=false;
          this.cooldownMsg=`${key} 刷新太频繁,${d.cooldown_remaining} 秒后再试`;
          setTimeout(()=>{this.cooldownMsg=''},3000);
        }else{
          this.refreshing[key]=false;
        }
      }catch(e){this.refreshing[key]=false;}
    },
    async pollThenMaybeRefresh(){
      // 30 秒轮询:读缓存后顺手复检 staleness
      await this.poll();
      this.checkStaleAndRefresh();
    },
    staleHint(){
      // 顶部时间提示:新鲜时显示「下次自动刷新 Xm Ys 后」,偏旧时显示状态文案
      this.tick;
      if(!this.lastRefresh) return '';
      const ageSec=Date.now()/1000-this.lastRefresh;
      if(ageSec<=600){
        const remain=600-Math.floor(ageSec);
        const m=Math.floor(remain/60),s=remain%60;
        return '下次自动刷新 '+m+'分'+s+'秒后';
      }
      if(this.anyRefreshing()) return '数据偏旧,后台刷新中…';
      return '数据偏旧';
    },
```

- [ ] **Step 4: 启动服务,浏览器手动验证 — 场景 A(热启动,缓存新鲜)**

```bash
.venv/bin/python app.py
```

打开浏览器 `http://127.0.0.1:5070/`,等数据加载完毕后关闭服务(`Ctrl+C`)。**立刻**重启服务并刷新页面:
- Expected:页面瞬间出现上一次的卡片数据(不等),顶部显示「上次更新 · 刚刚 · 下次自动刷新 X分Y秒后」,卡片不进入刷新态。

- [ ] **Step 5: 浏览器手动验证 — 场景 B(热启动,缓存偏旧)**

模拟「磁盘缓存 > 10 分钟」:停服务,手动编辑 `data/cache.json`,把 `last_refresh` 改成 `当前时间戳 - 900`(15 分钟前),保存。重启服务并刷新页面:
- Expected:页面瞬间出现旧数据 → 卡片头部 ↻ 变 ⟳ 旋转态(后台刷新)→ 几秒后数据更新,顶部时间变「刚刚」,⟳ 变 ✓ 再变回 ↻。期间旧数据一直可见。

- [ ] **Step 6: 浏览器手动验证 — 场景 C(冷启动,无缓存)**

```bash
rm data/cache.json
.venv/bin/python app.py
```

刷新页面:
- Expected:首屏卡片为空(网格区无卡片或处于加载占位),后端日志显示并发拉取;几秒后下次 30s 轮询(或手动点「刷新」按钮)出数据。**不卡死、不白屏报错**。

- [ ] **Step 7: 提交**

```bash
git add templates/index.html
git commit -m "feat: 前端启动先渲染缓存+staleness 自动分卡片刷新"
```

---

## Self-Review 记录

**1. Spec coverage:**
- 缓存落盘 `data/cache.json` → Task 1 ✅
- 原子写 + `_persist_lock` → Task 1 Step 5 ✅
- 启动同步读盘 → Task 4 Step 4 (`start`) ✅
- 启动后台并发刷新 → Task 4 Step 4 (`_startup_refresh`) ✅
- `refresh_now` / `refresh_one` / `_loop` 并发+落盘 → Task 3 ✅
- `get_all` 去 fallback → Task 4 Step 3 ✅
- `/api/usage` 不改 → 全局约束已声明,无对应 task(无需改动)✅
- 前端 `init` 先 `poll` → Task 5 Step 2 ✅
- `checkStaleAndRefresh` / `refreshAllStale` → Task 5 Step 3 ✅
- `pollThenMaybeRefresh` 30s 复检 → Task 5 Step 2/3 ✅
- `staleHint` 顶部提示 → Task 5 Step 1/3 ✅
- 错误边界(文件缺失/损坏/写失败/双重拉取)→ Task 1 测试 + 全局约束声明 ✅

**2. Placeholder scan:** 无 TBD/TODO/省略代码块,所有步骤含完整代码。

**3. Type consistency:**
- `_fetch_all_concurrent` 在 Task 2 定义,Task 3(`refresh_now`/`_loop`)/ Task 4(`_startup_refresh`)消费 —— 名称一致 ✅
- `_persist` / `_load_cache_from_disk` 在 Task 1 定义,Task 3/4 消费 —— 一致 ✅
- 前端 `_refreshOneSilent` / `refreshAllStale` / `checkStaleAndRefresh` / `anyRefreshing` / `pollThenMaybeRefresh` / `staleHint` —— Task 5 内部自洽,且复用现有 `refreshing` / `cooldownMsg` / `lastRefresh` / `providers` 字段 ✅
- `lastRefresh` 在前端是秒级时间戳(`Date.now()/1000`),与现有 `poll()` 写入逻辑一致 ✅

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-08-12-fast-startup-cache.md`.
