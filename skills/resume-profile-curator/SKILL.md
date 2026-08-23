---
name: resume-profile-curator
description: "从 BossHunter Resume Studio 已接受的简历字段和 STAR 事实构建或刷新可追溯中文职业简历档案；适用于把同一项目下的多条技术贡献整理为多个 STAR、维护 Known Gaps、贡献边界与少量高价值补充问题，不用于直接针对 JD 改写或视觉排版。"
---

# Resume Profile Curator

## 入口条件

- 只读取 status=accepted 的事实和用户明确确认的补充回答。
- 不把 pending、rejected 或分类失败材料用于档案正文。
- 没有已接受事实时停止，并引导用户先在 Resume Studio 审核。

## 工作流

1. 在 Resume Studio 刷新补充问题。
2. 每轮最多处理 5 个最高优先级问题，顺序为个人贡献边界、冲突字段、结果与数字口径、缺失任务背景、派生技能。
3. 把回答保存为独立确认记录；不要覆盖原材料或原文证据。
4. 用 group_id、项目标题和来源文件归并项目；一个项目可以包含多个较小 STAR。
5. 生成 Career Profile 的 JSON 和 Markdown 草稿，在同一区域完成预览、下载与启用。
6. 检查质量报告、Known Gaps、未使用事实和证据覆盖率。
7. 只有用户审阅后才把版本启用为当前求职主简历。

## 生成约束

- 每个正文、项目、STAR、缺口或批准表达至少引用一个有效 fact_id 或已回答的 clarification_id。
- 新数字、日期、链接、技术名或实体名称必须阻断生成。
- ownership_level=unknown 时不能使用“负责、推动、主导、独立完成”等升级贡献的措辞。
- 不完整 STAR 保留为事实或 Known Gap，不补写缺失的结果。
- 项目标题只输出一次；同一项目的通信、感知、算法、控制、工程化等贡献分别作为 stars，不复制项目标题。
- 每个 STAR 必须有 Action，并优先表达为“使用/基于技术或方法，解决/完成问题或任务；已证实结果”。结果无证据时省略并加入 Known Gap。
- situation、task、action、result 用于内部校验，Markdown 不机械输出 S/T/A/R 标签。
- 派生技能在用户确认前不能当作已证明能力。
- Career Profile 是长期事实底稿；JD 定制和视觉排版继续由后续流程处理。

处理字段、分组和证据身份时读取 [fact-contract.md](references/fact-contract.md)。判断贡献动词时读取 [ownership-levels.md](references/ownership-levels.md)。生成或检查文字时读取 [honesty-rules-cn.md](references/honesty-rules-cn.md)。设计追问时读取 [clarification-policy.md](references/clarification-policy.md)。
