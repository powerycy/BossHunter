# Resume Studio 第二阶段实现 Review

## 结论

本轮已在本地完成两项工作：

1. 把简历、技术文档和作品集的章节分片合并为有上限的抽取批次，减少 LLM 往返。
2. 实现 accepted facts 到补充确认、Career Profile、质量报告、版本激活和 Skill 路由的完整第二阶段。

没有创建 Issue、提交 commit、push 或 PR。resume_markdown 和 start-bosshunter.cmd 是用户已有未跟踪内容，本轮未修改。

## 调用次数优化

service.py 新增语义批处理：

- 先由章节分片保留 Markdown 标题边界。
- 简历默认每批目标 30,000 字符；STAR 技术材料按约 12,000 字符合并，并从首次调用启用严格逐字证据提示。
- 短材料整体进入一批。
- 长材料顺序重平衡，最多 4 批；每个 STAR 批次最多返回 4 条高价值故事。
- 简历字段抽取和技术文档/作品集 STAR 抽取共用同一批处理边界。

四份指定材料的纯本地结构回放：

| 文件 | 字符数 | 标题数 | 优化前按标题估算 | 当前计划调用 |
|---|---:|---:|---:|---:|
| 校招简历样本.md | 1,616 | 14 | 14 | 1 |
| 近期简历样本.md | 1,235 | 13 | 13 | 1 |
| 技术方案样本.md | 19,634 | 67 | 66 | 2 |
| 私有项目说明样本.md | 6,459 | 32 | 32 | 1 |

本组材料的正常抽取调用从 125 次降为 5 次。网络往返上限已经由章节数改为受控批次数；每次失败最多重试一次。

回放脚本 scripts/replay_resume_profile_materials.py 默认只做本地分批计划。只有显式提供 --allow-external-ai 才会发送材料到当前 AI 服务；默认路径不会外发内容。

## 第二阶段数据流

    accepted facts
      -> 确定性缺口和冲突扫描
      -> 每轮最多 5 个补充问题
      -> 用户明确确认或忽略
      -> AI 组织 Career Profile JSON
      -> 确定性引用、token、贡献动词校验
      -> Markdown + JSON + quality_report
      -> 用户审阅后标记当前版本

### 数据库

db.py 新增：

- resume_clarifications：问题、优先级、回答、状态和关联事实。
- resume_profile_versions：JSON、Markdown、质量报告、文件路径和激活状态。
- resume_profile_facts：Profile 到 accepted fact 的引用。
- resume_profile_clarifications：Profile 到确认回答的引用。

材料删除引用计数同时检查主简历版本与 Career Profile；已有版本引用时拒绝删除。

### 缺口队列

refresh_profile_clarifications() 只读取 accepted facts，并识别：

- STAR 缺失的 Situation、Task 或 Result。
- ownership unknown 或缺少个人动作证明。
- derived professional skill 尚未确认。
- 量化 Result 缺少口径或验证来源。
- 同一 entity_type、group_id、field_name 存在冲突值。

已回答和已忽略项目在刷新后保留状态。每轮只发布最高优先级的 5 个未解决问题。

### Career Profile 生成与防幻觉

compose_career_profile() 只向模型提供 accepted facts 和 answered clarifications。

生成后执行以下确定性检查：

- 每个条目必须引用有效 fact_id 或 clarification_id。
- 引用只允许来自当前 accepted/answered 集合。
- 新数字、日期、联系方式、链接等结构化 token 直接阻断。
- 负责、推动、主导、独立完成等强贡献动词必须存在于引用证据或确认回答。
- 至少有一个可追溯正文 section。

无来源 token 或贡献动词升级会触发一次受控修复生成。修复稿仍执行完全相同的确定性校验；第二次违规继续阻断，不会放宽规则。

质量报告包含：

- accepted / used fact 数量。
- unused fact IDs。
- 已使用确认回答数量。
- 未解决问题数量。
- incomplete fact 数量。
- evidence coverage。

生成文件使用临时文件后原子替换。版本默认 draft，用户审阅后才能标记 active。

## API

- POST /api/resume-studio/profile/clarifications/refresh
- PATCH /api/resume-studio/profile/clarifications/{id}
- POST /api/resume-studio/profile/compose
- POST /api/resume-studio/profile/versions/{id}/activate
- GET /api/resume-studio/profile/versions/{id}/download
- GET 下载接口增加 format=json，可下载机器可读版本。

GET /api/resume-studio 现在同时返回 clarifications 和 profile_versions。

## 前端 Review

ResumeStudioPage.tsx 新增第 5 区域：

- 刷新高价值待确认问题。
- 回答或忽略问题。
- 显示待确认与已回答数量。
- 生成 Career Profile。
- 显示事实使用数、证据覆盖率和未解决问题。
- 预览 Markdown。
- 下载 Markdown / JSON。
- 标记当前 Profile 版本。

## Skill

skills/resume-profile-curator 是独立 Skill，不把完整流程继续塞入根 SKILL.md。

Skill 包含：

- SKILL.md：触发范围、入口条件、工作流和生成约束。
- fact-contract.md：accepted fact、group_id 和引用身份。
- ownership-levels.md：参与、负责、推动、主导等证据门槛。
- honesty-rules-cn.md：中文事实安全边界。
- clarification-policy.md：每轮最多 5 个高价值追问。
- agents/openai.yaml：可发现名称、描述、默认提示和隐式调用策略。

官方 quick_validate.py 在 PYTHONUTF8=1 下验证通过。首次按 Windows GBK 读取中文失败，未发现 Skill 结构问题。

## 自动验证

    .\.venv\Scripts\python.exe -m pytest -p no:cacheprovider tests\test_resume_builder.py tests\test_resume_studio_api.py -q
    26 passed

    .\.venv\Scripts\python.exe -m pytest -p no:cacheprovider -q
    331 passed, 11 subtests passed

    .\.venv\Scripts\ruff.exe check scripts\replay_resume_profile_materials.py src\bosshunter\resume_builder tests\test_resume_builder.py tests\test_resume_studio_api.py
    All checks passed

    cd src\bosshunter\web\frontend
    npm run build
    TypeScript + Vite build passed

server.py 全文件 Ruff 仍会报告仓库既有的 BLE001、TRY004、未使用局部导入和 queue_lock 等告警。本轮没有为消除这些历史告警扩大修改范围；后端语法、API 测试和全量测试均通过。

## 四份材料真实外部 AI 验收

用户明确授权后，使用当前配置的外部 AI 服务完成最终端到端回放：

| 文件 | 类型 | 事实数 | LLM 调用 | 耗时 |
|---|---|---:|---:|---:|
| 校招简历样本.md | resume | 62 | 1 | 79.90 秒 |
| 近期简历样本.md | resume | 57 | 1 | 88.94 秒 |
| 技术方案样本.md | technical_document | 4 | 2 | 138.90 秒 |
| 私有项目说明样本.md | technical_document | 3 | 1 | 54.17 秒 |

最终结果：

- 四份抽取全部通过，共 126 条严格证据事实。
- 抽取阶段 5 次调用；Career Profile 1 次调用；总计 6 次。
- 总耗时 431.25 秒，平均每次调用 71.84 秒。
- Career Profile 使用 55 条事实，evidence coverage 为 43.65%。
- clarification queue 保留最高优先级的 5 个问题。
- 最终 Profile 没有触发安全修复，直接通过 token、引用和贡献动词校验。
- 回放不打印材料正文，使用临时数据库和临时输出目录，结束后自动删除。

真实测试也验证了模型输出存在轮次波动。服务因此保留一次传输/无效 JSON/零候选重试，但正常路径仍按 1/1/2/1 调用。

## VS Code 断点建议

使用已有 .vscode/launch.json：

- Python: Resume Builder Core Tests
- Python: Resume Studio API Tests
- Resume Studio: Full Stack

建议断点顺序：

1. service.py 的 _star_batches：确认旧的 14/13/66/32 个分片如何合并成 1/1/2/1 批。
2. service.py 的 extract_source_facts：确认类型路由。
3. service.py 的 refresh_profile_clarifications：确认问题优先级与每轮上限。
4. service.py 的 _validated_profile_item：确认 token 和贡献动词阻断。
5. service.py 的 compose_career_profile：确认 accepted facts、回答和质量报告。
6. store.py 的 replace_open_clarifications 与 create_profile_version：确认状态保留和引用落库。
7. server.py 的 profile API 路由：确认 HTTP 错误映射。

## 建议 Review 顺序

1. 先看 service.py 的批处理与测试，确认调用次数边界。
2. 再看 db.py 和 store.py，确认引用身份与删除保护。
3. 审核 compose_career_profile 的输入和生成后校验。
4. 审核 API 测试，确认完整工作流。
5. 最后查看 ResumeStudioPage.tsx 和 Skill 文案。

## 已知后续项

- Career Profile 当前是独立长期资产，不会自动改写 profile.resume_path；主简历启用流程保持原状。
- 补充回答以 clarification 独立证据保存，并通过“确认回答”动作批准；没有伪装成原材料证据。
- 尚未实现视觉排版、RenderCV 输出或针对 JD 的 Profile 选择器。
- 超过 12,000 字符的 STAR 跨批次故事仍主要依赖模型在单批内部拆解，批间只做确定性去重，不做第二次汇总调用。
