# Resume Studio 多 STAR 与安全改造评审说明

## 评审目标

本轮在本地完成八项改造，不包含提交、推送、Issue、PR 或上线操作：

1. 事实审核区固定为 720px 高度并使用纵向滚动条。
2. 测试中的临时目录断言先对两端执行 `resolve()`，兼容 macOS `/var` 与 `/private/var` 的真实路径差异。
3. 每次提取事实或生成职业简历档案前都展示发送范围、敏感数据类型、外部 AI 服务和取消后果；服务端要求 `external_ai_consent=true`，不能只依赖前端弹窗。
4. 下载和激活生成版本时，重新解析数据库路径，并同时校验直接父目录、版本 ID 推导的精确文件名和普通文件属性。
5. 新增带“清空”文本确认的工作室清空接口，只处理工作室表和受管命名文件。
6. Career Profile 改为接近中文简历的结构化输出，技术/方法、问题/任务和已证实结果形成独立贡献段落。
7. 生成、预览、下载、历史版本和启用合并到同一个步骤；启用 Career Profile 会同步成为当前求职主简历路径。
8. 核心数据契约改为一个 `project` 下包含多个 `stars`，项目标题只出现一次。

## 重点实现

### 多 STAR 数据契约

`profile.projects[]` 是项目、竞赛或作品的边界，`projects[].stars[]` 是该项目中的较小技术贡献。每个 STAR 至少包含：

- `action`：实际采用的技术、方法或专业动作；
- `bullet`：面向简历的紧凑表达；
- `fact_ids` / `clarification_ids`：该条文字自己的证据引用；
- 可选的 `heading`、`situation`、`task`、`result`、`technologies`。

Markdown 只输出一次项目标题，再以空行分隔各 STAR，例如：

```markdown
### 项目名称

- **通信与协同**：使用某技术完成某任务；得到已证实结果。

- **控制算法**：基于某方法解决某问题；得到已证实结果。
```

内部仍保留 STAR 字段用于校验，但不会机械显示 S/T/A/R 标签。没有 Result 证据时不补写结果，而是保留到“待补充信息”。

### 事实约束

- 只读取 `accepted` 事实和 `answered` 补充回答。
- 项目标题、元信息、每个 STAR 的 Action、技术名、结果和最终 bullet 都要由该对象自己的引用支撑。
- 新数字、日期、英文技术名和更高等级贡献动词继续由确定性校验阻断。
- Profile 最多选择 8 个项目，每个项目 1 至 5 条不重复 STAR；不要求消耗所有原子事实，避免生成逐字段堆砌的长清单。
- 面向模型的结构数据已经压缩：简历字段只保留 value，STAR 只保留标题、S/T/A/R 文本、技术名、贡献边界和缺口，省略重复 evidence。

### 外部 AI 明示同意

受保护操作包括：

- `/api/resume-studio/sources/<id>/extract`
- `/api/resume-studio/compose`（保留的兼容接口）
- `/api/resume-studio/profile/compose`

未携带布尔值 `external_ai_consent=true` 时返回 HTTP 428。前端每次操作都重新询问，不保存永久同意，也不会把上传动作本身视为同意。

### 下载与激活路径约束

不能只检查 `Path.exists()`。当前规则为：

- 主简历必须是 `data/resumes/master_resume_<version-id前12位>.md`；
- Profile 必须是 `data/career_profiles/career_profile_<profile-id前12位>.md/.json`；
- `resolve()` 后必须仍是对应受管目录的直接子文件；
- 激活 Profile 时 Markdown 和 JSON 两个文件都要通过校验；
- 越界、符号链接逃逸、文件名与版本 ID 不一致或不是普通文件时返回 409。

### 清空边界

清空要求同时满足 `confirmed=true` 和 `confirmation_text="清空"`。删除范围：

- 9 张 `resume_*` 工作室表；
- `resume_sources/<32位来源ID>_*`；
- `resumes/master_resume_*.md`；
- `career_profiles/career_profile_*.md/.json`。

不递归删除目录，不删除 `resume_markdown`，不删除 `data/resumes` 中不符合工作室生成命名的手动简历。只有当前配置指向上述工作室生成文件时才解除 `profile.resume_path`。

本轮实际清空结果：删除 7 个受管文件；清空来源 3、事实 39、事实证据 49、主简历版本 2、版本事实引用 104、确认问题 10、Profile 版本 1、Profile 事实引用 38、Profile 问题引用 5。清空后 9 张表均为 0；两份 `resume_markdown` 原始材料和一份非工作室命名的手动简历仍存在。

## 验证结果

- Resume Studio 定向测试：30 passed。
- 完整 Python 测试：335 passed，11 subtests passed，最终状态耗时 201.06 秒。
- 前端：`tsc && vite build` 通过。
- Skill：`quick_validate.py` 返回 `Skill is valid!`。
- 本轮 Resume Studio 服务、存储和测试文件的 Ruff 检查通过；整个既有 `server.py` 仍有 38 项历史 Ruff 告警，本轮未批量改写无关路由。
- macOS 路径断言：已改为 resolved path 对 resolved root 的 `is_relative_to`。
- 路径篡改回归：主简历/Profile 的下载与激活四条路径均返回 409。
- 清空回归：错误确认被拒绝；正确确认清空工作室；手动简历文件和配置保持不变。
- 多 STAR 回归：同一个项目标题只出现一次，两个 STAR 分段输出并分别保留技术标题。

## 四份授权材料回放

测试全程使用临时数据库和临时输出目录，退出后删除测试存储。

第一轮：四份材料提取全部通过，共 8 次外部 AI 调用；两份简历分别得到 83、63 条事实，两份技术材料得到 1、3 条 STAR。Profile 首轮返回后触发确定性修复，第二次 Profile 调用超时，因此没有生成最终 Profile。

第二轮：两份简历样本与私有项目说明样本通过；技术方案样本的一批响应在重试后仍不是有效 JSON，因此未进入 Profile 阶段。随后增加首个完整 JSON 对象解析、短证据、JSON 标准转义和每批最多 3 条 STAR 的规则。

修复后的技术方案样本单文件回放通过：本地正常规划为 2 个批次，实际因无有效候选/格式重试使用 5 次调用，最终得到 1 条高层 STAR。该结果确认格式失败已能恢复，但也保留一个待优化边界：外部模型证据不合格时，重试仍可能把正常 2 次调用放大到 5 次，且长方案的 STAR 召回率偏低。

因此，本轮可以确认单元/集成契约、前端流程、安全边界和四类材料提取路径；不能声称四份材料合并后的最终 Profile 已完成一次成功的真实外部 AI 端到端生成。代码仍会在生成时执行确定性校验和一次修复，UI 也要求用户预览后再启用。

## 建议评审顺序

1. `src/bosshunter/resume_builder/service.py`：提示词、`projects[].stars[]` 校验、Markdown、清空文件边界。
2. `src/bosshunter/web/server.py`：同意门禁、下载/激活路径校验、清空 API、Profile 启用配置。
3. `src/bosshunter/web/frontend/src/pages/ResumeStudioPage.tsx`：弹窗文案、固定滚动区、统一生成/预览/启用页面。
4. `tests/test_resume_builder.py` 与 `tests/test_resume_studio_api.py`：多 STAR、macOS、同意、路径篡改和清空回归。
5. `skills/resume-profile-curator/`：Skill 工作流和项目分组契约。

## 本轮未做

- 未提交、推送、创建 Issue、创建 PR 或上线。
- 未把真实材料或生成结果写入 Git 跟踪文件。
- 未自动启用未经用户审阅的外部 AI 真实生成结果。
