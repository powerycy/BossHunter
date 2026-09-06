# 贡献指南

感谢你对 BossHunter 的关注！欢迎提交 Issue 和 Pull Request。

## 行为准则

- 保持友善和建设性的讨论
- 尊重每一位贡献者的时间

## 提交 Issue

- Bug 报告请使用 [Bug 报告模板](.github/ISSUE_TEMPLATE/bug_report.md)
- 选择器失效请使用 [选择器失效模板](.github/ISSUE_TEMPLATE/selector_broken.md)
- 新功能建议请先开 Issue 讨论
- 希望长期参与维护，请阅读 [项目治理](GOVERNANCE.md) 并使用 [候选维护者申请模板](.github/ISSUE_TEMPLATE/maintainer_application.md)

## 提交 Pull Request

### 接受的 PR 类型

- Bug 修复
- 选择器适配更新
- 文档改进
- 新功能（需先开 Issue 讨论）

### 不接受的 PR

- **提高默认发送频率** — 这会增加所有用户的封号风险
- **绕过人工确认环节** — 人工审核是核心安全机制
- **绕过平台安全检测的新方法** — 项目定位是效率工具，不是攻防工具
- **降低反检测策略的保守程度** — 如缩短间隔、扩大时间窗口等

### PR 流程

1. Fork 仓库
2. 基于 `main` 创建功能分支：`git checkout -b feat/your-feature`
3. 提交代码，确保 `bosshunter --help` 正常运行
4. 运行相关测试；涉及前端时同时确认前端可以构建
5. 推送并创建 Pull Request
6. 由维护者认领或分配一名推进负责人（Assignee），确认风险等级并请求相应 Review；作者负责实现和回复
7. 等待至少 1 名非作者正式维护者批准；高风险 PR 需要 2 名不同的非作者正式维护者批准，其中 1 人明确完成安全检查
8. 通过相关测试和仓库检查、处理阻塞意见后，由正式维护者合并；无人响应时由维护者内部协调，不等待项目负责人技术终审

项目负责人 `@powerycy` 是正式技术维护者，可参与代码审核和技术合并，其有效批准按一人计入；本人提交或参与编写的技术改动不得自审计票，高风险 PR 仍须两名不同的非作者维护者批准。README 由包括项目负责人在内的正式维护者审核。PR 项目贡献记录见 [CONTRIBUTORS.md](CONTRIBUTORS.md)，维护贡献记录及由项目负责人单方面决定的评分见 [MAINTENANCE_CONTRIBUTIONS.md](MAINTENANCE_CONTRIBUTIONS.md)；贡献记录应与技术改动分开提交。

### 代码风格

- Python: 遵循 ruff 默认规则，行长 120
- 提交信息：中文或英文均可，简洁描述改动

## 本地开发

```bash
# 安装开发依赖
pip install -e ".[dev]"

# 检查代码风格
ruff check src/

# 运行 CLI
bosshunter --help
```

## 选择器维护

某直聘页面结构可能随时变化。如果你发现选择器失效：

1. 打开 Chrome DevTools 检查新的 DOM 结构
2. 更新对应的选择器代码
3. 提交 PR 并说明变化

这是最欢迎的贡献类型之一。

## 成为维护者

BossHunter 的正式维护者共同维护整个项目，不划分固定模块或席位；个人擅长方向只用于协作参考，不限制 review 和跟进范围。候选维护者通常先以 `Triage` 权限进入 2–4 周观察期，不会因为项目贡献排名或维护贡献记录自动获得写权限。

正式维护者连续 14 天没有可核实的维护活动，且未提前声明暂停或请假，将进入联系确认与权限复核流程。有效活动包括 Review、Issue 闭环、安全复核、发布验证和治理交接；机械评论以及维护者自己的功能 PR 不计作维护活动。

候选与晋升标准、权限范围和离任机制详见 [GOVERNANCE.md](GOVERNANCE.md)，现任及历任维护者记录见 [MAINTAINERS.md](MAINTAINERS.md)。
