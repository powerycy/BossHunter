# BossHunter 维护者记录

本文件从 **2026-08-28** 起正式记录 BossHunter 的现任和历任维护者。

维护者身份表示对全项目承担持续的人类维护责任。AI 工具可以协助执行工作，但不列入维护者名单。

治理与晋升规则见 [GOVERNANCE.md](GOVERNANCE.md)。社区贡献影响力榜与维护者身份相互独立。

## 现任维护者

| GitHub | 身份 | 贡献占比 | 擅长方向 | 任期 | 状态 |
|---|---|---|---|---|---|
| [@yuppiez99999](https://github.com/yuppiez99999) | 正式维护者 | 33.9%（试算） | 平台采集、城市数据与测试 | 2026-08-29 起 | 在任（Write） |
| [@yukinoshi](https://github.com/yukinoshi) | 正式维护者 | 28.8%（试算） | AI、错误恢复与产品流程 | 2026-08-29 起 | 在任（Write） |
| [@bianshilong0604](https://github.com/bianshilong0604) | 正式维护者 | 26.3%（试算） | Web、产品流程与隐私边界 | 2026-08-30 起 | 在任（Write） |
| [@fengziliang43-cmyk](https://github.com/fengziliang43-cmyk) | 正式维护者 | 11.0%（试算） | 运行时、发送安全与监测链路 | 2026-08-30 起 | 在任（Write） |
| [@powerycy](https://github.com/powerycy) 跑跑蹦蹦跳跳 | 项目负责人 | 不参评 | 项目治理与发布协调 | 项目发起至今；2026-08-28 起正式建档 | 在任（Admin） |

以上为截至 **2026-09-03**、尚未经双人复核的基线试算。维护者任职满 30 天后，按任期开始至核算日的全部维护成果计算正式贡献占比；不是只计算最近 30 天，也不会每 30 天重置。

## 维护方式

4 名正式维护者拥有相同的全仓维护范围，不设置固定模块或席位。表中的擅长方向只用于协作参考；任何正式维护者都可以认领、审核和合并其他方向的工作。

- 普通 PR：至少一名非作者正式维护者批准并通过 CI。
- 高风险 PR：至少两名不同的非作者正式维护者批准，其中一人明确完成安全检查。
- 项目负责人不是作者时可以作为高风险复核人之一，但不是必需或最终复核人。

## 候选维护者（观察期）

候选人确认参与后，在这里记录观察期；尚未获得正式维护者身份或 `Write` 权限。

| GitHub | 擅长方向 | 观察期开始 | 推荐/带教人 | 状态 |
|---|---|---|---|---|
| — | — | — | — | 当前暂无观察期候选 |

## 现任维护者贡献详情

以下为截至 **2026-09-03** 已核实的维护活动摘要，仅记录对他人 PR 的 Review、Issue 治理、安全复核和交付协作，不计入本人提交的功能、修复、测试或文档。

| 维护者 | 贡献占比 | 维护贡献摘要 | 代表证据 |
|---|---:|---|---|
| [@yuppiez99999](https://github.com/yuppiez99999) | **33.9%（试算）** | 对 51job API 采集完成只读边界、失败关闭、速率控制、断点续采和真实环境验证检查；持续梳理积压 PR 的风险、冲突与合并阻塞项，并主动让原贡献者方案承接合并。 | [#142 安全复核](https://github.com/shengjidaguai-china/BossHunter/pull/142#pullrequestreview-5074202801) · [#139 Review](https://github.com/shengjidaguai-china/BossHunter/pull/139#pullrequestreview-5084448426) · [#89 协作复核](https://github.com/shengjidaguai-china/BossHunter/pull/89#pullrequestreview-5056851017) |
| [@yukinoshi](https://github.com/yukinoshi) | **28.8%（试算）** | 对简历失败恢复 PR 完成全量测试、前端类型检查和安全红线验证并批准；为评分解释和招呼语队列改动处理冲突、补充合并方案与完整验证。 | [#92 Review](https://github.com/shengjidaguai-china/BossHunter/pull/92#pullrequestreview-5089153466) · [#77 合并分析](https://github.com/shengjidaguai-china/BossHunter/pull/77#issuecomment-5466530159) · [#86 合并验证](https://github.com/shengjidaguai-china/BossHunter/pull/86#issuecomment-5466756964) |
| [@bianshilong0604](https://github.com/bianshilong0604) | **26.3%（试算）** | 在招呼语网址防护中识别简历隐私、可信网址来源和重复发送边界问题，并在修改后完成复核；同时审查招呼语生成流程的岗位状态与任务并发风险。 | [#88 首轮 Review](https://github.com/shengjidaguai-china/BossHunter/pull/88#issuecomment-5463732307) · [#88 复核](https://github.com/shengjidaguai-china/BossHunter/pull/88#issuecomment-5466342761) · [#90 Review](https://github.com/shengjidaguai-china/BossHunter/pull/90#issuecomment-5463882496) |
| [@fengziliang43-cmyk](https://github.com/fengziliang43-cmyk) | **11.0%（试算）** | 对安全锁是否应随账号切换重置完成风险边界研判，并对一键投递失效问题提出分层排查与复现信息要求；相关 Issue 尚未闭环。 | [#78 风险研判](https://github.com/shengjidaguai-china/BossHunter/issues/78#issuecomment-5452233325) · [#93 问题排查](https://github.com/shengjidaguai-china/BossHunter/issues/93#issuecomment-5452071152) |

## 历任维护者

目前没有从本制度下离任的维护者。

离任后保留以下信息，不删除历史：

| GitHub | 曾任角色 | 负责范围 | 任期 | 维护摘要 |
|---|---|---|---|---|
| — | — | — | — | — |

## 记录规则

- 新增、晋升、暂停、恢复和离任均通过 Pull Request 修改本文件。
- 任期日期使用 `YYYY-MM-DD`；不确定的历史日期不得猜测。
- 候选人未确认参与前，不公开列入名单；公开记录只使用 GitHub ID，不登记真实姓名。
- 离任时移动记录，将开始和结束日期合并填写为完整任期，并补充维护摘要，不直接删除。
- 同一维护者再次加入时，保留旧任期并新增任期。
- 权限变化应同步更新 GitHub Teams 和 `.github/CODEOWNERS`。
- 现任维护者贡献详情记录贡献占比、可核实的维护成果与代表证据；任职未满 30 天的占比必须标记为“试算”，任职满 30 天后按整个任期的累计证据更新贡献占比，历史依据不覆盖。
- 当事人可以提交证据，但不能批准或合并涉及自己身份、任期或贡献摘要的修改。
