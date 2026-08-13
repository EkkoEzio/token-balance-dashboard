# 更新日志 — Token 余额看板

记录各版本变更,遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/)。
首个正式版本 `v1.0` 的内容同时即「已实现需求说明」。

---

## [v1.0] — 2026-08-13

聚合 5 家 AI 服务的额度/余额,在一个本地看板统一展示,定时自动刷新,额度告警红条 + macOS 桌面通知。

### 一、接入服务（5 家）

| 服务 | key | 类型 | 鉴权方式 | 展示内容 |
|---|---|---|---|---|
| **DeepSeek** | `deepseek` | 余额型 | Bearer(`sk-…`) | ¥ 总余额、充值/赠金明细、可调用状态 |
| **智谱 Coding Plan** | `zhipu` | 窗口型(积分) | API Key(不加 Bearer,`xxx.yyy`) | 5 小时 / 7 天 双窗口、套餐等级、重置倒计时 |
| **Tavly** | `tavly` | 多账号次数型 | `tvly-…`(支持 2 账号) | 各账号 `已用/总次数`、月度重置(每月 1 号精确倒计时)、限流防护 |
| **千问 Token Plan** | `qianwen` | 窗口型(积分) | `sec_token` + `cookie`(阿里 SSO,从 F12 抓取) | 5 小时(限时取消=无限)/ 7 天 窗口、套餐档位、剩余天数 |
| **MiniMax Token Plan** | `minimax` | 百分比型 | Subscription Key(`sk-cp-…`) | 5 小时 / 7 天 窗口、boost 加成(总额最高 150%)、已用%·剩余% |

**卡片排版规范（v1.0 统一）**
- 余额型(DeepSeek):大号 `¥ 总余额` + 充值/赠金明细 + 可用性指示。
- 窗口型(智谱/千问/MiniMax):每个窗口右上 `已用 / 总额 单位`,进度条下方 `已用% · 剩余%`。
  - 单位按各家语义保留:智谱/千问=`积分`,MiniMax=`%`。
  - MiniMax boost 窗口显示真实剩余(如 `2% · 剩余 148%`,而非按总额折算的 99%)。
- Tavly:每个账号一行,`已用 / 总次数 次` + `已用% · 剩余%` + 月度重置倒计时。

### 二、数据获取与刷新

- **定时兜底**:每 **600 秒(10 分钟)** 并发拉取全部 provider(`ThreadPoolExecutor`,最多 5 并发),各自独立 try/except,一家失败不影响其他家。
- **启动流程**:同步读磁盘缓存 → 立即返回(首次 `/api/usage` 不等网络)→ 后台异步并发刷新(不阻塞服务启动)。
- **手动刷新**:
  - 全局「刷新」按钮:触发全量并发拉取,**所有卡片同步进入刷新态(`⟳`→`✓`→`↻`)**。
  - 单卡「↻」按钮:只刷新该 provider,不影响其他家(避免全量请求触发风控)。
- **刷新冷却（防风控/防狂点）**:
  | 场景 | 冷却 |
  |---|---|
  | Tavly 单卡刷新 | 30 秒 |
  | 全局刷新(含 Tavly) | 60 秒 |
  | 其他 provider 单卡刷新 | 5 秒 |
  | 全局刷新(不含 Tavly) | 10 秒 |
  冷却期内返回 429 + 剩余秒数,前端给出提示。
- **前端 staleness 复检**:每 30 秒轮询读缓存;若数据超过 600 秒且当前无刷新,自动触发分卡片刷新。

### 三、失败保护与缓存

- **失败保护**:某 provider 本次 fetch 失败(`error`/`expired`)且缓存里有上次成功数据 → **保留旧的成功数据**,避免偶发网络抖动把好数据冲掉(前端显示上次成功值)。
- **磁盘缓存**:`data/cache.json`,原子写入(`.tmp` → `os.replace`),存 `{results, last_refresh}`。启动同步读盘,保证首次访问即有数据。

### 四、额度告警

- **阈值**(`data/config.json` → `alerts`):
  - 余额阈值 `threshold_balance`:¥(默认 10)。DeepSeek 用。
  - 剩余百分比阈值 `threshold_pct`:%(默认 20)。窗口型/次数型用。
  - 告警级别:`critical` = 阈值的 1/4(余额为阈值的一半),其余为 `warning`。
- **展示**:页面顶部红条(汇总 + 可展开详情),critical 红 / warning 黄。
- **通知**:macOS 桌面通知(`osascript`),critical 🔴 / warning 🟡。
- **去重防抖动(关键)**:同一告警指纹(`key:window`)只在「新增」时弹一次。指纹是**粘性**的——一旦计入,仅当对应 provider 数据可用(`status==ok`)且告警确实消失时才清除。fetch 偶发失败(`status!=ok`)不会冲掉已通知指纹,避免「失败→恢复」抖动让同一告警被反复弹出(曾出现 DeepSeek 短时间弹一堆「余额仅剩 ¥0.00」)。

### 五、设置与配置

设置入口:右上「设置」抽屉。

- **主题**:`auto`(跟随系统)/ `light` / `dark`。
- **卡片排序**:设置页内 **HTML5 拖拽排序**(设置页顺序即卡片展示顺序),拖完即时保存。
- **provider 开关**:关闭后该家不刷新、不展示。
- **API Key 录入**:各家按鉴权方式分别录入(单 key / 双 key(Tavly)/ sec_token+cookie(千问))。
- **主密码**:复制任意 Key 前需验证主密码。密码以 **PBKDF2-SHA256(200,000 轮)** 哈希存储,不存明文(至少 4 位)。
- **告警配置**:开关 + 余额/百分比阈值。

**`data/config.json` 结构**
```jsonc
{
  "api_keys": { "deepseek": "sk-…", "zhipu": "…", "tavly": "tvly-…,tvly-…",
                "qianwen_sec_token": "…", "qianwen_cookie": "…", "minimax": "sk-cp-…" },
  "theme": "auto",
  "disabled": ["minimax"],          // 关闭的 provider key
  "order": ["deepseek","zhipu","tavly","qianwen","minimax"],
  "alerts": { "enabled": true, "threshold_balance": 10, "threshold_pct": 20 },
  "master_password": { "salt": "…", "hash": "…", "iters": 200000 }
}
```

### 六、前端看板

- 纯前端,**Alpine.js 3(CDN 引入,无构建步骤)**,单页 `templates/index.html`。
- **状态语义**:每张卡有 `ok` / `error` / `expired` / `unconfigured` 四态,各自配状态点颜色与人话文案。
- **错误分类**:`auth`(Key 失效)/ `expired`(过期)/ `network`(超时)/ `rate_limit`(限流)/ `blocked`(被墙,需代理)/ `unknown` → 映射人话文案 + 配色,便于定位。
- **顶部信息条**:上次更新时间 + 「下次自动刷新 Xm Ys 后」/ 数据偏旧提示。

### 七、技术架构

| 层 | 技术 |
|---|---|
| Web 服务 | Flask,监听 `127.0.0.1:5070`(注:不用 5060,Chrome/Edge 视其为受限端口) |
| 前端 | Alpine.js 3(CDN),无构建 |
| 调度 | `scheduler.py`:内存缓存 + 线程锁 + 后台兜底循环 + 启动异步刷新 |
| 存储 | `data/config.json`(配置)+ `data/cache.json`(结果缓存),纯本地文件 |
| 依赖 | `flask`、`requests`、`pytest`(`yt-dlp` 为模板残留,本应用未使用) |
| 测试 | `pytest`,75 项用例(provider 解析、调度逻辑、告警去重、缓存) |

### 八、启动方式

双击 **`启动.command`**:自动创建 `.venv`、安装依赖、清理占用 5070 的残留进程、2 秒后浏览器打开 `http://localhost:5070`,然后启动 `app.py`。
