# Luna 新窗口实施交接词：智联招聘独立采集与多平台队列

> 当前状态更新请优先阅读：
> [LUNA_ZHILIAN_COLLECTION_HANDOFF_2026-08-16.md](./LUNA_ZHILIAN_COLLECTION_HANDOFF_2026-08-16.md)。
> 本文保留为早期产品确认与实施规划基线，其中“尚未实现/待验证”部分不代表当前实时 Git 状态。

你将接手本地项目 BossHunter，在已经确认的产品方案上实现“智联招聘独立采集 + BOSS/智联统一选择与串行队列 + 目标数量进度”。不要重新讨论已确认的产品方向；若本交接与当前工作区证据冲突，以当前文件和实时 Git 状态为准，并先报告差异。

【工作目录】

`D:\简历\BossHunter`

【用户目标】

在当前 BossHunter 上新增智联招聘岗位采集能力。智联采集器必须与 BOSS 采集器独立，但采集后的岗位进入共享岗位池，并共用现有过滤、去重、AI 评分、导出和回收站。用户要在一个“岗位采集”窗口中勾选平台、设置各平台目标数量和执行顺序；双平台严格串行，默认 BOSS → 智联。

【开始前必须完整阅读】

1. `D:\简历\BossHunter\docs\LUNA_ZHILIAN_COLLECTION_IMPLEMENTATION_GUIDE.md`
2. `D:\简历\BossHunter\CLAUDE.md`
3. `D:\简历\BossHunter\README.md`
4. `D:\简历\BossHunter\src\bosshunter\scraper\jobs.py`
5. `D:\简历\BossHunter\src\bosshunter\browser\__init__.py`
6. `D:\简历\BossHunter\src\bosshunter\web\server.py`
7. `D:\简历\BossHunter\src\bosshunter\web\tasks.py`
8. `D:\简历\BossHunter\src\bosshunter\web\preflight.py`
9. `D:\简历\BossHunter\src\bosshunter\db.py`
10. `D:\简历\BossHunter\src\bosshunter\ai\scorer.py`
11. `D:\简历\BossHunter\src\bosshunter\web\frontend\src\hooks\useDashboard.ts`
12. `D:\简历\BossHunter\src\bosshunter\web\frontend\src\pages\DashboardPage.tsx`

【已验证的当前状态】

- [verified] 规划编写时分支为 `codex/bosshunter-upstream-main`。
- [verified] 规划编写时 HEAD 为 `62d1ccea878932f4e98ff67eaa00d5302c7cdff4`。
- [verified] 提交信息为 `feat: conservatively adapt selected PR #29 improvements (#44)`。
- [verified] 该 HEAD 与本地 `upstream/main` 一致。
- [verified] 规划编写时工作树干净。
- [verified] `origin` 是 `https://github.com/zhenian-666/BossHunter.git`。
- [verified] `upstream` 是 `https://github.com/powerycy/BossHunter.git`。
- [verified] 当前 Web 采集只显示扫描、新增、重复，不显示目标数量、百分比、过滤和失败细分。
- [verified] 当前 Web 采集没有传 `limit`，CLI 虽有 `--limit`，进度仍是未知总量。
- [verified] 当前 `_execute_collect()` 采集结束后无条件评分全部未评分岗位，不是只评分本轮新增。
- [verified] 当前 `collect` 预检始终要求 AI Key，即使用户只想采集。
- [verified] 当前 `jobs` 表没有来源平台和平台原始岗位 ID。
- [verified] 当前 BOSS 列表、详情解析、循环、过滤、入库和进度集中在 `scraper/jobs.py`。
- [verified] 当前任务互斥和停止由 `WorkbenchTaskRunner` 管理。
- [verified] 现有 `score_jobs()` 已支持 `scope="selected" + job_ids`，可用于只评分本轮新增岗位。
- [verified] 现有独立评分运行已经有 `scoring_runs` 持久化模式，可作为 collection run 的项目内风格参考。
- [unverified] 智联当前实时 DOM、城市编码和页面接口仍可能变化；本交接中的第三方选择器只能作为候选，不能宣称已在本机实时验证。

【与旧交接的差异】

早期交接记录使用 `codex/bosshunter-local-improvements` 和 `ec658d1`。当前工作区已经不是该状态，而且当前上游分支没有早期交接中列出的若干 Luna 文档。不要切回旧分支、重放旧提交或重复实现当前代码已经具备的功能。

【已完成】

- [verified] 用户已经完成 Deep Requirement Mode 的产品需求确认。
- [verified] 当前采集架构、数据库、任务系统、预检、评分选择能力和前端入口已经做过只读分析。
- [verified] 已完成 2026-08-15 GitHub 参考调研和许可证边界判断。
- [verified] 已生成详细实施规划和本交接词。
- [verified] 当前只完成规划文档；没有修改生产代码，没有运行真实平台、真实 AI 或真实数据库验证。

【已经确认的产品要求】

1. 使用统一“岗位采集”窗口。
2. 默认只勾选 BOSS；勾选智联后默认顺序为 BOSS → 智联。
3. 只勾选智联时只运行智联。
4. 同时勾选两个平台时严格串行；允许用户调整顺序。
5. BOSS 和智联分别保存关键词、城市、平台城市编码、最大页数、排序和目标新增数量。
6. 每个平台目标数量独立，也可以选择不限数量。
7. 只有通过过滤、去重并真正成功入库的新增岗位才计入目标数量。
8. 重复、标题/公司/JD 过滤、详情解析失败和保存失败必须分别统计，不占目标。
9. 每个平台显示 `已新增/目标`、百分比、当前关键词、城市和页码。
10. 未达到目标时显示真实原因，例如最大页数耗尽、没有更多结果、登录墙或选择器失效。
11. “采集后自动评分”是可选项，默认关闭。
12. 选择自动评分后，只评分本次两个平台真正新增的岗位 ID。
13. 评分完成后停止，不进入人工确认、招呼语、发送、简历投递或监测。
14. 智联首版只支持采集和共享评分，不实现智联自动投递。
15. 智联岗位不得误入现有 BOSS 发送、监测或自动跟进链路。

【必须采用的架构边界】

- 新建公共 collection 合同、平台注册表和串行 Orchestrator。
- BOSS 与智联分别实现 `BossCollector`、`ZhilianCollector` 或同等职责的独立适配器。
- 保留 `bosshunter.scraper.jobs.scrape_jobs` 兼容入口，旧调用默认仍是 BOSS。
- 智联复用 BossHunter 已有 Browser Runtime，不新增 Selenium、独立 Playwright 浏览器或 Cookie 存储。
- 平台适配器只负责搜索、列表/详情解析和转换为统一岗位候选。
- 共享层负责过滤、原子去重入库、进度和目标数量。
- 使用结构化进度对象，不解析日志字符串生成 UI。
- 在 `jobs` 表增加 `source_platform`、`source_job_id`、`source_keyword`，使用幂等迁移。
- 不改写旧 BOSS 主键；智联新岗位主键使用平台前缀避免碰撞。
- 使用 `(source_platform, source_job_id)` 唯一索引，并保留旧 BOSS ID 去重兼容。
- 新增 `collection_runs` 持久化选项、每平台进度、收集到的岗位 ID、错误和最终状态。
- 平台能力必须由后端验证：BOSS 支持现有完整链路，智联当前仅 `collect + score`。

【GitHub 参考与许可证】

- [JobRadar](https://github.com/jason-huanghao/jobradar)：参考适配器、注册表和 `ok/empty/error/blocked` 来源状态；它是 GPL-3.0，不复制源码。
- [Get Jobs](https://github.com/loks666/get_jobs)：参考平台模块独立和进度回调；其智联功能被作者标注当前有问题，且为自定义非商业许可证，不复制 Worker 或自动投递代码。
- [Hiring Radar](https://github.com/simonlin1212/Hiring-Radar)：参考统一岗位字段、来源追踪和不绕登录/验证码的边界。

只复用通用架构思想。不要复制第三方代码、Cookie 处理、反检测、验证码处理或自动投递逻辑。

【未完成 / 待验证】

- [pending] production code 尚未实现。
- [pending] 智联实时 DOM、城市编码、页面状态和登录要求尚未在本机实时核验。
- [pending] 新增测试、后端全量测试和前端构建尚未运行。
- [pending] 所有第三方候选选择器都必须通过本项目离线 fixture 或得到授权后的人工只读核验确认。

【建议实施顺序】

Phase 0：保护现场

1. 先运行：

```powershell
git status --short --branch
git log -1 --oneline
git remote -v
git diff --stat
git diff --check
```

2. 如果状态与上面不同或已有用户改动，保留改动并报告；不要 reset、clean、checkout、merge 或 rebase。

Phase 1：统一数据合同与数据库来源

1. 新增 collection models/base/registry。
2. jobs 表增加来源字段和唯一索引。
3. 实现真正插入才返回成功的原子入库方法。
4. 岗位 API、前端类型、来源筛选和导出增加平台字段。
5. 后端阻止智联岗位进入 BOSS 投递链路。

Phase 2：抽取 BOSS 适配器

1. 从 `scraper/jobs.py` 抽出 BOSS 选择器和解析逻辑。
2. 保留旧导入和函数签名兼容。
3. 接入统一进度和目标数量。
4. 先让现有 BOSS 测试全部恢复通过，再继续智联。

Phase 3：智联适配器

1. 建立独立智联城市快照，不复用 BOSS 编码。
2. 使用 Browser Runtime 后台打开搜索和详情页。
3. 用离线 HTML fixture 验证列表与详情解析。
4. 第三方代码中出现过的 `div.joblist-box__item`、`a.jobinfo__name`、`p.jobinfo__salary`、`div.companyinfo__name` 只能作为候选；集中管理并提供少量语义 fallback。
5. 详情 JD 解析失败不入库、不计目标。
6. 遇到登录墙、验证码、频率限制、账号异常或选择器整体失效，记录明确状态并停止当前平台，不绕过。

Phase 4：串行队列和自动评分

1. 按 `platform_order` 顺序普通循环，禁止平台并行。
2. 每个平台独立目标和进度。
3. 最大页数耗尽但未达目标时标记 `completed_with_shortage`。
4. 单平台阻断时记录错误；如果浏览器整体仍可用且用户没有停止，可继续下一个平台，最终标记 `completed_with_errors`。
5. 用户主动停止后不运行下一平台，也不自动评分。
6. 自动评分必须调用 `score_jobs(scope="selected", job_ids=本轮新增去重ID, force_rescore=False)`。

Phase 5：Web API 与前端

1. 扩展 Workbench 启动请求以接收 collection options。
2. 增加可接收同一请求体的结构化预检；保留旧 GET 兼容。
3. 纯采集不要求 AI；自动评分才要求简历和 AI。
4. `WorkbenchTask.metrics` 保留兼容，另加结构化 `progress`。
5. 新建 `CollectJobsDialog.tsx`。
6. 现有“单独采集”卡片改为先开窗口，不立即启动。
7. 显示每平台进度、队列顺序、短缺/阻断原因。
8. 岗位池显示来源；智联自动投递按钮禁用并有说明。

Phase 6：验证与报告

必须先用 mock、fixture 和临时 SQLite 完成验证。不得把真实平台当测试环境。

【预计重点文件】

- `src/bosshunter/collection/**`（新增）
- `src/bosshunter/scraper/jobs.py`
- `src/bosshunter/config.py`
- `src/bosshunter/db.py`
- `src/bosshunter/job_export.py`
- `src/bosshunter/collection_run_store.py`（建议新增）
- `src/bosshunter/data/zhilian_cities.json`（新增）
- `src/bosshunter/web/tasks.py`
- `src/bosshunter/web/preflight.py`
- `src/bosshunter/web/server.py`
- `src/bosshunter/web/config_schema.json`
- `src/bosshunter/web/frontend/src/components/dashboard/CollectJobsDialog.tsx`（新增）
- `src/bosshunter/web/frontend/src/hooks/useDashboard.ts`
- `src/bosshunter/web/frontend/src/pages/DashboardPage.tsx`
- `src/bosshunter/web/frontend/src/pages/ConfigPage.tsx`
- `config.example.yaml`
- `tests/test_collection_orchestrator.py`（建议新增）
- `tests/test_zhilian_collector.py`（建议新增）
- `tests/test_collection_runs.py`（建议新增）
- `tests/test_scraper_background.py`
- `tests/test_web_api_routes.py`
- `tests/test_web_preflight.py`

【必须测试的关键场景】

- 只选 BOSS、只选智联、双平台默认顺序、双平台自定义顺序。
- 两个平台没有同时运行。
- 目标 10 时，重复和过滤不计数，只有真实入库达到 10 才停止。
- 最大页数耗尽只新增 7 个时显示 `7/10` 和不足原因。
- 不限数量不显示虚假百分比。
- `auto_score=false` 完全不调用 AI。
- `auto_score=true` 只传本轮新增 ID，不包含历史待评分岗位。
- 用户停止后不启动下一平台和 AI。
- 智联列表/详情 fixture 正常、缺字段、选择器失效。
- 城市编码跨平台不串用。
- 老 BOSS 数据和新跨平台唯一索引兼容。
- 智联岗位无法通过伪造 API 请求进入 BOSS 发送器。
- 数据库迁移重复执行幂等。
- 应用重启不会自动恢复采集、自动开浏览器或自动调用 AI。

【必须遵守的要求】

- 不执行 `git reset`、`git clean`、`git checkout --`。
- 不切换分支，不 rebase，不 merge upstream，不强推。
- 未经用户明确授权不 commit、不 push、不修改 GitHub PR。
- 不覆盖、删除或回滚用户已有成果。
- 不读取、输出或提交 API Key、Cookie、OAuth、Token、登录状态或浏览器凭据。
- 不读取或修改真实 `config.yaml`、`data/bosshunter.db`、真实简历或用户导出文件。
- 不运行真实 AI、BOSS 采集、智联采集、招呼语发送、简历投递或监测。
- 不绕过验证码、账号异常、平台拦截或频率限制。
- 不从第三方仓库复制 GPL 或自定义许可证源码。
- 不把历史测试结果当成本次验证结果。

【验证命令】

后端全量测试：

```powershell
cd 'D:\简历\BossHunter'
& '.\.venv\Scripts\python.exe' -m unittest discover -s tests -p 'test_*.py'
```

如果 `.venv` 不存在，可使用项目当前可用 Python，但报告实际解释器和实际结果。

前端构建：

```powershell
cd 'D:\简历\BossHunter\src\bosshunter\web\frontend'
npm run build
```

Git 检查：

```powershell
cd 'D:\简历\BossHunter'
git diff --check
git diff --stat
git status --short --branch
```

【完成标准】

不要用“已完成”代替证据。最终必须给出“需求 → 文件/测试证据”表，并报告：

1. 实际修改文件。
2. BOSS 与智联适配器边界。
3. 数据库迁移和旧数据兼容方式。
4. 串行顺序和目标数量计数证据。
5. 自动评分只处理本轮新增 ID 的证据。
6. 智联投递保护证据。
7. 新增测试与后端全量测试实际数量和结果。
8. 前端构建实际结果。
9. `git diff --check` 结果。
10. 是否发起真实 AI 请求。
11. 是否访问真实 BOSS/智联。
12. 是否触碰真实数据库或真实简历。
13. 所有仍为 `未验证` 的实时 DOM、城市编码、接口和登录行为。
14. 完整 Git 状态。
15. 不要自动提交或推送，等待用户复核。

【下一步】

1. 先执行只读 Git 核对并完整阅读必读文件。
2. 若发现工作区、分支或代码事实冲突，先报告差异并保护已有修改。
3. 若没有证据冲突，按 Phase 1 开始增量实施，不再要求用户重复确认产品方向。

---

交接模式：full。隐私模式：local。来源：当前对话、当前工作区文件、Git 状态和 2026-08-15 GitHub 只读调研。未执行真实平台或真实 AI 验证。
