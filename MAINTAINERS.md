# BossHunter 维护者记录

本文件从 **2026-08-28** 起正式记录 BossHunter 的现任和历任维护者。

维护者身份表示对指定范围承担持续的人类维护责任。AI 工具可以协助执行工作，但不列入维护者名单。

治理与晋升规则见 [GOVERNANCE.md](GOVERNANCE.md)。社区贡献影响力榜与维护者身份相互独立。

## 现任维护者

| GitHub | 角色 | 负责范围 | 任期 | 状态 |
|---|---|---|---|---|
| [@powerycy](https://github.com/powerycy) 跑跑蹦蹦跳跳 | 项目负责人 | 全项目；核心与安全最终审批；候选维护者招募 | 项目发起至今；2026-08-28 起正式建档 | 在任（Admin） |
| [@yuppiez99999](https://github.com/yuppiez99999) | 平台适配维护者 | 招聘平台采集器、城市数据、适配测试与平台域 PR 治理 | 2026-08-29 起 | 在任（Write） |
| [@yukinoshi](https://github.com/yukinoshi) | 产品与 AI 维护者 | AI 评分、招呼语、错误恢复与产品域 PR 治理；高风险路径转核心与安全复核 | 2026-08-29 起 | 在任（Write） |
| [@fengziliang43-cmyk](https://github.com/fengziliang43-cmyk) | 核心与安全维护者 | 发送安全、运行时、数据库、监测链路与核心域 PR 治理；高风险修改与项目负责人共同复核 | 2026-08-30 起 | 在任（Write） |
| [@bianshilong0604](https://github.com/bianshilong0604) | 产品与 AI 维护者 | Web 工作台、AI 评分、招呼语与产品域 PR 治理；高风险路径转核心与安全复核 | 2026-08-30 起 | 在任（Write） |

## 首轮维护配置

以下是首轮责任域配置目标。候选人确认参与后，通过治理 PR 记录观察期；完成观察期并正式晋升后，才加入“现任维护者”。

| 维护域 | 首轮目标人数 | 当前正式维护者 | 当前候选 | 下一步 |
|---|:---:|:---:|:---:|---|
| 核心与安全 | 3 | 2 | 0 | 继续招募 1 名稳定候选或备份；高风险修改由双人复核，且保留项目负责人审批 |
| 产品与 AI | 2 | 2 | 0 | 首轮目标已达到；保持交叉复核和持续响应 |
| 平台适配 | 2 | 1 | 0 | 继续招募第 2 名候选，形成交叉复核 |

## 候选维护者（观察期）

候选人确认参与后，在这里记录观察期；尚未获得正式维护者身份或 `Write` 权限。

| GitHub | 维护域 | 观察期开始 | 推荐/带教人 | 状态 |
|---|---|---|---|---|
| — | — | — | — | 当前暂无观察期候选 |

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
| 2026-08-28 起 | [@powerycy](https://github.com/powerycy) | 全项目；核心与安全最终审批 | 首个统计周期进行中；完成高风险路径保护，将多个落后 PR 的有效内容按原作者署名安全整合到最新主线，并完成 #86、#88 的最终验证与合入 | [PR #120](https://github.com/shengjidaguai-china/BossHunter/pull/120)、[#123](https://github.com/shengjidaguai-china/BossHunter/pull/123)–[#127](https://github.com/shengjidaguai-china/BossHunter/pull/127)、[#86](https://github.com/shengjidaguai-china/BossHunter/pull/86)、[#88](https://github.com/shengjidaguai-china/BossHunter/pull/88) | 进行中 |
| 2026-08-29 起 | [@yuppiez99999](https://github.com/yuppiez99999) | 平台适配 | 完成 BOSS、51job、猎聘采集回归与运行边界交付；持续进行平台域 Review、风险识别和 Issue 治理 | [申请 #113](https://github.com/shengjidaguai-china/BossHunter/issues/113)、[PR #122](https://github.com/shengjidaguai-china/BossHunter/pull/122)、[#95/#97/#98/#99 → #126](https://github.com/shengjidaguai-china/BossHunter/pull/126)、[#121 → #127](https://github.com/shengjidaguai-china/BossHunter/pull/127)、[PR #81 Review](https://github.com/shengjidaguai-china/BossHunter/pull/81#issuecomment-5459820689)、[PR #89 Review](https://github.com/shengjidaguai-china/BossHunter/pull/89#issuecomment-5459791861)、[PR #104 Review](https://github.com/shengjidaguai-china/BossHunter/pull/104#issuecomment-5459833276)、[Issue #78 Triage](https://github.com/shengjidaguai-china/BossHunter/issues/78#issuecomment-5459841957) | 进行中 |
| 2026-08-29 起 | [@yukinoshi](https://github.com/yukinoshi) | 产品与 AI | 修复评分 JSON 兼容、错误传播、暂停恢复和 AI 凭据优先级，为高风险改动补充回归验证，并参与 #86 产品域维护协作 | [PR #114](https://github.com/shengjidaguai-china/BossHunter/pull/114)、[#115](https://github.com/shengjidaguai-china/BossHunter/pull/115)、[#117](https://github.com/shengjidaguai-china/BossHunter/pull/117)、[#118](https://github.com/shengjidaguai-china/BossHunter/pull/118)、[#86](https://github.com/shengjidaguai-china/BossHunter/pull/86) | 进行中 |
| 2026-08-28 起；2026-08-30 晋升 | [@fengziliang43-cmyk](https://github.com/fengziliang43-cmyk) | 核心与安全 | 完成监测回复轮次、幂等确认和安全会话跳转交付，补充 Issue 风险研判；OCR 修复仍在继续处理 | [PR #119 → #124](https://github.com/shengjidaguai-china/BossHunter/pull/124)、[Issue #93](https://github.com/shengjidaguai-china/BossHunter/issues/93)、[PR #67](https://github.com/shengjidaguai-china/BossHunter/pull/67) | 进行中 |
| 2026-08-28 起；2026-08-30 晋升 | [@bianshilong0604](https://github.com/bianshilong0604) | 产品与 AI | 推进可解释评分与复盘方向；在 #88、#90 审核中识别隐私、网址边界和重复发送风险，并跟进修复验证 | [PR #77](https://github.com/shengjidaguai-china/BossHunter/pull/77)、[#88 Review](https://github.com/shengjidaguai-china/BossHunter/pull/88#issuecomment-5463732307)、[#88 复核](https://github.com/shengjidaguai-china/BossHunter/pull/88#issuecomment-5466342761)、[#90 Review](https://github.com/shengjidaguai-china/BossHunter/pull/90#issuecomment-5463882496) | 进行中 |
| 2026-08-31 | [@yuppiez99999](https://github.com/yuppiez99999) | 平台适配 | 补齐岗位筛选、采集注册、采集模型和平台能力边界回归，将相关核心模块测试覆盖提升至 100% | [PR #130](https://github.com/shengjidaguai-china/BossHunter/pull/130)、[#131](https://github.com/shengjidaguai-china/BossHunter/pull/131)、[#132](https://github.com/shengjidaguai-china/BossHunter/pull/132) | 已核实（8 月补充） |
| 2026-08-31 | [@fengziliang43-cmyk](https://github.com/fengziliang43-cmyk) | 核心与安全 | 完成监测会话同步、单条待处理消息的安全读取与建议生成、会话精确跳转和本地 AI 凭据隔离，并补齐安全回归 | [PR #134](https://github.com/shengjidaguai-china/BossHunter/pull/134) | 已核实（8 月补充） |
| 2026-09-01 起 | [@yuppiez99999](https://github.com/yuppiez99999) | 平台适配 | 对 51job API 采集整合提交实质 Review 并批准，核对只读边界、fail-closed 风控、速率控制和断点续采，并要求真实环境冒烟验证 | [PR #142 Review](https://github.com/shengjidaguai-china/BossHunter/pull/142#pullrequestreview-5074202801) | 进行中 |

记录摘要应覆盖适用的维度：PR 审核、模块交付、Issue 治理、安全与质量、社区协作。没有发生的维度保持空缺，不以机械数量补齐。

## 记录规则

- 新增、晋升、暂停、恢复和离任均通过 Pull Request 修改本文件。
- 任期日期使用 `YYYY-MM-DD`；不确定的历史日期不得猜测。
- 候选人未确认参与前，不公开列入名单；公开记录只使用 GitHub ID，不登记真实姓名。
- 离任时移动记录并填写结束日期，不直接删除。
- 同一维护者再次加入时，保留旧任期并新增任期。
- 权限变化应同步更新 GitHub Teams 和 `.github/CODEOWNERS`。
- 维护贡献摘要只追加经核实的周期记录，不覆盖旧周期。
- 当事人可以提交证据，但不能批准或合并涉及自己身份、任期或贡献摘要的修改。
