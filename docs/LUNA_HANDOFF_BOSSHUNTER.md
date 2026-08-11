# Luna 交接文案：BossHunter 本地增强改造

下面内容可以直接复制到新的 Luna 窗口中使用。

---

你将接手本地项目：

`D:\简历\BossHunter`

请先完整阅读：

1. `D:\简历\BossHunter\CLAUDE.md`
2. `D:\简历\BossHunter\docs\BOSSHUNTER_LOCAL_IMPROVEMENT_PLAN.md`
3. `D:\简历\BossHunter\README.md`

这是一次已经完成需求确认的编码任务，不需要重新讨论产品方向。请严格按实施文档执行；遇到会扩大范围、破坏数据、增加真实费用或改变投递行为的问题时才暂停询问。

## 一、任务目标

在不重新采集岗位的情况下增加独立 AI 评分；岗位池支持 XLSX/CSV 导出；城市扩展为全国且不依赖在线接口启动；增加 LuluCoding 服务商和省费用的 AI 诊断。

本项目只用于用户个人本地使用，不做公开部署或商业化。

## 二、必须实现

### 1. 独立 AI 评分

- 工作台新增“单独 AI 评分”入口，不调用岗位采集。
- 默认处理 20 个岗位，数量可自定义，也可选全部。
- 支持未评分/失败继续、只重试失败、岗位池已选重评、全部有效评分岗位重评。
- 普通继续评分必须跳过已有有效评分。
- 开始前显示符合条件岗位数、跳过数、首轮最多请求数和考虑重试后的最大请求数，并要求确认。
- 单岗位空响应、HTML、非法 JSON、字段缺失、上下文失败等记录后继续下一条。
- API Key 无效、余额不足、模型不存在、明确限流或连续 3 次网络/超时错误时安全暂停整个任务。
- 任务状态必须区分 completed、completed_with_errors、paused、stopped、failed。
- 评分失败记录结构化、可重试、前端展示简化原因和脱敏详情。
- 不能重评或改写已经 approved/sent/replied/resume_sent 等进入投递链路的岗位。

### 2. 岗位池和导出

- 修复 `/api/jobs` 和前端固定 100 条造成的数据截断。
- 使用分页循环或等价可靠方式读取全部岗位，不要简单改成无限大的 limit。
- 岗位池增加搜索、状态/城市筛选、复选框和当前筛选结果全选。
- 岗位池选择状态与确认投递选择状态必须分开。
- 导出 XLSX 和 UTF-8 BOM CSV。
- 支持导出全部、当前筛选和已选。
- 导出全部岗位字段、BOSS 城市编码、岗位 URL、最近评分失败原因。
- XLSX URL 是可点击超链接。
- CSV 防公式注入。
- 后端必须按 ID 查询数据库，不能信任前端传来的岗位字段。

### 3. 全国城市

- 移除 `config.py` 和 `ConfigPage.tsx` 各自写死 20 个城市的重复逻辑。
- 随项目内置一份 BOSS 全国城市静态快照，当前公开接口约 373 个唯一城市。
- 默认只读本地快照/本地缓存，启动和打开配置页时不访问 BOSS 城市接口。
- 搜索、多选并显示“城市名 · BOSS 编码”，热门城市优先。
- 只有用户点击“刷新城市列表”才请求：
  `https://www.zhipin.com/wapi/zpCommon/data/cityGroup.json`
- 在线数据必须严格验证；失败或返回 HTML 时继续使用旧本地数据，不覆盖缓存。
- 新岗位保存 `city_code`；旧岗位缺少编码时按城市名解析。
- 数据库只做向前兼容列迁移，不得清库。

### 4. LuluCoding 和 AI 诊断

- 新增服务商 `LuluCoding`，协议是 OpenAI Chat Completions。
- 站点根地址：`https://api.lulucoding.com`
- 规范 API Base：`https://api.lulucoding.com/v1`
- 用户填根地址或 `/v1` 都能规范化，不得出现重复 `/v1/v1`。
- 默认基础检测只检查 URL、Key、`/models`、JSON 类型、模型列表和当前模型是否存在，不调用 chat completions，不产生生成 Token。
- `/models` 返回官网 HTML 不能判定成功。
- 模型列表可选择，但只有点击“使用该模型”才更新模型字段，再走保存配置流程。
- 保留“高级实际测试”，必须由用户主动点击并确认费用提醒。
- 高级测试只发送一次极短流式请求，建议最多 32 个输出 Token，使用虚拟数据，不上传真实简历/JD。
- 展示请求发出、响应头、首包、完成和每阶段耗时。
- 检查精简 BossHunter 评分 JSON 能否解析。
- OpenAI 兼容 `auto` 默认不发送 Thinking；LuluCoding 默认 `off`。
- 用户明确 disabled 时，Thinking 参数不兼容、超时或明确参数异常可以最多回退一次到不发送 Thinking。
- 鉴权、余额、模型错误不能伪装成 Thinking 兼容问题。
- 空响应、HTML、非 JSON、缺 choices 等必须转成明确、脱敏的 `AIRequestError`。

## 三、当前代码中的关键事实

- 后端是 Bottle：`src/bosshunter/web/server.py`。
- 前端是 React + TypeScript：`src/bosshunter/web/frontend/src`。
- `server.py::_execute_rescore` 和 `web/tasks.py` 已有 rescore，但现有语义不等于新的独立评分范围。
- `DashboardPage.tsx` 的 `modes` 目前没有 rescore/score 卡片。
- `useDashboard.ts` 固定请求 `/api/jobs?limit=100`。
- `scoring.max_candidates=20` 已存在，但 `ai/scorer.py` 未使用。
- `db.py` 的 jobs 表已有 `url`，没有 `city_code`。
- `scraper/jobs.py` 已知道抓取时使用的 city_code，并保存完整 `detail_url`。
- `web/preflight.py::check_ai_connection` 当前只请求模型列表且没有充分检查 HTML/JSON。
- `ai/credentials.py::_openai_thinking_strategies` 当前 auto 会先发送 `thinking: disabled`，需要按实施文档调整。
- `call_openai_compatible_text` 当前直接调用 `response.json()`，需要补齐安全解析。
- 现有 `score_jobs` 遇全局错误会设置 pause_reason 后返回，任务运行器可能仍显示 completed。

## 四、建议新增模块

- `src/bosshunter/cities.py`
- `src/bosshunter/data/boss_cities.json`
- `src/bosshunter/ai/diagnostics.py`
- `src/bosshunter/scoring_selection.py`
- `src/bosshunter/job_export.py`

建议新增前端组件：

- `components/dashboard/ScoreJobsDialog.tsx`
- `components/dashboard/JobsToolbar.tsx`
- `components/config/CityMultiSelect.tsx`
- `components/config/AiDiagnosticsModal.tsx`

文件名可以按现有项目风格调整，但职责必须清晰，不要把大量新逻辑继续堆入 `server.py` 或单个 TSX 文件。

## 五、接口要求

至少提供：

- `GET /api/cities`
- `POST /api/cities/refresh`
- `GET /api/diagnostics/ai`：基础、无生成 Token
- `POST /api/diagnostics/ai/advanced`：必须 `confirmed=true`
- `POST /api/scoring/preview`
- 扩展 `POST /api/workbench/task` 支持 `mode=score` 和 options
- `POST /api/jobs/export`

保持旧 `GET /api/config/cities` 和 `/api/jobs` 的基本兼容性。

## 六、数据与安全

- 不打印或读取真实 Key；只能通过现有配置辅助函数获得调用凭据。
- 不在聊天、日志、测试快照、Markdown、错误响应中写 Key。
- 不把 `config.yaml`、`data/`、数据库、简历或导出文件加入 Git。
- 测试全部使用临时目录和临时 SQLite。
- 不对真实 `data/bosshunter.db` 跑迁移实验；迁移只在应用正常启动时幂等执行。
- 城市缓存写入 `data/cities.cache.json`，已被 `data/` 的忽略规则覆盖。
- 高级 AI 测试除非用户在界面主动确认，否则不要替用户调用真实接口。

## 七、不得修改

- 监控轮询逻辑。
- 投递和发送安全规则。
- 招呼语生成业务。
- HR 回复业务。
- 简历生成和发送流程。
- 每次投递前的人工确认原则。
- 非商业许可证和风险提示。

只允许为新评分状态的展示做必要的兼容更新，不能借机重构上述流程。

## 八、执行顺序

1. 检查 `git status --short`，确认工作树状态。
2. 创建 `codex/bosshunter-local-improvements` 分支；若分支已存在则安全切换，不要覆盖改动。
3. 先补城市静态快照、城市服务和测试。
4. 再补 LuluCoding、URL 规范化、AI 响应解析和诊断测试。
5. 再实现评分选择、预览、结构化结果、任务状态和评分测试。
6. 再实现岗位完整加载和导出。
7. 最后修改前端交互。
8. 运行全部后端测试和前端构建。
9. 检查差异中是否出现 Key、数据库、简历或导出产物。
10. 用临时数据库做手工冒烟；不要写用户真实数据库。

## 九、必须覆盖的测试

- 城市快照有效、缓存损坏回退、HTML 不覆盖、手动刷新原子写入。
- LuluCoding 根 URL 与 `/v1` 规范化。
- 基础诊断不调用 chat completions。
- HTML/空响应/非法 JSON/缺 choices 的错误分类。
- OpenAI auto 不带 Thinking；明确 disabled 最多回退一次。
- 默认评分 20、自定义数量、全部、已评分跳过、选中重评。
- 单岗位失败继续；鉴权/额度暂停；连续网络错误暂停。
- paused/completed_with_errors/stopped 状态正确。
- `/api/jobs` 完整分页加载。
- 三种导出范围、CSV BOM 和公式防护、XLSX hyperlink。
- API/错误响应不泄露 Key。

## 十、验证命令

后端：

```powershell
cd 'D:\简历\BossHunter'
& '.\.venv\Scripts\python.exe' -m unittest discover -s tests -p 'test_*.py'
```

如果虚拟环境不可用，再使用系统 Python，但必须说明实际使用的解释器。

前端：

```powershell
cd 'D:\简历\BossHunter\src\bosshunter\web\frontend'
npm run build
```

当前旧基线曾出现 225 个测试中 1 个 Windows 临时静态文件句柄清理错误；这只是历史参考。你必须报告本次真实测试数量、失败项和错误原因，不能照抄。

## 十一、验收重点

完成后逐项确认：

- 不重新抓岗位能否评分。
- 默认是否严格为 20，是否可以选择全部。
- 是否先显示请求规模并确认。
- 第一条评分坏掉时第二条是否继续。
- 全局鉴权错误是否显示“已暂停”而不是“已完成”。
- 岗位池是否超过 100 条仍能完整显示。
- 三种导出范围是否正确，XLSX 链接是否可点击。
- 断开 BOSS 城市接口时全国城市是否仍可选。
- 基础 AI 检测是否完全不调用生成接口。
- 高级测试是否必须手动二次确认。
- LuluCoding 根地址是否自动变成正确 `/v1`。
- Git 中是否没有 config、数据库、简历和导出文件。

## 十二、完成后的回复格式

请用中文简明但完整地报告：

1. 已完成的四类功能。
2. 关键文件和实现方式。
3. 后端测试结果。
4. 前端构建结果。
5. 是否运行真实 AI 请求。
6. 是否触碰用户现有数据库。
7. 尚存限制。
8. 当前分支和 `git status --short`。

不要仅回复“已完成”，也不要在没有运行验证时声称验证通过。

---

以上需求已经由用户确认，请开始实施。
