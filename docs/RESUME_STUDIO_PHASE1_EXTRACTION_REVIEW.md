# Resume Studio 第一阶段抽取优化 Review

## 状态

- 开发位置：当前本地工作区
- GitHub：未创建 Issue、未提交 PR、未 push
- 范围：材料语义分类、简历精确抽取、技术文档/作品集 STAR 抽取、结构化证据和审核界面
- 验证：331 passed，11 subtests passed；前端生产构建通过

## 调用链

    上传文件
      -> 自动分类或用户指定类型
      -> resume: 原文逐字段抽取
      -> technical_document / portfolio: 一条或多条 STAR
      -> 逐字段或逐组件证据校验
      -> 人工审核

## 主要实现

### 数据库

src/bosshunter/db.py

- resume_sources 新增检测类型、检测置信度、分类证据和用户选择类型。
- resume_facts 新增事实类型、实体、字段、分组、结构化数据、完整度和待补充标记。
- 新增 resume_fact_evidence，保存每个简历字段或 STAR 组件的独立证据。
- _migrate_resume_studio() 对已有 SQLite 数据库只增列，不重建或覆盖旧表。

### 分类和抽取服务

src/bosshunter/resume_builder/service.py

- _resolve_source_kind()
  - 支持 auto、resume、technical_document、portfolio。
  - 用户指定类型优先。
  - 自动识别必须同时满足合法类型、置信度阈值和原文证据。
  - mixed、unknown 或低置信度结果停止提取并要求用户选择。
- _section_chunks()
  - 按 Markdown 标题保留章节边界。
  - 大章节继续按字符限制分片。
- _star_batches()
  - 简历把多个章节合并到约 30,000 字符；STAR 技术材料约 12,000 字符。
  - 短材料优先单次抽取，长材料最多 4 次。
  - 同时用于简历字段和 STAR 抽取，避免按标题逐次调用 LLM。
- _extract_resume_candidates()
  - 只接受原文逐字段值。
  - value 必须是 evidence 的子串。
  - 不对简历内容做 STAR 或润色。
- _extract_star_candidates()
  - 一份材料可返回多条项目故事。
  - S/T/A/R 每个组件分别保存 text 和 evidence。
  - Action 为必需项，其他缺失项进入 missing_fields。
  - 技术名称必须逐字存在于证据。
  - 专业技能允许从动作归纳，但标记为 derived。
  - 缺少个人贡献证据时，ownership 降为 unknown。

### 存储

src/bosshunter/resume_builder/store.py

- 保存和读取结构化 JSON。
- 聚合逐组件证据为 evidence_items。
- 重新提取时保留 accepted 事实。
- 去重键由单一 content 升级为事实类型、实体、字段、分组和内容。
- 不同经历中相同公司、职位或技术不会被误合并。

### API 和界面

src/bosshunter/web/server.py

- POST /api/resume-studio/sources/<id>/extract 接受 source_kind。

src/bosshunter/web/frontend/src/pages/ResumeStudioPage.tsx

- 每份材料可选择自动识别、简历、技术文档或作品集。
- 展示识别类型和置信度。
- STAR 卡片展示 S/T/A/R、完整度、贡献边界、技术、待确认技能和缺失字段。
- 明确提示文件保存在本地，但点击提取后文本片段会发送到配置的 AI 服务。

## 安全规则

- 分类证据必须存在于完整原材料，而不是只存在于截断预览。
- 简历字段 value 必须逐字存在于 evidence。
- 新增数字、日期、链接、电话、邮箱等结构化 token 会被拒绝。
- STAR 的 Action 缺失时整条候选被拒绝。
- Result 缺失时保留事实并标记需要补充，不生成虚构结果。
- 没有个人贡献证据时，不接受负责或主导归因。
- 所有新事实默认 pending，人工接受后才能进入生成范围。

## 测试覆盖

tests/test_resume_builder.py

- 旧数据库原位迁移。
- 自动分类到简历路径和低置信度阻断。
- 简历逐字段原文校验及幻觉字段丢弃。
- 多作品拆成多条 STAR。
- STAR 完整度、技术、技能和缺失字段。
- 个人贡献 unknown。
- 不同经历相同字段值不误去重。
- 重新提取保留 accepted 事实。
- 结构化证据落库。

tests/test_resume_studio_api.py

- 类型覆盖参数和非法类型 400。
- 上传、抽取、审核、生成、启用完整流程。
- 被简历版本引用的材料禁止删除。

## 验证命令

    .\.venv\Scripts\python.exe -m pytest tests\test_resume_builder.py tests\test_resume_studio_api.py -q -p no:cacheprovider
    # 26 passed

    .\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider
    # 331 passed, 11 subtests passed

    cd src\bosshunter\web\frontend
    npm.cmd run build
    # TypeScript 和 Vite production build 通过

## 建议 Review 断点

- 后端：_resolve_source_kind、_extract_resume_candidates、_extract_star_candidates、replace_fact_candidates。
- 前端：extractFacts 和 STAR 卡片渲染区域。

## 尚未验证的边界

- 已在用户明确授权后使用四份指定材料完成外部模型端到端回放；结果详见第二阶段实现 Review。
- PDF/DOCX 复杂双栏、页眉页脚和扫描 OCR 没有扩展。
- STAR 跨多个超长批次的语义合并仍依赖标题和 Action 去重。
- 专业技能属于语义归纳，必须人工确认。
- 本阶段没有改变现有 JD 定制简历生成器。
