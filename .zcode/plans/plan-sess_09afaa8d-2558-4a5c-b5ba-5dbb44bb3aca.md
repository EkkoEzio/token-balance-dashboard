# 第二轮迭代计划:bug修复 + 核心需求

## 目标
修复智谱 bug,并实现你提的 5 个核心需求(3/4/5/6)。加密方案(第3点的进阶版)作为后续阶段标注,本轮先把"查看已存 key"做出来。

## 7 个改动,按文件分组

### A. 修复:智谱业务层错误码识别(第1点)
**问题**:智谱 key 失效时,接口返回 HTTP 200 但 body `{"code":401,"msg":"令牌已过期"}`,我们的代码只看 HTTP 状态码,误判成 ok,解析出空数据。
**修复**:`providers/zhipu.py` 的 `fetch()` 解析前检查 body `code` 字段,`!= 200` 时返回 `error(msg)`。这样失效 key 会显示"令牌已过期"而不是空白。
**同时**:`providers/base.py` 加一个统一的 `_check_http_ok(resp)` 工具,供各家复用(智谱/MiniMax 都有这种 body 错误码模式)。

### B. 新增:Tavly provider + 双 key(第4、6点)
**新增** `providers/tavly.py`:
- `GET https://api.tavily.com/usage`,Bearer 鉴权
- 解析 `account.{plan_usage,plan_limit,current_plan}` 和 `key.{usage,limit}`
- 剩余 = limit - usage;每月1号重置(固定文案)
- **特殊**:读 config 里 `tavly` 的 key 支持**两个**(逗号分隔或数组),fetch 时分别查两个 key,返回 `data: {accounts: [{plan,used,total,remaining}, ...]}`,前端同卡片左右并排展示两个额度。
- 测试:解析单 key / 双 key / key 失效(401)

### C. 供应商开关:停用就不刷新不展示(第5点)
**改动** `config.py`:
- 新增 `get_disabled() -> set[str]` / `set_disabled(provider, disabled) -> dict`,存 config.json 的 `disabled` 数组
**改动** `scheduler.py`:
- `_init_providers` 时跳过 disabled 的;`refresh_now`/`get_all` 也跳过 → 服务后台不刷新它
**改动** `app.py`:
- `/api/config` 返回 `disabled` 列表;新增 `POST /api/config/disabled`
**改动** 前端:设置页每个供应商一个开关;关掉的卡片从首页消失

### D. Key 查看(不加密版,第3点的"能用"版;加密是后续阶段)
**本轮实现**:设置页每个 key 输入框旁加「👁 查看」按钮,点 GET `/api/config/api-key?provider=xxx` 返回明文(因为本轮还没加密,key 本来就在 config.json 明文存着)。
**下一阶段**(你已确认靠后):主密码加密方案——存密文、查看时输密码实时解密、密码只在内存重启重输。本轮先把"能看到已存 key、不用重置"这个痛点的 80% 解决了,加密升级时接口不变,只换存储层。

### E. 前端设置页重构(index.html)
- 每个供应商一栏:标签 + 输入框 + 「保存」「👁 查看」+ 「启用」开关
- Tavly 那栏支持两个 key 输入框
- 查看按钮调接口拿明文回填到输入框(可复制)
- 智谱失效时卡片显示具体错误信息(配合 A 的修复)

## 测试策略(TDD)
- `test_zhipu.py`:加业务码 401 → error 的测试
- `test_tavly.py`(新):单/双 key 解析、401 失败、limit=null(无限)
- `test_config.py`:disabled 读写
- `test_scheduler.py`:disabled 的 provider 不出现在结果里

## 不在本轮范围(明确排除)
- ❌ 加密方案(主密码/keychain)→ 下一阶段单独做
- ❌ 千问/MiniMax 真实接口接入 → 需要你登录后配合抓包(可并行推进,但不阻塞本轮)
- ❌ 二期签到、三期账号切换

## 实施顺序
1. A(智谱 bug 修复)— 最先,独立
2. C(供应商开关 config + scheduler)— 给后续打基础
3. B(Tavly provider + 双key)
4. D(Key 查看接口 + 前端查看按钮)
5. E(前端设置页重构,把 B/C/D 串起来)
6. 全量测试 + 手动验证 + 提交

完成后:智谱失效能看清原因、Tavly 双号并排显示、不买的供应商能关掉、key 能查看复制。加密作为下一轮。