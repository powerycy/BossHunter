# 报名事实表

更新时间：2026-08-06（Asia/Shanghai）

## 比赛事实

| 项目 | 已核事实 | 来源 |
|---|---|---|
| 赛事 | 2026 外滩黑客松 AI Coding 大赛 | [官方统一入口](https://hackathon2026.app.weavefox.cn/) |
| 线上阶段 | 2026-07-20 至 2026-08-09 | [赛事报道](https://www.jiemian.com/article/14811118.html) |
| 提交方式 | 通过大赛统一入口提交可体验作品 | [赛事报道](https://www.jiemian.com/article/14811118.html) |
| 后续阶段 | 专业评审与小红书人气评选 | [赛事信息页](https://www.competehub.dev/zh/competitions/urls7a0490315db3a1cceaca6a702da72c50) |
| 已选通道 | 官方统一入口自助报名 | 无需冒用任何合作平台创作经历；以表单实际字段为准 |

## 项目事实

| 字段 | 可填写内容 | 证据 |
|---|---|---|
| 项目名 | BossHunter——把岗位筛选、沟通和跟进交给 AI，把投递决定留给人 | 本目录统一命名 |
| 仓库 | `powerycy/BossHunter` | Git remote 与 GitHub 仓库 |
| 当前版本 | v2.2.0 | `pyproject.toml`、`src/bosshunter/__init__.py` |
| 产品形态 | Python 求职工作流 + React Web Dashboard + 本地 Browser Runtime | 源代码 |
| 公开体验 | <https://powerycy.github.io/BossHunter/> | GitHub Pages 比赛沙盒 |
| 源代码 | <https://github.com/powerycy/BossHunter> | 公开仓库 |
| 许可证 | BossHunter Non-Commercial License | `LICENSE` |
| Python 要求 | Python 3.10+ | `pyproject.toml` |
| Node 要求 | Node.js 22+ | `README.md` |
| 核心数据库 | SQLite | `src/bosshunter/db.py` |
| AI 接口 | Anthropic 与 OpenAI 兼容协议；可配置 Claude、DeepSeek、豆包或自定义服务 | `src/bosshunter/config.py`、Web 配置页 |
| 真实模式边界 | 本地运行；连接用户已登录的 Chrome；发送前人工确认；低频、时间窗与每日上限 | `README.md`、`CLAUDE.md`、执行器代码 |
| 评委模式边界 | 6 条模拟 JD、匿名简历、固定可复现演示结果；不抓取、不登录、不真实发送 | `JudgeDemoPage.tsx` |

## 可验证闭环

真实产品源代码覆盖：

1. 岗位采集与去重。
2. 硬条件预筛与 AI 深度评分。
3. 个性化招呼语生成。
4. 人工确认投递。
5. 低频发送与任务停止。
6. HR 回复监测、建议回复和简历请求识别。
7. 基于原始经历生成定制简历并等待人工发送。
8. Web 漏斗、岗位池、任务状态与错误恢复。

公网沙盒覆盖其中最适合评审的安全闭环：预筛 → 深度评分 → 排序 → 招呼语 → 人工确认 → 状态漏斗 → 简历请求 → 定制简历差异。

## 不能填写或需要本人确认的字段

- 真实姓名、手机号、邮箱、身份证号、学校、公司、职务。
- 团队人数和团队成员信息（仓库不能证明参赛团队组成）。
- 小红书公开笔记链接（必须发布后回填）。
- “使用哪个合作平台创作”的选择（必须以表单选项和用户真实使用情况为准）。
- 用户数、面试数、投递数、节省时间、用户评价、获奖经历。

## AI 辅助说明草稿

本次比赛打磨过程中使用 Codex 辅助完成代码审计、评委沙盒实现、构建与浏览器回归、GitHub Pages 部署和报名文档整理。产品问题定义、功能边界、是否采用修改以及最终提交由人确认。既有仓库可能包含更早阶段的其他开发过程，除非本人补充，不推断或代填。
