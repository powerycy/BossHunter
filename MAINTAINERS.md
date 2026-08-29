# BossHunter 维护者记录

本文件从 **2026-08-28** 起正式记录 BossHunter 的现任和历任维护者。

维护者身份表示对指定范围承担持续的人类维护责任。AI 工具可以协助执行工作，但不列入维护者名单。

治理与晋升规则见 [GOVERNANCE.md](GOVERNANCE.md)。社区贡献影响力榜与维护者身份相互独立。

## 现任维护者

| GitHub | 角色 | 负责范围 | 任期 | 状态 |
|---|---|---|---|---|
| [@powerycy](https://github.com/powerycy) | 项目负责人 | 全项目；核心与安全最终审批；候选维护者招募 | 项目发起至今；2026-08-28 起正式建档 | Active |
| [@yuppiez99999](https://github.com/yuppiez99999) | 平台适配维护者 | 招聘平台采集器、城市数据、适配测试与平台域 PR 治理 | 2026-08-29 起 | Active（Write） |

## 首轮招募名额

以下是候选名额，不代表已经授予权限。候选人确认参与后，通过治理 PR 记录观察期；完成观察期并正式晋升后，才加入“现任维护者”。

| 维护域 | 候选名额 | 当前正式维护者 | 临时安排 |
|---|:---:|---:|---|
| 核心与安全 | 3 | 1 | 项目负责人负责最终审批 |
| 产品与 AI | 2 | 0 | 项目负责人代管 |
| 平台适配 | 2 | 1 | 继续招募第 2 名维护者 |

## 候选维护者（观察期）

候选人确认参与后，在这里记录观察期；尚未获得正式维护者身份或 `Write` 权限。

| GitHub | 维护域 | 观察期开始 | 推荐/带教人 | 状态 |
|---|---|---|---|---|
| [@yukinoshi](https://github.com/yukinoshi) | 核心与安全 | 2026-08-28 | [@powerycy](https://github.com/powerycy) | 观察中（Triage） |

## 历任维护者

目前没有从本制度下离任的维护者。

离任后保留以下信息，不删除历史：

| GitHub | 曾任角色 | 负责范围 | 开始日期 | 结束日期 | 状态/说明 |
|---|---|---|---|---|---|
| — | — | — | — | — | — |

## 维护贡献记录

这里从 **2026-08-28** 起按周期追加维护与治理摘要。统计维度和权重见 [GOVERNANCE.md 的“维护贡献统计”](GOVERNANCE.md#维护贡献统计)；GitHub 上的 PR、Review、Issue 和 Release 是原始证据，本表不替代原始记录。

首个正式记录周期从 **2026-08-28** 开始。候选观察期每两周汇总，正式维护者每月更新活动摘要、每季度形成任期评估；尚未结束的周期不提前填写结论。

| 周期 | 维护者 | 负责范围 | 维护与治理摘要 | 证据 | 周期结论 |
|---|---|---|---|---|---|
| 2026-08-28 起 | [@powerycy](https://github.com/powerycy) | 全项目；核心与安全最终审批 | 首个统计周期进行中 | 待周期结束后由治理 PR 汇总 | 进行中 |
| 2026-08-29 起 | [@yuppiez99999](https://github.com/yuppiez99999) | 平台适配 | 正式维护首周期进行中；晋升依据包括平台实现、适配测试、跨域风险识别、利益冲突披露与 Issue 治理 | [申请 #113](https://github.com/shengjidaguai-china/BossHunter/issues/113)、[PR #111](https://github.com/shengjidaguai-china/BossHunter/pull/111)、[PR #81 Review](https://github.com/shengjidaguai-china/BossHunter/pull/81#issuecomment-5459820689)、[PR #89 Review](https://github.com/shengjidaguai-china/BossHunter/pull/89#issuecomment-5459791861)、[PR #104 Review](https://github.com/shengjidaguai-china/BossHunter/pull/104#issuecomment-5459833276)、[Issue #78 Triage](https://github.com/shengjidaguai-china/BossHunter/issues/78#issuecomment-5459841957) | 进行中 |

记录摘要应覆盖适用的维度：PR 审核、模块交付、Issue 治理、安全与质量、社区协作。没有发生的维度保持空缺，不以机械数量补齐。

## 记录规则

- 新增、晋升、暂停、恢复和离任均通过 Pull Request 修改本文件。
- 任期日期使用 `YYYY-MM-DD`；不确定的历史日期不得猜测。
- 候选人未确认参与前，不公开记录姓名。
- 离任时移动记录并填写结束日期，不直接删除。
- 同一维护者再次加入时，保留旧任期并新增任期。
- 权限变化应同步更新 GitHub Teams 和 `.github/CODEOWNERS`。
- 维护贡献摘要只追加经核实的周期记录，不覆盖旧周期。
- 当事人可以提交证据，但不能批准或合并涉及自己身份、任期或贡献摘要的修改。
