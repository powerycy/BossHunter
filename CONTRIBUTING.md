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
6. 等待对应维护域的 review

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

BossHunter 将维护责任划分为“核心与安全”“产品与 AI”“平台适配”三个维护域。候选维护者通常先以 `Triage` 权限进入 2–4 周观察期，不会因为贡献榜排名自动获得写权限。

首轮计划招募 6 名候选维护者：核心与安全 3 名、产品与 AI 2 名、平台适配 1 名。平台适配工作量增加后再补充第 7 名候选。

候选与晋升标准、权限范围和离任机制详见 [GOVERNANCE.md](GOVERNANCE.md)，现任及历任维护者记录见 [MAINTAINERS.md](MAINTAINERS.md)。
