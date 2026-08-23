# 事实契约

## 可用证据

- 简历原子字段：fact_type=resume_field，用 entity_type + group_id 重建同一经历。
- 技术文档或作品集：fact_type=star_story，保留 S/T/A/R、技术、专业技能、完整度和贡献等级。
- 用户补充：仅使用 status=answered 的 clarification；用 clarification_id 追溯。

同值但 group_id 不同的事实不得合并。edited_content 只在事实已接受时作为 effective_content 使用。

## 项目与 STAR 分组

- 优先用 entity_type + group_id 识别同一项目或竞赛；star_story 缺少稳定 group_id 时，再结合规范化标题和 source_filename。
- 一个 project 可以引用多条事实并包含多个 stars；一个 star 只描述一个较小的技术贡献或问题闭环。
- 不得因为存在多个 STAR 而重复输出同一项目标题，也不得把不同 group_id 的同名经历静默合并。
- 每个 star 自身的 fact_ids/clarification_ids 必须足以支撑 heading、技术名、Action、Result 和最终 bullet。

## 输出引用

每个输出条目至少包含一个存在于当前输入中的 fact_id 或 clarification_id。引用不能越权到待审核、已拒绝或已删除事实。Evidence Index 应能从条目回到来源文件和原文证据。
