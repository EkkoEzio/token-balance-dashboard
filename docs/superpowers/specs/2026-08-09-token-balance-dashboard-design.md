# Token 余额看板 — 设计文档

日期:2026-08-09
状态:已确认,待实施

## 1. 目标

做一个本地看板,一眼同时看到多家 AI 平台的剩余额度(5小时窗口、7天窗口、剩余积分/次数、余额、套餐到期、重置倒计时),取代「浏览器开四个平台网页反复刷新」的操作。

### 三期路线(本次只做一期)

| 期 | 能力 | 性质 | 本次 |
|---|---|---|---|
| 一期 | 监控余量 | 只读,定时 | ✅ 本设计 |
| 二期 | 签到板 | 定时写入 + 状态展示 | 未来 |
| 三期 | 多账号切换 | 条件写入(A空→换B写进消费端配置) | 未来 |

三期都是「后台服务 + 调度器 + 登录态」同一内核,只是任务类型从读→写升级。架构一次定死,分期增量加任务。

## 2. 关键决策(已与用户确认)

1. **应用形态:本地服务(Flask)**,非浏览器扩展。理由:三期都需要后台持续运行 + 改本地文件,扩展沙箱做不了。
2. **登录态复用 Edge**:用 `yt-dlp cookiesfrombrowser=("edge",)` 读 Edge cookie(复用用户 B站项目验证过的模式),不手解密 SQLite。
3. **技术栈:Flask + Alpine.js**,沿用用户 B站项目(`~/Desktop/b站视频下载`)的 Python/Flask/config.py/data JSON/`.command`启动/TDD 风格。Alpine.js 做前端响应式(定时刷新、进度条、倒计时),无构建工具。
4. **展示原则:「拼页面」,不强统一**。每家 provider 吐自己的原生字段,前端每家卡片按自己的样子画。目标是「把四个看余额的页面拼一起」,信息明确即可。
5. **开发节奏:确定的两家先行**。DeepSeek(公开API)+ 智谱(API Key)先落地跑通;千问、MiniMax 等用户登录后现场抓接口再补。
6. **API Key 存 config.json + 看板设置页**(复用 B站 config.py 热更新模式)。千问/MiniMax 走 Edge cookie 不需要存 key。

## 3. 整体架构

```
token余额看板/
├─ app.py                 Flask 服务 (端口 5060)
├─ 启动.command           双击启动(复用 B站模式)
├─ config.py              配置 + API Key 存储(热更新)
├─ cookie_jar.py          读 Edge cookie(yt-dlp cookiesfrombrowser)
├─ scheduler.py           定时拉取(每家独立间隔) + 内存缓存
├─ providers/             每家一个适配器,各吐原生字段
│   ├─ base.py            最小契约
│   ├─ deepseek.py        公开 API /user/balance
│   ├─ zhipu.py           内部 API /monitor/usage/quota/limit
│   ├─ qianwen.py         控制台接口(待抓包,先占位)
│   └─ minimax.py         控制台接口(待抓包,先占位)
├─ data/                  JSON 存储(config.json、历史快照)
├─ templates/
│   └─ index.html         看板(Alpine.js)
└─ tests/                 pytest
```

### 核心数据流

```
调度器(每家独立间隔) → provider.fetch() → API Key 或 Edge cookie 取数
  → 归一化(每家自己的结构) → 存 data/ 快照 + 内存缓存
  → 前端每 60s 轮询 /api/usage → 各卡片渲染
```

### 最小 provider 契约(base.py)

不强统一字段,只约束最小接口:

```python
class Provider:
    name: str                 # 展示名,如 "智谱 Coding Plan"
    key: str                  # 标识,如 "zhipu"
    refresh_interval: int     # 秒,该家刷新间隔

    def fetch(self) -> dict:
        """返回该家原生数据。至少含:
           {name, key, status, updated_at, ...各家字段}
           status ∈ ok | unconfigured | expired | error
        """
```

各家返回结构自由:

- **DeepSeek**:`{balance, granted_balance, topped_up_balance, currency, is_available}`
- **智谱**:`{tier, expires_at, limits:[{window, used, total, unit, reset_at}, ...]}`
- **千问/MiniMax**:抓包后定

## 4. 数据获取层(四家各论)

| 平台 | 端点 | 鉴权 | 一期 | 字段(来源) |
|---|---|---|---|---|
| DeepSeek | `GET https://api.deepseek.com/user/balance` | `Authorization: Bearer <KEY>` | ✅ 确定 | `balance_infos[].{currency,total_balance,granted_balance,topped_up_balance}`, `is_available` |
| 智谱 | `GET https://open.bigmodel.cn/api/monitor/usage/quota/limit` | `Authorization: <KEY>`(**不加 Bearer**) | ✅ 大概率 | `data.limits[]`,用 `type==="TOKENS_LIMIT"` 筛 + `unit`(3=5h,6=周)区分窗口;字段 `percentage/usage/currentValue/remaining/nextResetTime`;`data.level` 是套餐档 |
| 千问 | 控制台内部接口(待抓) | Edge cookie | ⏳ 抓包后补 | 待定 |
| MiniMax | `/v1/token_plan/remains` 或 `/v1/api/openplatform/coding_plan/remains`(待定) | API Key 或 cookie(矛盾,待实测) | ⏳ 抓包后补 | 待定;已知有 `current_interval_*` / `current_weekly_*` 或新版 `_remaining_percent` |

### Edge cookie 读取(cookie_jar.py)

复用 B站 `bilibili_auth.get_cookiejar()` 模式:

```python
def get_cookiejar(domain: str):
    """读 Edge 里某域名的 cookie。用 ytdlp cookiesfrombrowser=("edge",)。
    缓存 5 分钟(Edge 读 cookie 较耗时)。失败返回 None。"""
```

### 风险与应对

- **智谱内部接口无 SLA**:抓包验证字段对不对得上;字段漂移时降级显示上次成功数据。
- **MiniMax 鉴权矛盾**:两个端点都试,Bearer 失败再试 cookie;记录哪个能用。
- **千问风控/滑块**:抓包时观察接口是否要额外头(CSRF/Referer);必要时带 BROWSER_HEADERS。

## 5. 前端看板

### 布局

卡片网格,每家一张,按各自原生展示。一期先 2 张(DeepSeek、智谱),抓包后补成 3-4 张。

### 各卡片示例(信息齐全,允许不一样)

```
┌─────────────────────────────┐  ┌─────────────────────────────┐
│ 💰 DeepSeek                  │  │ 🔷 智谱 Coding Plan          │
│                              │  │ Pro · 到期 2026-09-01        │
│ 可用余额                      │  │                              │
│ ¥ 85.30                      │  │ 5小时窗口                     │
│ ──────────────────           │  │ ████████░░ 1500/2000 积分    │
│ 充值 ¥100 · 赠金 ¥10          │  │ 75% · 2小时15分后重置        │
│                              │  │                              │
│ ✅ 可调用 · 刚刚更新           │  │ 7天窗口                       │
└─────────────────────────────┘  │ ████░░░░░░ 8000/10000 积分   │
                                  │ 80% · 4天12小时后重置        │
                                  │ ✅ 刚刚更新                    │
                                  └─────────────────────────────┘
```

### 关键技术点

- **自动刷新**:Alpine.js 每 60s 轮询 `/api/usage`,局部更新(整页不刷)。
- **倒计时**:前端用 `reset_at` 实时算「X小时Y分后重置」,每秒 tick。
- **主题**:复用 B站 light/dark/auto。
- **状态标识**:每卡底部 `✅可调用 / ⚠️额度低 / ❌已耗尽 / 🔴登录失效` + 更新时间。

## 6. 错误处理

| 场景 | 处理 |
|---|---|
| API Key 无效/空(DeepSeek/智谱) | 卡片「未配置」+ 设置页入口 |
| 额度耗尽 | 标红 + 0% 进度条 |
| 登录失效(千问/MiniMax cookie 过期) | 标红「登录失效」+ 服务自动重读 Edge cookie 尝试恢复 |
| 接口报错/字段漂移 | 显示上次成功数据 + 「数据可能过期」,不崩 |
| 网络错误 | 重试 3 次,失败显示离线 |

### 登录态自愈流程

```
服务定时取数 → cookie失效 → 服务下次循环重读 Edge cookie(缓存5分钟过期一次)
  → 用户在 Edge 里重新登录该平台 → 新cookie自动被读到 → 恢复
  → 全程无需在看板操作,卡片自动从红变绿
```

## 7. 一期范围(本次实施)

**包含**:
- 项目骨架(Flask + config.py + .command + Alpine.js)
- cookie_jar.py(读 Edge cookie)
- scheduler.py(定时 + 缓存)
- providers: deepseek.py、zhipu.py
- 前端看板(2张卡片 + 自动刷新 + 倒计时 + 主题)
- 设置页(填 API Key)
- 错误处理 + 登录态自愈
- tests(pytest,覆盖 parse 逻辑)

**占位**(架构留位,抓包后补):
- providers/qianwen.py、minimax.py(返回 unconfigured 状态)
- 前端预留卡片位

**不含**:二期签到、三期账号切换。

## 8. 依赖

```
flask
requests
yt-dlp        # 读 Edge cookie
pytest        # 测试
```

## 9. 验证标准

- 双击 `启动.command` → 浏览器打开 localhost:5060 → 看到 2 张卡片
- 填入 DeepSeek key → 卡片显示真实余额
- 填入智谱 key → 卡片显示真实 5h/7d 额度 + 倒计时
- 关闭页面重开 → 数据从缓存恢复,60s 内自动刷新
- 拔 key/改错 → 卡片标红「未配置」,不崩
