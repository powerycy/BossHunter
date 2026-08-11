# BossHunter 下一轮改造实施说明（供 Luna 直接执行）

> 文档状态：需求已经由用户确认，可以直接实施。
> 项目路径：`D:\简历\BossHunter`
> 当前分支：`codex/bosshunter-local-improvements`
> 重要边界：本文是当前任务的最高优先级实施说明。旧文档中的“不得修改招呼语生成业务”只适用于上一轮任务，在本轮已被明确的新需求覆盖。

## 1. 本轮目标

在保留上一轮本地增强成果的基础上，完成以下七组改造：

1. 修复招呼语部分失败后岗位消失、任务被整体判定失败的问题，并支持单条/批量重新生成。
2. 为岗位采集、AI 评分和招呼语生成增加真正可恢复的暂停、恢复和停止机制。
3. 把采集数量改为每个“关键词 × 城市”组合各自的新增唯一岗位目标。
4. 把单一简历升级为轻量“求职资料方案”，同时保证只上传一份简历即可直接开始。
5. 在左侧导航增加独立“AI 设置”页面，配置页只保留 AI 状态和跳转入口。
6. 让被偏好规则过滤的岗位仍可查看链接，并可在二次确认后人工推进。
7. 补齐岗位池链接、任务阶段、数量校验、文案和异常展示等相关问题。

本项目是用户个人本地工具。不要把本轮改造成云端、多用户、商业化或复杂 RAG 项目。

## 2. 开始前必须确认的工作树事实

执行前先运行：

```powershell
cd 'D:\简历\BossHunter'
git status --short --branch
```

当前工作树已经有大量上一轮未提交改动，包括但不限于：

- 全国城市、本地城市快照和手动刷新。
- 独立 AI 评分和评分范围预览。
- 岗位完整分页加载与 XLSX/CSV 导出。
- LuluCoding、AI 诊断和 AI 错误分类。
- 相关前端页面、构建产物和测试。

这些改动属于用户现有成果，必须原地增量开发：

- 不得 `git reset --hard`、`git checkout --`、清理未跟踪文件或覆盖现有改动。
- 不要重新创建或切换分支；继续使用当前分支。
- 不要删除旧构建产物来“整理”工作树。
- 修改前检查目标文件的当前内容，不能依据旧计划假定文件仍是旧版本。
- 不自动 commit、push 或创建 PR。

建议先完整阅读：

1. `CLAUDE.md`
2. 本文档
3. `docs/BOSSHUNTER_LOCAL_IMPROVEMENT_PLAN.md`（只作为上一轮背景）
4. `README.md`

## 3. 当前实现与已确认根因

### 3.1 招呼语失败后只显示少量成功岗位

当前链路存在两个直接原因：

1. `db.py::get_jobs_ready_to_send()` 只返回招呼语非空的 `ready/approved` 岗位；`server.py::api_workbench()` 又把它作为 `pending_greetings` 返回。没有招呼语的已确认岗位因此从工作台消失。
2. `server.py::_execute_deliver()` 在 `generated_count != selected_job_ids` 时直接抛出 `RuntimeError`。单岗位生成失败或 AI 安全暂停因此会把整个任务显示成 `failed`。

`ai/greeter.py::generate_greetings()` 已经具备部分基础能力：

- 每条成功招呼语会立即写入数据库。
- 会记录 `greeting_failed` history。
- 支持 `_workbench_stop_event` 和可取消 AI 请求。
- 遇到部分 AI 全局错误会安全停止后续生成。

但它仍缺少：

- 岗位级结构化失败字段。
- 明确的 `pending/generated/failed` 招呼语状态。
- 可恢复的暂停检查点。
- 面向单条或指定岗位的重新生成入口。
- 让任务运行器识别“暂停”和“部分失败”的结构化结果。

### 3.2 暂停与停止不是同一件事

`web/tasks.py` 当前只有内存中的 `stop_requested`，任务状态不会跨进程保存。`paused` 目前只是某些执行器返回的结果名称，没有暂停端点、恢复端点和持久化检查点。

`cancellation.py::run_cancellable()` 已采用守护线程包装阻塞 HTTPS 请求。Python 无法强杀进行中的请求，但可以在用户暂停/停止后立即放弃等待，并丢弃迟到结果。应复用并扩展这个机制，不要另写一套危险的线程强杀逻辑。

### 3.3 岗位采集停止不及时

`scraper/jobs.py::scrape_jobs()` 当前：

- 不接收暂停事件。
- 不检查工作台停止事件。
- 使用多个 `time.sleep()`，暂停/停止时无法立即返回。
- `limit` 是整批全局上限，不是每个关键词与城市组合的目标。

`server.py::_execute_collect()` 调用采集时也没有传递任务控制对象，并且“单独采集”实际上还会继续 AI 评分，前端文案与行为不一致。

### 3.4 当前个人资料只有一个简历路径

目前事实来源主要是 `config.yaml -> profile.resume_path`：

- 评分会读取整份简历，但放入评分提示词时通常截断到约 3000 字符。
- 招呼语只使用简历前约 1500 字符。
- `greeter.py` 虽然支持配置中的 `extra_highlights` 和 `portfolio_url`，配置页没有形成可管理的资料库。
- 当前上传接口只支持一份 `.md/.docx`，上传新文件会覆盖默认 `resume_path`。

因此不能简单把多份文件全文拼进提示词，否则会导致重复、冲突和上下文膨胀。

### 3.5 已有链接和导出基础

- `jobs.url` 已存在。
- `job_export.py` 已导出完整 URL，XLSX 已支持可点击链接。
- 岗位池表格缺少明显的“打开原岗位”入口。

本轮重点是补前端入口和回归，不要重写已经可用的导出模块。

## 4. 总体实施原则

1. **向前兼容**：只做幂等列/表迁移，不清库、不重建用户真实数据库。
2. **增量持久化**：每完成一个岗位或一个采集组合步骤就保存，不能等整批结束才落库。
3. **任务可恢复**：暂停依赖持久化检查点，而不是只保留一个内存 Event。
4. **成功项不回滚**：后续岗位失败时，前面已入库、已评分、已生成的内容继续保留。
5. **幂等**：恢复任务时重复扫描同一页或同一岗位不会重复入库、重复评分、重复生成或重复发送。
6. **秘密不入检查点**：任务配置快照不能保存 API Key、Cookie、Token 或登录状态。
7. **人工投递确认不变**：任何发送仍需用户确认；人工覆盖过滤条件不等于自动发送。
8. **第一版保持轻量**：不用向量库、不做 OCR、不做自动简历路由、不做复杂知识图谱。

## 5. 推荐数据结构与迁移

在 `db.py` 中增加新的幂等迁移函数。所有测试必须使用临时 SQLite；不要对真实 `data/bosshunter.db` 做实验性迁移。

### 5.1 jobs 表新增字段

建议增加：

| 字段 | 建议类型与默认值 | 用途 |
| --- | --- | --- |
| `greeting_status` | `TEXT DEFAULT 'not_started'` | `not_started/generating/generated/failed` |
| `greeting_failure_json` | `TEXT DEFAULT NULL` | 最近一次脱敏、结构化失败原因 |
| `greeting_attempts` | `INTEGER DEFAULT 0` | 已尝试次数 |
| `greeting_updated_at` | `TIMESTAMP DEFAULT NULL` | 最近生成或失败时间 |
| `filter_source` | `TEXT DEFAULT NULL` | `deal_breaker/prefilter/ai_score` 等岗位偏好过滤来源 |
| `filter_reason` | `TEXT DEFAULT NULL` | 独立于评分理由保存过滤原因 |
| `manual_override` | `INTEGER DEFAULT 0` | 是否人工推进 |
| `manual_override_at` | `TIMESTAMP DEFAULT NULL` | 人工推进时间 |

兼容规则：

- 旧记录如果 `greeting` 非空，读取时视为 `greeting_status=generated`。
- 旧记录如果有 `greeting_failed` history 且招呼语为空，可在展示层视为 `failed`；不要求启动时扫描重写整库。
- `score_reason` 继续保留，不能破坏现有评分和导出兼容性。
- 岗位主状态 `status` 继续描述投递主流程；不要再用它承载所有招呼语子状态。

### 5.2 求职资料方案表

保持三个轻量表即可：

#### `candidate_profiles`

- `id TEXT PRIMARY KEY`
- `name TEXT NOT NULL`
- `is_default INTEGER DEFAULT 0`
- `created_at`
- `updated_at`

#### `candidate_documents`

- `id TEXT PRIMARY KEY`
- `profile_id TEXT NOT NULL`
- `document_type TEXT NOT NULL`：`resume` 或 `supplement`
- `filename TEXT NOT NULL`
- `storage_path TEXT NOT NULL`
- `text_content TEXT`：解析后的本地文本；若考虑隐私，可只存文件路径并在使用时读取，但行为必须一致
- `is_primary INTEGER DEFAULT 0`
- `parse_status TEXT DEFAULT 'ready'`
- `created_at`
- `updated_at`

#### `candidate_facts`

- `id TEXT PRIMARY KEY`
- `profile_id TEXT NOT NULL`
- `source_document_id TEXT NULL`
- `category TEXT NOT NULL`
- `content TEXT NOT NULL`
- `normalized_hash TEXT NOT NULL`
- `status TEXT DEFAULT 'pending'`：`pending/confirmed/rejected/conflict`
- `conflict_group TEXT NULL`
- `created_at`
- `updated_at`

约束：同一 profile 下 `normalized_hash` 去重。冲突只做基础提示，不自动覆盖事实。

### 5.3 持久化任务表

第一版用一张 `workbench_tasks` 表即可，不引入复杂队列系统：

- `id TEXT PRIMARY KEY`
- `mode TEXT NOT NULL`
- `label TEXT NOT NULL`
- `status TEXT NOT NULL`
- `stage TEXT`
- `config_snapshot_json TEXT NOT NULL`
- `checkpoint_json TEXT`
- `progress_json TEXT`
- `logs_json TEXT`
- `profile_id TEXT NULL`
- `context_refs_json TEXT NULL`：记录使用的 document/fact ID，不复制 API Key
- `error TEXT NULL`
- `stop_reason TEXT NULL`
- `created_at`
- `updated_at`
- `finished_at`

启动恢复规则：

- 正常 `paused` 任务继续保持 `paused`。
- 应用异常退出前仍是 `running/pausing/stopping` 的任务，启动时转换为 `paused`，原因写为“应用已重启，请手动决定是否恢复”。
- 永远不自动访问 BOSS，也不自动调用 AI。
- 任务历史可以保留，但“结束旧任务”应清空可恢复 checkpoint 并标记 `stopped`，不能删除已完成岗位数据。

## 6. 求职资料方案 MVP

### 6.1 最小可用路径

用户只上传一份简历时：

1. 如果没有 profile，自动创建“默认求职资料”。
2. 把该简历保存为默认 profile 的 primary resume。
3. 同步更新旧配置 `profile.resume_path`，保证 CLI、评分、招呼语和定制简历仍可运行。
4. 不要求用户先拆事实或确认知识库，即可开始采集。

这条路径必须是默认体验，不能用复杂资料录入阻塞用户。

### 6.2 多文件与补充事实

支持上传：

- 简历：MD、TXT、DOCX、PDF。
- 补充资料：MD、TXT、DOCX、PDF。
- 手动事实和作品集链接。

处理规则：

- DOCX 复用 `resume_upload.py` 的安全解析逻辑。
- PDF 只做文本层提取，可引入 PyMuPDF；扫描版 PDF 明确提示“未识别到文本，本版不支持 OCR”。
- TXT/MD 做严格大小限制和安全解码。
- 所有文件仍存本地受控目录，不提交 Git。
- 上传一份新的简历不能默默删除旧简历；用户明确选择哪份是 primary。
- 删除操作默认只解除关联，不直接删除用户原始文件。

补充资料解析为待确认事实时，第一版采用本地确定性规则即可：按标题、段落和项目符号切分，去掉过短空行，生成 `pending` facts。不要为了事实拆分默认调用收费 AI。

### 6.3 重复、冲突和上下文控制

- 精确/规范化重复：用小写、空白和标点规范化后的 hash 去重。
- 潜在冲突：同 category 中高度相似但数字、日期或结论不同的条目标为 `conflict`，交给用户确认；不要自动判断哪条是真的。
- AI 只使用 primary resume 和 `confirmed` facts。
- 上下文生成集中到新模块，例如 `candidate_context.py`，不要让 scorer、greeter 各自拼接全部文件。
- `build_candidate_context(profile_id, purpose)` 按用途设置预算：评分保留当前约 3000 字符级别，招呼语保留约 1500 字符级别；优先放简历核心信息、与岗位关键词相关的确认事实和作品链接。
- 在任务启动时冻结 profile ID、primary document ID 和 fact ID 列表。恢复任务继续使用同一批引用；若资料已删除则暂停并提示，不自动换资料。

### 6.4 建议接口

- `GET /api/profiles`
- `POST /api/profiles`
- `PATCH /api/profiles/<profile_id>`
- `POST /api/profiles/<profile_id>/documents`
- `PATCH /api/profiles/<profile_id>/documents/<document_id>`：设置 primary 或更新类型
- `GET /api/profiles/<profile_id>/facts`
- `POST /api/profiles/<profile_id>/facts`
- `PATCH /api/profiles/<profile_id>/facts/<fact_id>`：确认、编辑、拒绝或解决冲突

保留旧 `/api/resume` 接口，内部映射默认 profile，避免一次性破坏前端和 CLI。

## 7. 每个关键词 × 城市的采集目标

### 7.1 配置语义

新增 `search.target_per_combo`，前端标签使用“每个关键词和城市组合采集数”。

示例：

- 关键词：`AI 产品经理`
- 城市：`北京、上海`
- 每组合目标：`10`
- 最大翻页：`3`

系统应分别尝试新增：

- 北京 × AI 产品经理：10 条
- 上海 × AI 产品经理：10 条

总目标最多 20 条。重复岗位不计数。

建议后端统一校验 `1-200`；前端 `min/max` 只是辅助，不能替代后端校验。`max_pages` 继续保持 `1-10` 安全上限。

### 7.2 scraper 修改要求

在 `scraper/jobs.py` 中：

- 每个组合使用独立的 `combo_new_count`。
- 达到当前组合目标后进入下一个组合，而不是结束整个批次。
- `insert_job()` 应返回是否真正插入；只有成功新增才计数。
- 去重至少同时考虑稳定 job ID 和规范化 URL。
- 恢复时允许重新打开当前页，但依靠去重保证不会重复入库。
- 每个组合结束记录 `target/inserted/pages_scanned/shortfall_reason`。
- 达到最大页数、页面为空、无法打开页面、城市无效、用户暂停/停止时都要有明确原因。
- 把固定 `time.sleep()` 改成可被控制事件唤醒的等待；暂停/停止时确保当前 tab 在 `finally` 中关闭。

保持 CLI 现有显式 `limit` 参数兼容。推荐把它继续作为可选全局安全上限，而 Web 配置的 `target_per_combo` 负责新的组合目标语义。

### 7.3 “单独采集”语义

- 工作台“单独采集”必须只采集，不自动 AI 评分。
- “运行全流程”显式按顺序调用采集和评分。
- 更新卡片文案，不能继续显示“单独采集会评分和发送”。
- 采集任务界面展示组合进度，例如 `北京 × AI 产品经理：7/10，第 2/3 页`。

## 8. 持久化暂停、恢复、停止与任务冲突

### 8.1 状态机

任务状态至少包含：

```text
running -> pausing -> paused -> running
running -> stopping -> stopped
paused  -> stopped
running -> completed | completed_with_errors | failed
```

状态含义：

- `paused`：有可恢复 checkpoint，用户可继续。
- `stopped`：本任务不会继续；删除可恢复 checkpoint，但已完成数据保留。
- `completed_with_errors`：任务跑完，但有岗位级失败。
- `failed`：不可恢复的程序错误或数据错误，不用于普通单岗位 AI 失败。

### 8.2 单一任务锁

同一时间只允许一个会修改任务数据的工作台任务运行。暂停任务也占用一个“可恢复任务位”。

当用户在存在暂停任务时启动新任务，后端返回结构化 `409`，前端必须给出三个明确选择：

1. 继续旧任务。
2. 结束旧任务并开始新任务。
3. 取消。

不能静默覆盖，也不能自动合并新旧配置。

### 8.3 配置快照

- 新任务启动时深拷贝非秘密配置并持久化。
- 去掉 `api_key`、`auth_token`、Cookie、登录信息以及运行时 Event/回调。
- 保留关键词、城市、每组合目标、翻页数、评分阈值、AI 服务商/模型、profile 和事实引用。
- 恢复任务使用原快照；用户如果想使用新配置，必须结束旧任务后新建。
- 恢复时凭据从当前安全配置或环境变量重新取得，不把凭据写进快照。

### 8.4 控制事件与迟到响应

为任务区分 `pause_requested` 和 `stop_requested`。可扩展 `cancellation.py`：

- AI 请求等待期间检测两个事件。
- 暂停触发专用 `OperationPaused`，停止继续使用 `OperationCancelled`。
- 对无法物理取消的 HTTPS 请求，任务线程立即退出等待；守护线程的迟到结果必须丢弃，不能写数据库。
- 在写入 AI 结果前再次核对 task ID、状态和 item checkpoint。

所有循环在安全边界调用控制检查：

- 采集：组合前、页前、打开详情前后、等待期间、入库前。
- 评分：每个岗位前、AI 返回后、写分数前。
- 招呼语：每个岗位前、每次生成/复核前后、写招呼语前。
- 发送：每条发送前后；已发送状态是幂等依据。

### 8.5 建议任务接口

- `POST /api/workbench/task/<task_id>/pause`
- `POST /api/workbench/task/<task_id>/resume`
- `POST /api/workbench/task/<task_id>/stop`
- `POST /api/workbench/task/<task_id>/end`（也可让 paused 状态下的 stop 承担此语义）
- `GET /api/workbench/recoverable`

任务 snapshot 增加：

- `stage`
- `progress`
- `can_pause`
- `can_resume`
- `recoverable`
- `checkpoint_summary`

前端不要再根据日志字符串猜阶段。`DashboardPage.tsx::currentTaskStage()` 的“等待后端返回阶段”应改成后端 `stage`，只有旧任务无 stage 时才显示“任务已启动，等待首个进度”。

### 8.6 应用重启

启动时只做状态恢复提示：

- 不自动打开 BOSS。
- 不自动调用 AI。
- 不自动发送。
- 用户点击恢复后重新做必要 preflight，再从原 checkpoint 继续。

## 9. 招呼语失败、暂停和重新生成

### 9.1 返回结构化结果

把 `generate_greetings()` 从只返回整数升级为结构化结果，例如：

```python
GreetingResult(
    selected=7,
    generated=2,
    failed=5,
    remaining=0,
    outcome="completed_with_errors",
    pause_reason="",
)
```

为保持 CLI/旧测试兼容，可以实现 `__int__` 或在旧调用处取 `.generated`，但不要再依赖“生成数量必须等于选择数量”判断成功。

### 9.2 岗位级失败

每个失败岗位：

- 保持岗位可见。
- 保持主流程状态为 `approved` 或当前合理状态。
- 写 `greeting_status=failed`。
- 写脱敏 `greeting_failure_json`，至少包含 `schema/kind/user_message/attempts/at`。
- 写 history `greeting_failed`。
- 失败后继续下一个岗位，除非是鉴权、额度、模型不可用、明确限流或连续网络错误等全局暂停条件。

成功岗位：

- 立即保存 `greeting`。
- 清空旧失败字段。
- 设置 `greeting_status=generated`。
- 只在写入成功后计数。

### 9.3 工作台返回集合

不要再用一个含糊的 `pending_greetings` 同时表示生成和发送。建议新增：

- `greeting_generation_items`：已确认但未成功生成，包括 pending/failed。
- `ready_to_send`：已有招呼语、等待发送或重新发送。
- `send_errors`：发送失败。

旧字段可以暂时保留以兼容旧前端，但新前端使用明确字段。

### 9.4 重新生成入口

新增独立 `greet` 工作台 mode，或提供等价的专用 API：

- 指定一个或多个 job ID。
- 后端只从数据库读取岗位，不信任前端传来的 JD/公司/招呼语。
- 默认只处理招呼语为空或失败岗位。
- 如允许重写已有招呼语，必须单独确认并明确显示会覆盖。
- “重新生成招呼语”本身不能自动发送；成功后进入 `ready_to_send`，再次走人工确认发送。

建议接口：

- `POST /api/greetings/preview`
- `POST /api/workbench/task`，`mode=greet`，options 包含 `job_ids` 和 `force_regenerate=false`

### 9.5 暂停语义

用户点击暂停生成时：

- 状态先显示 `pausing`。
- 不再开始下一个岗位或下一次复核。
- 当前 AI 请求通过可取消包装立即放弃等待，迟到结果不写库。
- 已生成成功的内容保留。
- checkpoint 保存剩余 job ID、当前 profile/context 引用和原配置快照。
- 恢复时只处理剩余或失败项，不重复生成成功项。

`server.py::_execute_deliver()` 必须删除当前的数量不一致 `RuntimeError`。改为根据 `GreetingResult`：

- `paused`：停止进入发送阶段，任务保持可恢复。
- `completed_with_errors`：只把本次已明确确认且生成成功的岗位送入发送阶段；失败岗位留在生成失败区。
- `completed`：正常进入发送。
- 用户从“重新生成”入口生成成功的岗位不自动发送，仍需新的发送确认。

如果暂停发生在发送阶段，已发送记录保留，未发送岗位保持 `ready_to_send`。恢复发送前重新展示数量并要求用户确认，避免重复消息。

## 10. 过滤岗位人工推进

### 10.1 可覆盖范围

允许人工覆盖的是岗位适配/偏好规则，例如：

- 排除关键词。
- 实习/管培偏好。
- 薪资偏好。
- 匿名公司提示。
- AI 低分过滤。

不能覆盖：

- 验证码。
- 账号异常或封禁提示。
- BOSS 平台拦截。
- 发送频率限制和其他账号安全停机条件。

### 10.2 保留链接与过滤原因

当前 scraper 在岗位标题命中 deal breaker 时直接 `continue`，导致此类岗位不入库。应调整为：

- 至少保存列表卡片可获得的公司、标题、城市和完整原岗位 URL。
- 标记 `status=filtered`、`filter_source=deal_breaker`、`filter_reason=...`。
- 不计入“新增可用岗位”目标；是否计入总入库数要在进度中分开展示。
- 若人工推进时 JD 为空，再由用户明确操作触发详情补采；补采失败时仍保留链接和已有字段。

AI 评分过滤的岗位已经有完整详情，继续保留。

### 10.3 人工推进流程

前端岗位池在 filtered 岗位上显示：

- 原岗位链接。
- 过滤原因。
- “仍要推进”按钮。

点击后弹出二次确认，明确说明这是用户主动忽略偏好过滤。确认后：

- 后端校验岗位确实是可覆盖的偏好过滤。
- 设置 `manual_override=1` 并记录 history。
- 让岗位进入待人工确认列表，即使分数低于当前阈值。
- 后续仍需正常确认，之后才能生成招呼语和发送。

建议接口：

- `POST /api/jobs/<job_id>/override-filter`
- body 必须包含 `confirmed: true`

不能只靠解析 `score_reason` 判断安全类型；使用明确 `filter_source`。

## 11. 独立 AI 设置页面

### 11.1 前端结构

新增：

- `pages/AiSettingsPage.tsx`
- 可复用组件 `components/config/AiSettingsForm.tsx`
- 路由 `/ai-settings`
- 左侧导航“AI 设置”入口和合适的 lucide 图标

把 `ConfigPage.tsx` 现有完整 AI 表单、基础诊断和高级诊断 UI 抽到可复用组件或新页面。配置页只保留：

- 当前服务商。
- 当前模型。
- 凭据是否已配置（只显示布尔/掩码状态）。
- 最近基础检测结果（如已有）。
- “打开 AI 设置”按钮。

不要在两个页面维护两套 AI 表单逻辑。

### 11.2 保存与诊断行为

保留上一轮已经实现的安全规则：

- 不显示真实 Key。
- 基础诊断不调用生成接口。
- 高级实际测试必须由用户主动二次确认。
- 选择模型后不能静默覆盖未保存配置。

## 12. 岗位链接和导出

### 12.1 岗位池

在 `JobsTable.tsx` 增加明显的“打开原岗位”操作：

- 使用 `target=_blank` 等价行为和 `noopener,noreferrer`。
- URL 为空时禁用并显示“链接不可用”。
- 点击链接不能触发行展开/复选框切换。
- 正常、filtered、greeting failed 状态都可打开。

岗位详情弹窗和工作台失败卡片也应复用同一入口。

### 12.2 导出

`job_export.py` 已有 URL 和 XLSX hyperlink，优先补测试而不是重写。新增字段后考虑导出：

- `filter_source/filter_reason/manual_override`
- `greeting_status`
- 最近招呼语失败原因（脱敏）
- 原岗位 URL

继续保持 CSV BOM、防公式注入和后端按 ID 查询。

## 13. 前端交互要求

### 13.1 工作台任务控制

任务卡显示：

- 明确 stage 和结构化进度。
- 运行时：暂停、停止。
- 正在暂停：禁用重复操作。
- 已暂停：继续、结束任务。
- 暂停任务与新任务冲突时：继续旧任务、结束旧任务并新建、取消。

不要把“暂停”和“停止”做成同一个按钮。

### 13.2 招呼语失败区

每张失败卡显示：

- 公司、岗位、原岗位链接。
- 简化失败原因。
- 最近失败时间和尝试次数。
- 单条重新生成。
- 复选框和批量重新生成。

页面刷新、切换页面或任务失败后这些岗位仍必须存在。

### 13.3 求职资料方案页

第一版可以放在配置页“个人信息”区域，也可以新增 `/profiles` 页面。优先保证简单：

- 顶部选择当前求职资料方案，默认方案预选。
- 显示 primary resume。
- “只上传一份简历即可开始”的明确提示。
- 折叠的“补充资料与事实”高级区。
- 待确认事实、重复和冲突数量可见。
- 任务启动预览显示本次使用的资料方案。

### 13.4 数量和文案

- 搜索设置增加“每组合采集数”。
- 保留“最大翻页数”，说明它是安全上限。
- 任务启动前展示 `关键词数 × 城市数 × 每组合目标 = 理论最大新增数`。
- 结束后按组合展示实际新增与不足原因。
- “单独采集”文案必须与后端只采集行为一致。

## 14. 文件级修改清单

下面是预期范围。允许按当前代码风格调整文件名，但职责必须清楚。

### 必改后端

- `src/bosshunter/db.py`
  - jobs 新字段、资料表、持久任务表和幂等迁移。
  - 招呼语状态/失败、过滤覆盖、任务和 profile 仓储函数。
  - `insert_job` 返回真实插入结果或增加等价 helper。

- `src/bosshunter/web/tasks.py`
  - 新状态机、pause/resume、持久化、配置快照、冲突响应。
  - snapshot 输出 stage/progress/capabilities。

- `src/bosshunter/cancellation.py`
  - 区分 pause/stop，放弃迟到 AI 响应。

- `src/bosshunter/web/server.py`
  - 任务控制、恢复、greet、profile/fact、过滤覆盖 API。
  - 修复 deliver 的数量不一致抛错。
  - workbench 返回明确的生成失败/待发送集合。
  - “单独采集”与全流程职责拆分。

- `src/bosshunter/scraper/jobs.py`
  - 每组合目标、真实新增计数、检查点、可中断等待、短缺报告。
  - 过滤岗位至少保留链接。

- `src/bosshunter/ai/scorer.py`
  - 使用统一 candidate context。
  - 接入持久 checkpoint 和 pause 控制；保持现有评分安全暂停规则。

- `src/bosshunter/ai/greeter.py`
  - 结构化 GreetingResult、岗位级失败、指定 ID 重试、统一 candidate context、pause checkpoint。

- `src/bosshunter/config.py`
  - `search.target_per_combo` 默认值。
  - 默认 profile 兼容配置；不要把复杂事实内容写入 YAML。

- `src/bosshunter/web/config_schema.json`
  - 每组合目标字段。
  - AI section 可以保留 schema 元数据，但通用配置页不再完整渲染。

- `src/bosshunter/web/resume_upload.py`
  - TXT/PDF 文本解析、安全限制和多文档支持所需 helper。

### 建议新增后端模块

- `src/bosshunter/candidate_profiles.py`
- `src/bosshunter/candidate_context.py`
- `src/bosshunter/task_store.py`
- `src/bosshunter/greeting_selection.py`（若 server/greeter 选择逻辑开始膨胀）

### 必改前端

- `src/bosshunter/web/frontend/src/App.tsx`
- `src/bosshunter/web/frontend/src/components/layout/Sidebar.tsx`
- `src/bosshunter/web/frontend/src/pages/ConfigPage.tsx`
- `src/bosshunter/web/frontend/src/pages/DashboardPage.tsx`
- `src/bosshunter/web/frontend/src/components/dashboard/JobsTable.tsx`
- `src/bosshunter/web/frontend/src/hooks/useDashboard.ts`
- `src/bosshunter/web/frontend/src/lib/status.ts`

### 建议新增前端

- `pages/AiSettingsPage.tsx`
- `components/config/AiSettingsForm.tsx`
- `components/config/CandidateProfilePanel.tsx`
- `components/dashboard/TaskConflictDialog.tsx`
- `components/dashboard/GreetingFailuresPanel.tsx`

避免把所有新逻辑继续堆进已经超过千行的 `DashboardPage.tsx`。

## 15. 推荐实施顺序

按以下阶段推进，每阶段先写测试再接 UI，降低停摆风险。

### 阶段 A：数据库兼容和任务基础

1. 新增幂等迁移及仓储函数。
2. 新增任务持久化和配置脱敏快照。
3. 扩展 cancellation 区分暂停/停止。
4. 完成任务状态机单元测试。

### 阶段 B：招呼语修复

1. GreetingResult 和岗位级失败。
2. 删除 deliver 数量不一致整体抛错。
3. workbench 返回失败/待生成/待发送集合。
4. 单条与批量重新生成。
5. 招呼语暂停、恢复和重启恢复测试。

先完成这一阶段即可直接解决用户当前最痛的问题。

### 阶段 C：采集目标与可暂停采集

1. `target_per_combo` 校验。
2. 组合级计数、短缺报告和去重。
3. scraper 控制点、checkpoint 和可中断等待。
4. 拆开“单独采集”和评分。

### 阶段 D：轻量求职资料方案

1. 默认 profile 和旧 `resume_path` 兼容。
2. 多文档上传和 deterministic pending facts。
3. 确认/去重/冲突 UI。
4. scorer/greeter 接统一 context，并在任务快照中记录引用。

### 阶段 E：过滤覆盖、AI 页面和链接 UI

1. 明确 filter_source 和人工推进 API。
2. filtered 岗位链接与二次确认。
3. 抽离独立 AI 设置页面。
4. 岗位池链接、招呼语失败卡和任务冲突对话框。

### 阶段 F：回归与构建

1. 运行全量后端测试。
2. 运行前端 `npm run build`。
3. 使用临时数据库做 API 冒烟。
4. 检查 diff 中无 Key、真实数据库、简历、Cookie 或导出文件。

## 16. 必须覆盖的测试

### 数据与迁移

- 旧 jobs 表可向前迁移且数据不丢失。
- 重复启动迁移无副作用。
- 旧非空 greeting 兼容显示为 generated。
- 默认 profile 能从现有 `resume_path` 懒迁移/映射。

### 招呼语

- 选择 7 个、2 个成功、5 个失败时：2 个保存，5 个仍可见，任务为 `completed_with_errors`。
- 单岗位失败不阻止下一岗位。
- 失败原因结构化、脱敏并可重试。
- 重试只处理指定失败岗位，不覆盖成功招呼语。
- 全局鉴权/额度/模型/限流错误进入 `paused`。
- 暂停时迟到 AI 响应不写数据库。
- 恢复只处理 remaining IDs。
- `generate_count != selected_count` 不再整体 `failed`。

### 任务控制

- running -> pausing -> paused -> running。
- paused -> stopped 后 checkpoint 清空、岗位数据保留。
- 暂停任务阻止新任务并返回三选一冲突信息。
- 新任务不能覆盖暂停任务。
- 原配置快照恢复；修改当前配置不影响旧任务。
- 快照不含 api_key/auth_token/cookie。
- 应用重启把遗留 running 任务转换为 paused，且不自动启动线程。

### 采集

- 1 关键词 × 2 城市 × 10 目标，分别达到 10 时总新增 20。
- 重复记录不计目标。
- 第一个城市不足不影响第二个城市继续。
- 达到 max_pages 后报告 shortfall。
- 暂停后从组合/页 checkpoint 恢复且不重复入库。
- 单独采集不调用 scorer。
- 配置数量的零、负数、字符串、超大值被后端拒绝。

### 求职资料

- 一份 MD/DOCX/TXT/PDF 简历可创建默认 profile 并直接通过 preflight。
- 扫描 PDF 无文本时明确报不支持 OCR。
- 多简历不会默默覆盖 primary。
- pending/rejected/conflict facts 不进入 AI 上下文。
- confirmed facts 去重后按预算进入上下文。
- 任务记录实际使用的 profile/document/fact IDs。

### 过滤覆盖

- filtered 岗位始终返回 URL 和原因。
- 未带 `confirmed=true` 不能人工推进。
- 可覆盖偏好过滤进入待确认流程。
- 验证码、账号、平台安全暂停不能被该接口覆盖。

### 前端与导出

- 左侧存在独立 AI 设置路由。
- 配置页不再重复完整 AI 表单。
- 岗位链接点击不改变选择或展开状态。
- 任务 stage 不再长期显示“等待后端返回阶段”。
- 导出继续包含 URL，XLSX 链接可点击，CSV 继续防公式注入。

## 17. 验证命令

后端：

```powershell
cd 'D:\简历\BossHunter'
& '.\.venv\Scripts\python.exe' -m unittest discover -s tests -p 'test_*.py'
```

如果虚拟环境不可用，可使用系统 Python，但必须报告实际解释器。

前端：

```powershell
cd 'D:\简历\BossHunter\src\bosshunter\web\frontend'
npm run build
```

安全检查：

```powershell
cd 'D:\简历\BossHunter'
git status --short
git diff --check
```

再检查 diff 是否意外包含 Key、真实配置、数据库、简历、Cookie、登录信息或导出文件。不要在检查命令中打印真实 Key。

## 18. 禁止事项

- 不清空、复制或试迁移用户真实 `data/bosshunter.db`。
- 不读取或输出真实 API Key、Cookie、OAuth、浏览器登录凭证。
- 不运行真实批量 AI 调用；测试全部 mock。高级真实 AI 测试必须由用户在界面主动确认。
- 不自动发送招呼语或简历。
- 不绕过验证码、账号封禁、平台限制或频率安全规则。
- 不引入向量数据库、OCR、复杂 RAG、自动简历路由或并行多任务。
- 不删除人工确认原则。
- 不回滚上一轮未提交改动。
- 不自动 commit 或 push。

## 19. 最终验收清单

完成后必须逐项回答：

- [ ] 招呼语部分失败后，全部岗位是否仍显示？
- [ ] 失败岗位是否能单条/批量重新生成？
- [ ] 成功岗位是否不会重复生成？
- [ ] 暂停是否不再派发下一条请求？
- [ ] 重启后是否只提示恢复而不自动运行？
- [ ] 暂停任务与新任务是否有继续/结束/取消三种选择？
- [ ] 一份简历是否可以直接开始？
- [ ] 每组合采集目标是否独立计数且重复不计？
- [ ] 单独采集是否真正只采集？
- [ ] filtered 岗位是否有链接、原因和安全的人工推进入口？
- [ ] AI 设置是否独立出现在左侧？
- [ ] 岗位池和导出是否都有完整 URL？
- [ ] 是否保持人工投递确认和平台安全停机？
- [ ] 全量后端测试和前端构建的真实结果是什么？
- [ ] 是否完全没有触碰真实数据库、简历和真实 AI 费用？

## 20. 完成后的回复格式

用中文报告：

1. 各阶段完成情况。
2. 核心问题如何修复。
3. 新增/修改的关键文件。
4. 数据迁移和向后兼容策略。
5. 后端测试数量、通过/失败和错误详情。
6. 前端构建结果。
7. 是否运行真实 AI 请求。
8. 是否触碰用户真实数据库或简历。
9. 尚存限制和需要用户手工验证的步骤。
10. 当前分支与 `git status --short`。

不能只回复“已完成”，也不能在未运行验证时声称通过。
