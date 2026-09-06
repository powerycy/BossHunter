# 发布前安全审查清单

## Pre-Mortem 发现的问题及解决方案

---

### Tiger 1: 商标/法律风险

**问题**：项目名包含 "某直聘" 商标
**解决**：
- [x] 对外名称改为 `BossHunter`（中性名称）
- [x] README 免责声明放在最顶部
- [x] 代码中移除 "bosshunter" → 改为 "bosshunter"（包名、模块名）
- [ ] 注释和文档中仅以 "目标平台" 或功能描述指代

### Tiger 2: 敏感数据泄露

**问题**：git 历史可能包含 API Key、真实简历、个人求职数据
**解决**：
- [x] 新建干净仓库，不从旧 repo 迁移 git history
- [x] .gitignore 严格排除 config.yaml、*.db、resume.md
- [ ] 上传前全局搜索：`grep -r "sk-" src/` 确认无硬编码 key
- [ ] 上传前确认 data/ 目录不含任何文件
- [ ] config.example.yaml 中所有值为示例数据

### Tiger 3: 安装复杂度

**问题**：需要 Python 3.11+ / Node.js 22+ / Chrome CDP / AI API Key
**解决**：
- [x] README 清晰列出所有前置条件和版本
- [x] Chrome 远程调试提供两种开启方式
- [ ] 考虑后续提供 Docker Compose 一键启动（Phase 2）
- [ ] 前端 dist/ 预构建，用户不需要自己 npm build

### Tiger 4: 平台更新导致失效

**问题**：某直聘更新 DOM 结构/反爬策略随时可能破坏功能
**解决**：
- [x] SKILL.md 说明了状态流转和架构，便于社区维护
- [ ] 在 README 添加 "维护状态" badge
- [ ] 关键选择器集中管理（单独 selectors.py），便于快速修复
- [ ] Issues 模板包含 "选择器失效" 类型

### Paper Tiger: 竞品

**实际情况**：类似工具市场空间足够，且大多数是浏览器插件形态，CLI + AI 评分的方案差异化明显。不构成实质风险。

### Elephant: 时机问题

**坦诚面对**：你目前仍在使用这个工具求职。开源后：
- 如果项目火了，某直聘可能注意到并加强检测
- 建议：求职完成后再公开发布，或先以 Private repo 形式准备好

---

## 发布前 Checklist

### 代码层面
- [x] 全局替换 `bosshunter` → `bosshunter`（包名、import、CLI 名）
- [ ] 移除所有硬编码的个人信息（简历内容、城市偏好等）
- [ ] 确认 `config.py` DEFAULTS 中无个人偏好泄露
- [ ] 搜索并移除所有 API Key / Token 硬编码
- [ ] 移除所有 debug/dev 时的临时代码

### 前端层面
- [ ] 构建 production 版本：`cd src/bosshunter/web/frontend && npm run build`
- [ ] 确认构建产物不含 source map（隐私）
- [ ] 移除 package-lock.json 中的私有 registry（如有）

### 仓库层面
- [ ] 新建 GitHub 仓库（Public / Private 取决于时机）
- [ ] 首次 commit 确认 .gitignore 生效
- [ ] 添加 GitHub Topics: `job-hunting`, `automation`, `ai-agent`, `claude-code-skill`
- [ ] 设置 branch protection（main 分支）

### 文档层面
- [x] README.md — 安装、使用、免责
- [x] SKILL.md — Skill 行为定义
- [x] config.example.yaml — 脱敏配置模板
- [x] resume.example.md — 简历模板
- [ ] CONTRIBUTING.md — 贡献指南（可选 Phase 2）
- [ ] CHANGELOG.md — 版本记录（可选 Phase 2）

---

## 重命名映射表

| 原名 | 新名 | 位置 |
|------|------|------|
| bosshunter | bosshunter | Python 包名 |
| BossHunter | BossHunter | 项目显示名 |
| bosshunter.db | bosshunter.db | 数据库文件名 |
| `bosshunter` CLI | `bosshunter` CLI | 命令行入口 |
| src/bosshunter/ | src/bosshunter/ | 源码目录 |

---

## 发布时间线建议

1. **现在**：准备所有文档和结构（已完成）
2. **求职完成后**：执行代码重命名 + 清理
3. **发布前一天**：最终安全审查（上方 checklist）
4. **发布**：新 repo → 首次 commit → 设为 Public
5. **发布后**：写一篇介绍文章（可选）
