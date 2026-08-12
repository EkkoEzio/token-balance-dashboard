# 启动即出结果 + 缓存驱动刷新 设计

## 背景

当前 `scheduler.start()` 第一行就调用 `refresh_now(notify=False)`,把所有 provider 串行 `fetch()` 一遍(DeepSeek → 智谱 → Tavly → 千问 → MiniMax)。期间内存 `_results` 是空,前端首次访问 `/api/usage` 要么挂起、要么等拉完才出数据 —— 用户体验是「每次启动都要等半天才出结果」。

项目里其实已有 `/api/refresh/<key>` 卡片单刷接口与前端 `refreshOne()` 逻辑,缺少的是:

1. 跨进程的缓存(进程退了就丢,启动必等)
2. 启动时不阻塞,先展示后刷新的策略
3. 启动并发拉取(现在是串行)

## 目标

- **启动即出**:打开服务 → 首屏立即展示上一次结果(从磁盘读),不再等。
- **后台保鲜**:数据超过 10 分钟(stale)时,前端分卡片触发刷新,期间旧数据仍可见。
- **冷启动不空**:首次(磁盘无缓存)能正确处理,不卡死、不空白。
- **并发提速**:启动并发拉取,降低「等最慢一家」的总耗时。

## 不在范围

- 不改 10 分钟定时兜底线程(它是数据保鲜的最后防线,保留)。
- 不改单家卡片刷新的 cooldown 策略(Tavly 30s / 其他 5s)。
- 不动各 provider 的 `fetch()` 实现。
- 不引入新 API 端点(复用现有 `/api/refresh/<key>`)。

## 关键决策(已与用户确认)

| 维度 | 选择 |
|---|---|
| 缓存持久化 | `data/cache.json`,原子写(tmp → rename) |
| 启动读取 | 同步读盘 + 立刻返回给 `/api/usage` |
| 启动并发 | `ThreadPoolExecutor(max_workers=5)`,后台线程拉 |
| 过期阈值 | 前端判断 `now - lastRefresh > 600`,触发分卡片刷新 |
| 冷启动 | 前端骨架,后端并发拉 |
| 触发节奏 | 页面 mount 时检一次 + 每 30s 轮询时复检 |

## 后端设计

### 1. 缓存落盘:`data/cache.json`

格式:

```json
{
  "results": [ /* 每个 provider 的完整 result 对象,与 /api/usage 返回一致 */ ],
  "last_refresh": 1700000000.0
}
```

**写入时机(全部走新的统一函数 `_persist()`):**

- `refresh_now()` 全量刷新成功后
- `refresh_one(key)` 单家刷新成功后
- 启动后台并发刷新,每家独立完成后(增量写盘,见下)

**写入实现:**

- 原子写:先写 `data/cache.json.tmp`,再 `os.replace()` 成 `data/cache.json`。防半写文件被读到。
- 用 `_results_lock` 保护内存,序列化时锁内取快照(`list(_results)` + `_last_refresh_ts`)、锁外写盘,避免长时间持锁。
- 写盘本身串行化:新增 `_persist_lock`(独立于 `_results_lock`),保证并发场景(如前端 `refreshAllStale` 同时刷 5 家)下多次 `_persist()` 不互相截断。文件量级 KB,串行写无性能问题。

**增量更新策略:**

`refresh_one(key)` 路径下,完整重写整份 `cache.json` 即可(数据量小,5 张卡的结果,KB 级别,无需做差量)。统一函数让全量/单家两个路径共用一份代码。

### 2. 启动流程改造(`scheduler.py::start()`)

旧:

```python
def start():
    if _started: return
    _started = True
    refresh_now(notify=False)          # ← 阻塞,串行
    t = threading.Thread(target=_loop, daemon=True); t.start()
```

新:

```python
def start():
    if _started: return
    _started = True
    _init_providers()
    _load_cache_from_disk()             # 同步:读盘 → 填充 _results / _last_refresh_ts
    # 启动后台线程:负责 10 分钟兜底轮询
    threading.Thread(target=_loop, daemon=True).start()
    # 启动后台线程:立刻并发拉一次(冷启动时是首次拉,热启动时刷新已有缓存)
    threading.Thread(target=_startup_refresh, daemon=True).start()
```

**关键约束:**

- `_load_cache_from_disk()` 必须在启动任何后台线程前完成 —— 这样 `/api/usage` 第一次被访问就能拿到磁盘数据,不会读空。
- `_startup_refresh()` 用 `ThreadPoolExecutor(max_workers=5)` 并发拉取。每家独立 try/except,失败不影响其他家。每家成功后单独更新内存 + `_persist()`。
- 启动刷新完成后,初始化 `_last_notified`(避免弹历史告警),逻辑搬自旧 `refresh_now(notify=False)` 的告警抑制分支。

### 3. `refresh_now()` 改并发

`refresh_now()` 也用 `ThreadPoolExecutor` 替代列表推导式,与 `_startup_refresh` 共用内部函数 `_fetch_all_concurrent()`,返回顺序按 `_ALL_CLASSES` 原顺序排(用 dict 收集,再按定义顺序提取),保证展示顺序稳定。

### 4. `/api/usage` 接口

返回结构不变,**不新增字段**(`providers` + `last_refresh`)。staleness 完全由前端基于 `last_refresh` 计算(用户决策:前端判断)。后端保持简单。

### 5. 文件读写位置

- 缓存路径:`config.DATA_DIR / "cache.json"`(沿用 `data/` 目录)
- `.gitignore` 已有 `data/*.json`,缓存文件不会被提交 ✅

## 前端设计

### 1. `init()` 流程改造

旧:

```js
async init(){
  await this.loadConfig();
  await this.forceRefresh();    // ← 等后端拉完才出数据
  await this.loadAlerts();
  setInterval(()=>this.poll(), 30000);
  setInterval(()=>{this.tick++}, 1000);
}
```

新:

```js
async init(){
  await this.loadConfig();
  await this.poll();            // 立即读缓存 → 瞬间渲染上一次结果
  this.checkStaleAndRefresh();  // mount 即检一次
  await this.loadAlerts();
  setInterval(()=>this.pollThenMaybeRefresh(), 30000);  // 轮询里夹带 staleness 检查
  setInterval(()=>{this.tick++}, 1000);
}
```

### 2. 新增 `checkStaleAndRefresh()`

```js
checkStaleAndRefresh(){
  if(!this.lastRefresh) return;            // 后端还没数据,不主动触发(等后端并发拉)
  const ageSec = Date.now()/1000 - this.lastRefresh;
  if(ageSec <= 600) return;                // 新鲜,不刷
  if(this.anyRefreshing()) return;         // 已在刷新中,不重复触发
  this.refreshAllStale();
}
```

### 3. 新增 `refreshAllStale()`

- 遍历 `this.providers`,对每个 key 并发调 `/api/refresh/<key>`(Promise.all,每家独立 try/catch)。
- 每家开始时设 `refreshing[key]=true`(复用现有 ⟳ 视觉)。
- 每家完成后单独 `.map()` 替换该卡片数据 + `refreshing[key]='ok'` + 1.2s 后清(复用 `refreshOne` 的成功反馈)。
- 若被 cooldown 拦截(返回 `cooldown_remaining`):跳过该家,显示 cooldownMsg(复用现有提示)。
- 全部完成后 `loadAlerts()`。

**与现有 `refreshOne(key)` 的关系:**

- `refreshOne(key)` 保留(用户手动点单卡按钮时用)。
- `refreshAllStale()` 内部不直接调 `refreshOne`(因为 `refreshOne` 会触发 `loadAlerts` N 次);它是独立的并发批量版本,末尾统一 `loadAlerts` 一次。

### 4. `poll()` 改造为 `pollThenMaybeRefresh()`

```js
async pollThenMaybeRefresh(){
  await this.poll();              // 原有读缓存逻辑
  this.checkStaleAndRefresh();    // 复检 staleness
}
```

原 `poll()` 函数体不变(只读缓存),只是被包了一层。

### 5. UI 反馈

复用现有视觉,不新增样式:

- 卡片在 `refreshing[key]===true` 时,头部 ↻ 图标变 ⟳ 旋转态(现有逻辑已实现)。
- 顶部 `.last-update` 区的 `.next-hint`「下次自动刷新 X 分 Y 秒后」,staleness 触发时改为「数据偏旧,后台刷新中…」。用一个计算属性 `staleHint()` 输出。

## 错误与边界

| 场景 | 处理 |
|---|---|
| `cache.json` 不存在 | `_load_cache_from_disk()` 静默跳过,`_results=[]`,`_last_refresh_ts=0` |
| `cache.json` 损坏(JSON 解析失败) | 静默跳过,记日志,等同上 |
| 启动并发拉取中某家失败 | 该家结果按 `fetch()` 内部 `error()` 走(已有错误分类),写盘照常,前端展示错误卡 |
| 磁盘写失败(权限/空间) | `_persist()` 内部 try/except,失败不影响内存与请求,下次重试 |
| 前端 stale 触发时遇到 cooldown | 跳过该家,显示 cooldownMsg;下次 30s 轮询再尝试 |
| 同一家并发被两次触发 | 前端用 `anyRefreshing()` 守卫;后端 cooldown 也兜底 |
| 用户在后台刷新期间点了全局「刷新」按钮 | 后端 cooldown 拦截(__all__ 全局 60s/10s);或正常走全量并发 |
| 启动窗口的双重拉取 | 场景:磁盘缓存 stale → 前端首次 `poll` 拿到旧 `last_refresh` → 触发 `refreshAllStale`;同时后端 `_startup_refresh` 也在并发拉。时间窗约 0.5~5 秒。**接受此重复**,依赖后端单家 cooldown + provider 内部限流兜底;`_startup_refresh` 完成后 `_last_refresh_ts` 更新为最新,后续 poll 不再触发。Tavly 最敏感,但其卡片内部有「显示上次成功数据」的容错,不会因一次重复请求而坏态。 |

## 测试要点

- 后端:
  - `cache.json` 不存在时启动不报错,`get_all()` 返回空(等并发拉完才有)
  - `cache.json` 正常时,启动后 `/api/usage` 立即返回磁盘数据(不等并发刷新)
  - `refresh_one` 后磁盘文件被更新
  - 并发拉取顺序按 `_ALL_CLASSES` 稳定
- 前端:
  - `init()` 后 `poll()` 立即渲染(不卡)
  - 磁盘数据 5 分钟前 → 不触发刷新
  - 磁盘数据 15 分钟前 → mount 即触发 `refreshAllStale`
  - 刷新中再次 stale → 不重复触发

## 影响面

文件改动预估:

- `scheduler.py`:新增 `_load_cache_from_disk` / `_persist` / `_fetch_all_concurrent` / `_startup_refresh`,改写 `start()` / `refresh_now` / `refresh_one`
- `templates/index.html`:改 `init()` / `poll()`,新增 `checkStaleAndRefresh` / `refreshAllStale` / `pollThenMaybeRefresh` / `staleHint` / `anyRefreshing`
- `app.py`:**无需改动**(接口层不变)
- `config.py`:可选新增 `CACHE_FILE` 常量(也可直接在 `scheduler.py` 里拼路径)
