# Resume Studio 第二阶段 Skill 具体规划

> 实施状态：本地第二阶段已完成。具体代码、验证结果和与规划的边界见 RESUME_STUDIO_PHASE2_IMPLEMENTATION_REVIEW.md。

## 定位

建议新增独立 Skill：resume-profile-curator。

它不直接针对 JD 生成投递简历，而是把第一阶段审核后的结构化事实整理成长期可复用、可追溯的职业事实底稿。现有 generate_tailored_resume() 再从已启用的主简历派生岗位版本。

    第一阶段：材料 -> typed facts / STAR -> 人工审核
    第二阶段 Skill：accepted facts -> 补充追问 -> career profile
    现有定制流程：career profile / 主简历 + JD -> 岗位简历

这样避免把事实发现、确认、JD 定制和排版混在一次模型调用中。

## 第一阶段测试对 Skill 设计的影响

### 一个文件会产生多个分片

章节化测试证明，同一份简历可能触发多个抽取调用。Skill 不能按文件简单拼接，必须使用 entity_type 和 group_id 重建工作、项目和教育实体。

### STAR 可以不完整

测试中完整度分别出现 75% 和 25%。Skill 必须先处理 missing_fields，不能把不完整 STAR 润色成完整成果。

### ownership 是独立安全边界

技术材料可能明确描述方案但没有个人贡献。ownership_level 为 unknown 时只能追问，不能自动采用负责、推动或主导。

### derived skill 需要确认

专业技能可以从明确动作归纳，但不是逐字字段。derived skill 必须进入确认队列，确认后才能进入 Technical Skills 或 Domain Expertise。

### accepted 事实需要稳定身份

第一阶段已按类型、实体、字段、分组和内容去重。Skill 应读取 fact ID 和 evidence，不应通过文本相似度再创建另一套事实身份。

### 当前定制简历已有安全校验

src/bosshunter/ai/resume.py 已具备新增数字、联系方式、日期、占位符和篇幅检查。第二阶段应提供更可靠的主简历输入，不重复实现 PDF 渲染或 JD 生成器。

## 可借鉴原则

- vignzpie/resume-agent-skills
  - career profile 作为长期资产
  - Achievements Bank、Known Gaps、Pre-built Framings
  - 只针对高价值缺口追问
  - https://github.com/vignzpie/resume-agent-skills
- coinluu/resume-jd-optimizer-cn
  - 个人贡献与团队成果分离
  - 支持、参与、负责、推动、主导的证据门槛
  - 记录数字来源和面试可解释性
  - 信息不足时只提出少量具体问题
  - https://github.com/coinluu/resume-jd-optimizer-cn
- rendercv/rendercv-skill
  - 内容模型与视觉设计分离
  - 类型化 Schema
  - 生成结果必须经过确定性校验
  - https://github.com/rendercv/rendercv-skill

本阶段只借鉴数据和工作流原则，不引入这些仓库的运行时依赖。

## Skill 输入契约

只读取：

- status 为 accepted 的事实。
- id、source_id、source_filename。
- fact_type、entity_type、field_name、group_id。
- effective_content、structured_data、evidence_items。
- completeness、needs_clarification。
- 已有主简历版本。
- 用户明确提供的目标方向，可选。

默认不读取 rejected 事实。pending 只用于待审核清单，不能进入主档案。

## Skill 输出契约

1. career_profile.json
   - 机器可读、类型化、保留 fact IDs。
2. career_profile.md
   - 用户可读的长期职业事实底稿。
3. clarification_queue.json
   - 缺失 STAR、贡献边界、冲突字段和 derived skill。
4. profile_quality_report.md
   - 完整度、证据覆盖、风险和仍需确认内容。

所有生成条目必须带 fact_ids。用户补充的新事实必须先回写 Resume Studio 并接受，不能只存在于 Markdown。

## career_profile 结构

1. 基本信息与联系方式
2. 目标方向与职业标题
3. 工作经历快照
4. 工作经历深挖
5. 项目与作品
6. 教育经历
7. 技术栈
8. 专业领域能力
9. 领导力与协作
10. 开源、文章、演讲、专利
11. 证书与奖项
12. Achievements Bank
13. Known Gaps
14. Approved Framings
15. Evidence Index

Evidence Index 映射档案条目到 fact IDs、来源文件和原文证据。

## 工作流

### Phase 0：Preflight

- 至少存在一条 accepted 事实。
- 检查非法结构化 JSON。
- 列出分类失败、pending 和 rejected 数量。
- 检查冲突的姓名、邮箱、公司日期和教育日期。
- 不满足条件时只输出阻断原因，不生成档案。

### Phase 1：实体重建

- 按 entity_type 和 group_id 聚合简历原子字段。
- 按 STAR group_id 聚合项目故事。
- 同值不同 group 保持分离。
- 同一 group 内冲突值进入 clarification queue。

### Phase 2：证据与完整度评分

每个候选经历计算：

- evidence coverage
- STAR completeness
- ownership certainty
- metric traceability
- technology-to-action binding
- interview defensibility

评分只用于决定追问顺序，不代表录用概率。

### Phase 3：针对性追问

每轮最多 5 个问题，只问会改变简历结论的缺口：

1. ownership unknown
2. Action 明确但 Result 缺失
3. Result 有数字但没有数据口径
4. derived skill 尚未确认
5. 同一经历存在冲突日期或角色

问题格式：

    [对应事实或风险]
    具体问题：
    建议回答范围：
    将影响的档案位置：

不得给用户可直接照抄的虚构答案。

### Phase 4：确认回写

- 创建 user_confirmed 来源事实。
- evidence 类型标记为 user_confirmation。
- 保存确认时间和关联原事实 ID。
- 仍通过 pending -> accepted 审核流程。
- 不直接修改原始材料证据。

### Phase 5：Achievements Bank

从完整且已确认的 STAR 中生成内部成就条目：

- 原始 S/T/A/R
- Action -> System -> Outcome 候选表述
- 可防守的数字
- 技术和专业技能
- ownership
- fact IDs
- 适合的岗位方向标签

候选表述不会自动启用。

### Phase 6：Known Gaps 与 Approved Framings

Known Gaps 示例：

- 文档证明团队采用 Kubernetes，但没有证明本人负责迁移。
- 结果为定性描述，没有可确认数字。
- 参与合规整改，但没有正式认证证据。

Approved Framings 必须由用户确认，例如：

- “参与并支持”而不是“主导”。
- “对齐某标准要求”而不是“通过认证”。

后续生成器不得覆盖用户批准的表达。

### Phase 7：档案生成与确定性校验

- 每个经历、项目、技能和数字至少引用一个 accepted fact ID。
- 新数字、新日期、新链接和新技术名直接阻断。
- ownership 动词不得高于证据等级。
- incomplete STAR 不得生成完整成果叙述。
- 输出 JSON 先通过 Schema 校验，再生成 Markdown。

## 建议目录

    skills/resume-profile-curator/
      SKILL.md
      references/
        fact-contract.md
        honesty-rules-cn.md
        ownership-levels.md
        clarification-policy.md
      templates/
        career_profile.md
        profile_quality_report.md
      scripts/
        export_accepted_facts.py
        validate_career_profile.py
      evals/
        cases/
        expected/
        README.md

根目录 SKILL.md 后续只增加路由说明，不把完整档案工作流继续塞入现有大 Skill。

## 第二阶段数据扩展

resume_clarifications：

- fact_id
- question
- answer
- status
- created_at、answered_at

resume_profile_versions：

- version
- json_path
- markdown_path
- fact_ids
- quality_report
- status

用户回答不覆盖原文 evidence，而是保存为独立证据来源。

## 与现有代码的接入点

- resume_builder/store.py
  - 导出 accepted fact bundle。
  - 保存 clarification 和 profile version。
- resume_builder/service.py
  - 构建档案、校验 fact IDs、激活档案版本。
- web/server.py
  - 获取缺口、提交回答、生成和激活职业档案。
- ResumeStudioPage.tsx
  - 缺口队列、回答确认、Career Profile 预览。
- ai/resume.py
  - 后续只读取已激活主简历或 Career Profile 派生结果。
  - 保留现有事实 token 和篇幅检查。

## Evals

固定案例至少包括：

1. 完整简历，字段可直接进入档案。
2. 同一公司多段任职，不误合并。
3. 两份材料的任职日期冲突。
4. 技术文档有方案、无个人贡献。
5. STAR 缺 Result。
6. Result 有百分比但无数据来源。
7. 团队成果不能升级为个人主导。
8. derived skill 用户接受。
9. derived skill 用户拒绝。
10. 重新生成时保留 Approved Framings。
11. 模型新增数字、日期或技术名时阻断。
12. 多材料重复事实保留单一事实身份和全部来源。

评测同时检查 JSON Schema、fact ID 有效性、证据覆盖、ownership 动词等级、结构化 token 无新增和输出稳定性。

## 实施拆分

### Phase 2A：只读 Skill 和导出器

- Skill 目录和输入契约。
- accepted facts 导出。
- clarification queue 只读生成。
- JSON Schema 和离线 eval。

### Phase 2B：追问与回写

- clarification 数据表和 API。
- 用户回答作为独立来源事实。
- Web 审核。

### Phase 2C：Career Profile 版本

- JSON 和 Markdown 双输出。
- Evidence Index。
- Known Gaps 和 Approved Framings。
- 激活与历史版本。

### Phase 2D：现有定制简历接入

- 已激活 Career Profile 生成主简历。
- 与现有 generate_tailored_resume() 衔接。
- 不改变自动投递确认和发送安全规则。

## 验收标准

- Skill 不能读取 rejected 事实生成正向经历。
- 每个档案条目都能追溯到 accepted fact IDs。
- 缺 Result 或 ownership 时只追问，不补写。
- 用户回答以独立证据保存。
- 同值不同经历不误合并。
- Approved Framings 在重新生成时保持不变。
- 输出 JSON 通过确定性 Schema 校验。
- 真实材料回放不会新增未确认数字、日期、技术或职责。
- 现有岗位评分、招呼语和定制简历测试保持通过。

## 明确不包含

- JD 关键词匹配和 ATS 评分。
- RenderCV 或 Typst 排版集成。
- 自动投递或自动发送简历。
- 扫描 PDF OCR。
- 未经用户确认的数字估算。
