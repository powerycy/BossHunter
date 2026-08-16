# BossHunter 智联采集当前收尾交接

> 本文是 2026-08-16 的最新交接，优先级高于同目录中早期“实施规划”性质的文档。
> 如果本文与实时工作区冲突，以实时文件和 Git 状态为准，并先报告差异。

## 工作目录与 Git 边界

- 工作目录：`D:\简历\BossHunter`
- 当前分支：`codex/bosshunter-upstream-main`
- 当前基线：`62d1cce feat: conservatively adapt selected PR #29 improvements (#44)`
- 当前工作树有用户已有的未提交修改，不能 reset、clean、checkout、切换分支、rebase、merge、commit 或 push。
- 不读取、输出或提交 API Key、Cookie、登录态、真实简历、真实 `config.yaml` 或真实 `data/bosshunter.db`。

## 用户当前重点

用户要求：

1. 把当前项目状态写入本地记忆。
2. 修复智联“只打开岗位列表，没有真正打开岗位详情/JD 链接，导致一直采集不到”的问题。
3. 补齐智联与 BOSS 对齐的招呼语、发送和持续监测链路。
4. 全流程采集也打开与单独采集相同的全局设置弹窗。

## 当前已确认事实

- [verified] BOSS 与智联已经拆成独立适配器：`collection/platforms/boss.py` 与 `collection/platforms/zhilian.py`。
- [verified] 共享层由 `collection/orchestrator.py` 负责严格串行、过滤、去重、原子入库、目标数量和自动评分选择。
- [verified] 配置页现在提供 BOSS/智联全局平台配置；采集窗口读取全局默认值，也允许启动前调整。
- [verified] 搜索设置是平台全局配置；单独采集和全流程现在都从同一个采集设置弹窗提交并保存。
- [verified] BOSS 与智联都可进入全流程；发送和监测通过各自的平台动作适配器执行，不共享对方 DOM。
- [verified] 智联列表正常情况下会进入详情阶段：列表候选通过后调用共享 Browser Runtime 打开详情页、等待加载、提取 JD，再交给共享层入库。
- [verified] 本轮发现详情链路的薄弱点：候选以前依赖列表标题 `href`；如果当前卡片只提供 `data-positionid`/`data-job-id`，候选会在打开详情前被丢弃。
- [inferred] 这可以解释用户看到“只有岗位列表，没有 JD 页面”的现象；实际平台 DOM 仍可能有其他变化，需要授权环境复核。

## 本轮修复内容

### 智联列表到详情的链路

文件：`src/bosshunter/collection/platforms/zhilian.py`

- 列表标题链接、卡片内其他详情链接都优先作为详情 URL。
- 如果没有可用 `href`，使用卡片或链接上的平台原始岗位 ID。
- 自动构造：`https://www.zhaopin.com/jobdetail/{source_job_id}.htm`。
- 只有得到有效详情 URL、岗位原始 ID、标题和公司后，才进入详情页。
- 详情页仍必须提取完整 JD；没有 JD 仍计入“详情解析失败”，不入库、不占目标数量。
- 保留共享 Browser Runtime；没有新增 Selenium、独立 Playwright 浏览器或 Cookie 存储。

### 其他已完成能力

- `jobs` 表幂等增加来源平台、平台原始岗位 ID、来源关键词和城市编码字段。
- 智联岗位主键使用 `zhilian:{source_job_id}`，避免与旧 BOSS ID 碰撞。
- `(source_platform, source_job_id)` 唯一索引保留旧 BOSS ID 兼容。
- 自动评分只使用本轮真正新增岗位 ID，并且 `auto_score=false` 不调用 AI。
- 智联招呼语复用共享事实约束和人工确认，发送使用 `executor/zhilian_actions.py` 的独立页面动作与发送后 DOM 验证。
- 智联监测按岗位详情进入对应沟通页面，提取对话并复用共享回复/简历状态处理；无法验证页面状态时不写入成功历史。
- 全流程弹窗强制自动评分，采集设置启动成功后关闭窗口；预检失败或启动失败时保留窗口显示错误。

## 离线测试与验证边界

必须使用临时 SQLite、mock Browser Runtime 和 HTML fixture；不把真实平台当测试环境。

关键测试文件：

- `tests/test_zhilian_collector.py`
- `tests/test_collection_orchestrator.py`
- `tests/test_collection_runs.py`
- `tests/test_collection_preflight.py`
- `tests/test_platform_delivery_guard.py`
- `tests/fixtures/zhilian_current_search.html`
- `tests/fixtures/zhilian_current_detail.html`

建议验证命令：

```powershell
cd 'D:\简历\BossHunter'
& '.\.venv\Scripts\python.exe' -m unittest discover -s tests -p 'test_*.py'
cd 'D:\简历\BossHunter\src\bosshunter\web\frontend'
npm run build
cd 'D:\简历\BossHunter'
git diff --check
git status --short --branch
```

本轮当前验证结果：

- [verified] 定向采集、智联、编排、运行记录、预检、投递适配、动作适配器和全流程接口测试：73 个通过。
- [verified] 后端全量测试：313 个通过，结果为 OK。
- [verified] 前端 npm run build：通过，Vite 转换 1525 个模块。
- [verified] 修改 Python 文件通过 py_compile。
- [verified] git diff --check 通过。

## 仍需后续验证

- [unverified] 修改后的智联适配器尚未在真实智联环境再次运行；此前用户授权的旧实现现场诊断曾得到真实列表但 `0/3`。
- [unverified] 智联沟通按钮、输入框、发送确认和对话 DOM 仍未在真实登录态现场核验；当前实现是隔离的候选选择器适配器。
- [unverified] 智联后续 DOM、详情路由、城市编码和登录/验证码/频率限制行为可能变化。
- [unverified] 当前构造的 `jobdetail/{source_job_id}.htm` 需要在授权只读环境确认是否覆盖当前所有岗位类型。
- [unverified] 不得因为真实环境未验证，就把 fixture 测试结果描述成真实平台已稳定可用。

## 下一窗口第一步

1. 先执行只读 Git 检查并报告当前状态，不恢复旧分支。
2. 阅读本文、`LUNA_ZHILIAN_COLLECTION_IMPLEMENTATION_GUIDE.md`、`src/bosshunter/collection/platforms/zhilian.py` 和智联测试。
3. 先运行智联定向测试，再运行后端全量测试和前端构建。
4. 只有用户再次明确授权时，才在已登录浏览器中做一次人力 + 深圳 + 3 个岗位的只读现场核验；不运行真实 AI、不投递、不发送、不监测。
5. 现场重点记录：列表卡片原始 ID、最终详情 URL、JD 命中、沟通按钮/输入框/发送后消息选择器、监测对话识别和是否成功入库。

## 提交边界

本项目当前不要自动提交或推送。完成后只报告修改文件、测试结果、构建结果、Git 状态和未验证项，等待用户复核。
