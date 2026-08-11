# BossHunter 本地增强改造实施文档

> 适用项目：`D:\简历\BossHunter`
> 使用范围：个人本地使用
> 目标执行者：Luna 或其他接手该仓库的编码 Agent
> 文档性质：实施规格，不是已经完成的改动说明

## 1. 改造目标

本轮只解决以下四类需求，并同步修复与这些需求直接相关的稳定性问题：

1. 增加独立 AI 评分入口，不重新采集岗位也能继续评分。
2. 岗位池支持导出 XLSX 和 UTF-8 CSV，并保留岗位链接。
3. 城市选择扩展为全国城市，且不能因 BOSS 城市接口不可用而导致项目不可用。
4. 增加 LuluCoding 服务商和更可靠的 AI 连接诊断，默认诊断不产生模型推理费用。

除上述范围外，不要重构岗位监控、投递、招呼语、HR 回复和简历生成流程。

## 2. 安全与范围边界

### 2.1 必须遵守

- 当前仓库许可证是 `LicenseRef-BossHunter-NonCommercial`，本轮仅用于个人本地使用。
- 不读取、输出、复制或提交 `config.yaml` 中的真实 API Key。
- 不提交 `data/`、`*.db`、简历、导出文件、缓存中的个人数据。
- 必须保留现有 `data/bosshunter.db`，不能清库、重建或用测试数据覆盖。
- 数据库结构变更必须采用向前兼容迁移，并先在临时数据库测试。
- 不更改监控、投递、招呼语、HR 回复、简历生成的业务语义。
- 不把高级 AI 实际测试做成自动动作；它必须由用户主动点击并再次确认。
- 所有错误信息必须脱敏，不返回请求头中的 Authorization，也不返回 API Key、完整请求体或完整简历。

### 2.2 建议的 Git 操作

开始编码前：

```powershell
cd 'D:\简历\BossHunter'
git status --short
git switch -c codex/bosshunter-local-improvements
```

如果工作树不再干净，先识别已有改动归属，不要覆盖或回滚用户改动。

## 3. 当前代码事实

以下事实已从当前 `main` 分支核对，Luna 修改前仍应再快速确认一次。

| 现状 | 位置 | 影响 |
|---|---|---|
| 后端是 Bottle，不是 FastAPI | `src/bosshunter/web/server.py` | 新接口应继续使用 Bottle 路由和现有 `_json_response` |
| 前端是 React 18 + TypeScript + Vite | `src/bosshunter/web/frontend/` | 不要按 Vue 项目处理 |
| 后端已有 `rescore` 执行器 | `server.py::_execute_rescore`、`web/tasks.py` | 现有能力只重置部分 AI 低分岗位，不能直接满足新的评分范围 |
| 前端工作台只展示 full、collect、monitor 三张卡片 | `DashboardPage.tsx` 中的 `modes` | 需要新增独立 AI 评分入口 |
| `/api/jobs` 默认只返回 100 条 | `server.py::api_jobs` | 前端也固定请求 `?limit=100`，因此只显示 100 条 |
| `scoring.max_candidates` 已配置但评分器未使用 | `config.py`、`config_schema.json`、`ai/scorer.py` | 默认 20 个目前没有真正生效 |
| 城市在后端和前端分别写死 20 个 | `config.py::CITY_CODES`、`ConfigPage.tsx::CITIES` | 两处容易漂移，无法选择全国城市 |
| 岗位表已有 `url` 字段 | `db.py` | 导出岗位链接无需重新抓取 |
| 抓取器已生成完整 BOSS 链接 | `scraper/jobs.py` 中的 `detail_url` | XLSX 可直接生成可点击链接 |
| 岗位表只有城市名称，没有 `city_code` | `db.py` | 新岗位应持久化城市编码，旧数据可按城市名回填或导出时解析 |
| AI 基础检测只访问 `/models` | `web/preflight.py::check_ai_connection` | 模型列表成功不代表评分请求可用，也未严格检查 JSON/HTML |
| OpenAI 兼容 `auto` 当前先发送 `thinking: disabled` | `ai/credentials.py::_openai_thinking_strategies` | 某些中转会超时或不兼容；LuluCoding 默认应完全不发送 Thinking 参数 |
| AI 返回内容直接 `response.json()` | `ai/credentials.py::call_openai_compatible_text` | HTML、空响应、非 JSON 可能逃逸为未分类异常 |
| 评分全局错误会设置 `pause_reason` 后退出 | `ai/scorer.py` | `score_jobs` 返回后任务运行器仍可能显示为普通完成 |
| 单条评分格式失败已有部分继续机制 | `ai/scorer.py::_record_score_failure` | 应扩展成结构化错误并确保不阻塞后续岗位 |

## 4. 总体实施顺序

按以下顺序修改，避免前端先依赖不存在的接口：

1. 建立城市数据服务和静态快照。
2. 增加 LuluCoding 服务预设、Base URL 规范化和 AI 错误分类。
3. 改造评分选择、数量限制、任务结果状态和失败继续策略。
4. 增加岗位查询完整加载、筛选和导出接口。
5. 修改前端工作台、岗位池、城市选择器和 AI 诊断弹窗。
6. 增加测试、构建前端、检查差异和敏感信息。

## 5. 全国城市改造

### 5.1 核心原则

城市功能不能在程序启动时依赖 BOSS 在线接口。默认使用项目内置的静态快照；只有用户主动点击“刷新城市列表”时才访问在线接口。

当前公开数据源：

- `https://www.zhipin.com/wapi/zpCommon/data/cityGroup.json`
- `https://www.zhipin.com/wapi/zpCommon/data/city.json`

2026-08-10 实测 `cityGroup.json` 返回 22 个字母分组、373 个唯一城市编码。该数量只是当前快照，不应写成永久不变的业务常量。

### 5.2 建议新增文件

- `src/bosshunter/cities.py`
- `src/bosshunter/data/boss_cities.json`
- `tests/test_city_service.py`

静态快照建议结构：

```json
{
  "schema": "bosshunter.cities.v1",
  "source_url": "https://www.zhipin.com/wapi/zpCommon/data/cityGroup.json",
  "fetched_at": "2026-08-10T00:00:00+08:00",
  "cities": [
    {
      "name": "北京",
      "code": "101010100",
      "first_char": "B",
      "hot": true
    }
  ]
}
```

注意：编码统一保存为字符串，避免前后端或表格软件改变格式。

### 5.3 运行时加载顺序

1. 如果存在用户主动刷新生成的 `data/cities.cache.json`，并且验证通过，则读取缓存。
2. 否则读取随包发布的 `src/bosshunter/data/boss_cities.json`。
3. 如果缓存损坏或在线刷新失败，继续使用内置快照，不得让配置页或抓取任务崩溃。

### 5.4 刷新校验

`cities.py` 应提供纯函数和 I/O 函数，例如：

- `load_cities()`
- `get_city_code(name)`
- `get_city_map()`
- `refresh_city_cache(cache_path)`
- `validate_city_payload(payload)`

在线返回必须满足以下条件后才能写缓存：

- HTTP 200。
- Content-Type 或响应体确认为 JSON，不能是 HTML。
- 顶层 `code == 0`。
- `zpData.cityGroup` 是列表。
- 展平后至少 300 个城市，名称非空、编码为 9 位左右的数字字符串。
- 城市编码唯一。
- 必须包含北京、上海、广州、深圳，且编码与已知值一致。

写缓存必须使用临时文件加原子替换；校验失败时不得覆盖旧缓存。

### 5.5 后端接口

保留旧的 `GET /api/config/cities`，但其数据改为由城市服务生成，避免破坏潜在旧客户端。建议新增：

#### `GET /api/cities`

响应示例：

```json
{
  "ok": true,
  "source": "bundled",
  "count": 373,
  "updated_at": "2026-08-10T00:00:00+08:00",
  "cities": [
    {"name": "北京", "code": "101010100", "first_char": "B", "hot": true}
  ]
}
```

#### `POST /api/cities/refresh`

- 只在用户点击刷新后调用。
- 成功返回新数量和更新时间。
- 失败返回清晰错误，同时注明“仍在使用本地城市列表”。
- 不要在 GET 配置、保存配置或启动服务时隐式刷新。

### 5.6 抓取和数据库

建议把 `jobs.city_code TEXT` 加入下一版迁移：

1. 在 `_migrate_v1_2` 中检查列是否存在，再执行 `ALTER TABLE jobs ADD COLUMN city_code TEXT`。
2. `insert_job` 增加 `city_code`。
3. `scraper/jobs.py` 构造 `job_record` 时保存当前搜索组合中的 `city_code`。
4. 对旧数据不做破坏性强制迁移；读取或导出时，如果 `city_code` 为空，用 `get_city_code(job.city)` 补充。

配置文件中仍保存城市名称，保持现有配置兼容；抓取前再通过城市服务解析编码。

## 6. LuluCoding 和 AI 诊断

### 6.1 服务预设

在后端 `config.py::AI_SERVICE_PRESETS` 和前端 `ConfigPage.tsx::AI_SERVICES` 增加：

```text
service: lulucoding
label: LuluCoding
provider: openai_compatible
site root: https://api.lulucoding.com
canonical API base: https://api.lulucoding.com/v1
key env: LULUCODING_API_KEY
```

同步更新：

- `SUPPORTED_AI_SERVICES`
- `_validate_ai_provider` 的错误提示
- `get_ai_api_key`
- `get_ai_key_source`
- `get_ai_base_url`
- `config_schema.json`
- `config.example.yaml`（只写占位信息，不能写真实 Key）

LuluCoding 可以兼容 `OPENAI_API_KEY` / `OPENAI_BASE_URL` 作为后备，但优先使用 LuluCoding 专用环境变量和本地配置。

### 6.2 Base URL 规范化

新增可单元测试的纯函数，例如 `normalize_openai_base_url(url, service)`：

- 去除首尾空格和尾部 `/`。
- 拒绝非 HTTP/HTTPS、包含用户名密码、明显无效的 URL。
- 如果 LuluCoding 输入 `https://api.lulucoding.com`，规范化为 `https://api.lulucoding.com/v1`。
- 如果已输入 `/v1`，保持不重复追加。
- 如果误填 `/models` 或 `/chat/completions`，应剥离具体端点后得到 API Base。
- 不要对所有自定义中转盲目追加 `/v1`；通用服务通过候选探测判断。

保存 LuluCoding 配置时写入规范化后的 Base URL。保存过程不需要发送模型推理请求。

### 6.3 免费基础检测

默认“测试连接”只做不产生模型生成 Token 的检查：

1. 验证配置完整性。
2. 规范化 Base URL。
3. 请求 `/models`。
4. 检查 HTTP 状态。
5. 检查 Content-Type 和 JSON 结构，不能把官网 HTML 当成成功。
6. 提取并返回模型列表。
7. 判断当前配置的模型是否在列表内。
8. 返回 Key 来源名称，但绝不返回 Key 内容。

建议把 AI 诊断从 `web/preflight.py` 拆到 `ai/diagnostics.py`，`preflight.py` 只负责将结果转换成启动检查项。

建议响应：

```json
{
  "ok": true,
  "billable": false,
  "normalized_base_url": "https://api.lulucoding.com/v1",
  "key_source": "本地配置",
  "current_model": "example-model",
  "current_model_available": true,
  "models": [{"id": "example-model"}],
  "stages": [
    {"id": "url", "status": "pass", "message": "服务地址有效"},
    {"id": "models", "status": "pass", "message": "已读取模型列表"}
  ]
}
```

### 6.4 高级实际测试

新增 `POST /api/diagnostics/ai/advanced`：

- 请求体必须包含 `confirmed: true`，否则返回 400。
- 只能由用户点击“高级实际测试”并确认“会产生少量 Token”后调用。
- 单次请求，建议 `max_tokens: 32`，不要自动重复发送完整评分测试。
- 使用极短的虚拟输入，不携带用户简历和真实岗位数据。
- 请求 OpenAI Chat Completions 流式接口，记录：请求开始、响应头到达、首个内容块、完成时间。
- 检查能否返回精简评分 JSON，例如 `{"score":75,"reason":"ok","missing":""}`。
- 测试失败只返回脱敏后的状态、阶段和错误分类。
- 测试模型不会仅因列表中被点击就自动覆盖配置；用户点击“使用该模型”后才更新表单，随后走现有保存配置流程。

如果服务不支持流式返回，应明确显示“普通接口可能可用，但流式测试不兼容”，不要伪装成功。

### 6.5 Thinking 兼容策略

只调整 OpenAI 兼容路径，不破坏 Anthropic 原生策略：

- LuluCoding 默认 `thinking: off`。
- OpenAI 兼容的 `auto` 默认先不发送 `thinking` 字段。
- `off` 永远不发送该字段。
- 用户明确选择 `disabled` 时，可以先发送关闭参数；遇到 Thinking 参数不兼容、请求超时或服务端明确参数错误时，最多回退一次到完全不发送该字段。
- 回退必须写入任务日志，避免用户误以为第一种调用成功。
- 鉴权失败、余额不足、模型不存在不属于 Thinking 兼容问题，不能用回退掩盖。

### 6.6 AI 响应解析

`call_openai_compatible_text` 在调用 `response.json()` 前必须处理：

- 空响应体。
- Content-Type 是 HTML。
- JSON 解码失败。
- `choices` 缺失或为空。
- message/content 为空。
- HTTP 200 但返回错误对象。
- 流式接口返回不完整 SSE。

统一转换为 `AIRequestError`，建议错误种类包括：

- `auth`
- `model_not_found`
- `rate_limit`
- `token_quota`
- `timeout`
- `network`
- `invalid_content_type`
- `invalid_json`
- `empty_response`
- `invalid_response`
- `output_truncated`
- `context_limit`
- `thinking_incompatible`

## 7. 独立 AI 评分

### 7.1 不要复用现有 rescore 的错误语义

现有 `_execute_rescore` 会调用 `reset_ai_filtered_jobs`，只适合“把部分 AI 低分岗位重置后重新评分”。新功能应增加独立 `score` 模式，同时保留旧 `rescore` 入口作为兼容别名或旧行为。

需要同步修改：

- `web/tasks.py::MODE_LABELS`
- `web/preflight.py::VALID_MODES`
- `server.py::_preflight_messages`
- `server.py` 的任务执行器映射
- 前端 `WorkbenchTask` 和 `WorkbenchMode` 类型

### 7.2 评分范围

推荐后端使用统一的选择规格：

```json
{
  "scope": "pending",
  "limit": 20,
  "job_ids": [],
  "force_rescore": false
}
```

支持：

- `pending`：未评分及评分失败的岗位；跳过已有有效评分。
- `failed`：只重试评分失败岗位。
- `selected`：只处理岗位池勾选的 ID；只有 `force_rescore=true` 时才重评已有有效评分。
- `all_scored`：重新评分所有仍处于可重评阶段的有效评分岗位。

默认 `limit=20`，来源是 `scoring.max_candidates`。用户可以选择其他正整数或“全部”；“全部”建议在 API 中使用 `limit: null`，不要使用 0 表示全部。

### 7.3 可重评状态边界

允许重评：

- `pending`
- `scored`
- `ready`
- `filtered` 中确实经过 AI 评分的岗位

默认跳过：

- 纯关键词预筛淘汰岗位（除非未来单独提供重新预筛）
- `approved`
- `sent`
- `replied`
- `resume_sent`
- `needs_resume`
- `follow_up_sent`
- 已有招呼语或已经进入发送链路的岗位

这样可以避免重评破坏已经确认或投递的状态。被跳过的岗位必须计数并向用户说明原因。

### 7.4 评分预览与费用确认

新增 `POST /api/scoring/preview`，接受与正式评分相同的选择规格，但不写数据库、不调用 AI。

响应至少包含：

```json
{
  "eligible_jobs": 20,
  "skipped_jobs": 3,
  "first_attempt_requests": 20,
  "max_attempts_per_job": 2,
  "max_possible_requests": 40,
  "note": "关键词预筛可能减少实际 AI 请求数"
}
```

前端必须先展示该结果，再由用户确认后启动。重新评分全部岗位需要更醒目的二次确认。

### 7.5 正式任务请求

扩展 `POST /api/workbench/task`，使其可以接收任务参数，而不是只读取 `mode`：

```json
{
  "mode": "score",
  "options": {
    "scope": "pending",
    "limit": 20,
    "job_ids": [],
    "force_rescore": false
  }
}
```

必须验证：

- scope 在白名单中。
- limit 是合理正整数或 null。
- job_ids 是字符串数组、去重并限制最大数量。
- selected 模式必须提供 ID。
- all_scored 和 force_rescore 必须在前端确认；后端仍要验证作用范围。

不要把任意前端字段直接合并进全局配置。

### 7.6 评分器接口

将 `score_jobs` 扩展为接收明确选择参数，不要在函数内部永远读取全部 pending：

```python
score_jobs(
    config,
    *,
    scope="pending",
    limit=20,
    job_ids=None,
    force_rescore=False,
)
```

建议返回结构化结果，而不只返回 `(scored, filtered)`：

```text
selected
completed
passed
filtered
failed
skipped
remaining
outcome = completed | completed_with_errors | paused | stopped
pause_reason
```

如果要保持 CLI 兼容，可以提供结果对象的兼容解包，或同步更新 `main.py`、`pipeline.py` 和测试；不要让旧调用悄悄失效。

### 7.7 单条失败继续、全局错误暂停

以下情况属于单岗位失败，记录后继续下一个：

- 返回空内容。
- 返回 HTML 或非法 JSON。
- 缺少 score/reason。
- 单岗位内容超出上下文，压缩重试后仍失败。
- 单岗位输出截断，有限次重试后仍失败。
- 单岗位超时或网络异常，但尚未达到连续失败阈值。

以下情况应安全暂停整个评分任务，避免持续浪费请求：

- API Key 无效。
- 余额或额度不足。
- 当前模型不存在。
- 明确的全局限流。
- 连续 3 个岗位出现相同网络/超时错误。

评分失败岗位保持可重试。建议在 `history` 写结构化 JSON：

```json
{
  "schema": "bosshunter.score_failure.v1",
  "kind": "invalid_json",
  "stage": "parse_score",
  "message": "AI 返回内容不是可解析的评分 JSON",
  "status_code": null,
  "retryable": true
}
```

不要存储原始完整模型输出、请求体、API Key 或完整简历。

### 7.8 任务状态

当前任务运行器会在执行器正常返回后统一标记 `completed`。需要区分：

- `completed`：全部处理成功或正常跳过。
- `completed_with_errors`：处理结束，但存在单岗位失败。
- `paused`：因鉴权、额度、限流、连续网络错误等安全暂停。
- `stopped`：用户主动停止。
- `failed`：未预期程序异常。

更新 `TERMINAL_STATUSES`、活动任务判定、快照和前端状态文字。不要把安全暂停继续显示为“已结束”。

## 8. 岗位池完整加载与筛选

### 8.1 修复 100 条限制

保留 `/api/jobs` 返回数组以减少兼容风险，同时：

- 后端把 limit 限制在合理范围，例如 1–500。
- 计算筛选后的总数，通过响应头 `X-Total-Count` 返回。
- 前端 `useDashboard` 使用分页循环读取全部岗位，例如每页 200 条，直到读取数量达到总数或某页为空。
- 每轮刷新建立新的临时数组并按 ID 去重，避免重复。
- 如果某一页失败，不要用部分数据静默覆盖现有完整列表。

不要只把 `limit=100` 改成极大的数字，这会再次形成隐藏上限。

### 8.2 岗位池筛选与选择

在岗位池增加：

- 关键词搜索：公司、职位、JD。
- 状态筛选。
- 城市筛选。
- 最低分筛选（可选，若实现需简单明确）。
- 每行复选框。
- 当前筛选结果全选/取消全选。
- 显示“已选择 N 条”。

岗位池选择状态不要与工作台“确认投递”的选择状态混用。建议使用独立的 `jobPoolSelectedIds`。

## 9. 岗位导出

### 9.1 依赖

在 `pyproject.toml` 增加：

```text
openpyxl>=3.1
```

### 9.2 接口

新增 `POST /api/jobs/export`：

```json
{
  "format": "xlsx",
  "scope": "selected",
  "job_ids": ["id-1", "id-2"]
}
```

支持：

- `scope=all`
- `scope=filtered`，由前端传当前筛选结果的 ID
- `scope=selected`
- `format=xlsx|csv`

校验 ID 数量和格式，导出时按数据库查询，不能相信前端传来的岗位内容。

### 9.3 导出字段

至少包含：

1. 岗位 ID
2. 职位
3. 公司
4. 薪资
5. 城市
6. BOSS 城市编码
7. 工作经验
8. JD
9. HR 姓名
10. HR 职位
11. HR 活跃度
12. 公司规模
13. 公司行业
14. 岗位链接
15. 预筛分
16. AI 分数
17. 评分理由
18. 当前状态
19. 招呼语
20. 简历路径（只导出路径文本，不导出文件）
21. 创建时间
22. 更新时间
23. 最近评分失败原因（如有）

### 9.4 XLSX

- 使用 `openpyxl` 和内存流，不在项目目录生成临时导出文件。
- 第一行加粗、冻结首行、启用自动筛选。
- 岗位链接单元格设置 `hyperlink` 和 Hyperlink 样式。
- 对 JD、理由、招呼语设置换行和合理列宽，不要让列宽无限扩张。
- 响应 Content-Type 使用 XLSX 标准类型，并设置安全文件名。

### 9.5 CSV

- UTF-8 with BOM（`utf-8-sig`），提高 Windows Excel 中文兼容性。
- 使用 `csv` 标准库正确处理引号、逗号和换行。
- 防止公式注入：文本去除左侧空白后若以 `=`, `+`, `-`, `@`, 制表符或回车开头，则在原值前加单引号。
- URL 仍作为文本导出；CSV 是否自动变成链接由表格软件决定。

## 10. 前端改造

### 10.1 UI 一致性

继续使用现有组件和设计语言：

- `Button`、`Input`、`Select`、`Card`、`Badge`
- `lucide-react`
- 现有橙色 `primary`、圆角卡片、`#FFFCFA` 背景和 Tailwind 类

不要引入第二套 UI 框架。仓库上级说明提到 `lmpeccable`、`Motion AlKit`、`Taste Skill`、`Better Icons`、`UI Skills` 和 `DESIGN.md`；当前环境中这些资源未确认可用，Luna 若找不到，不要伪造或阻塞，按现有组件和视觉令牌做人工一致性检查。

### 10.2 独立评分入口

在工作台增加第四张卡片“单独 AI 评分”。点击后打开评分弹窗，而不是立即请求。

弹窗包含：

- 范围：未评分/失败、只重试失败、岗位池已选、重新评分全部有效评分岗位。
- 数量：默认 20，可输入其他数量或选择全部。
- 已选岗位数量。
- 预览结果：符合条件、跳过、首轮最多请求数、考虑重试后的最大请求数。
- 费用提醒。
- 明确确认按钮。

任务运行后显示通过、过滤、失败、剩余和当前岗位；失败项可在岗位池展开查看简化原因和脱敏详情。

### 10.3 岗位池工具栏

在 `JobsPoolView` 和 `JobsTable` 增加：

- 搜索/筛选控件。
- 复选框。
- 独立评分已选。
- 导出全部、当前筛选、已选。
- XLSX/CSV 格式选择。
- 岗位链接入口。

导出按钮应直接下载后端返回的 Blob，并从 `Content-Disposition` 获取文件名；失败时读取 JSON 错误并展示。

### 10.4 全国城市选择器

移除 `ConfigPage.tsx` 中的 `CITIES` 常量。新增可搜索多选组件，要求：

- 页面加载时只调用本地 `GET /api/cities`，该接口读取本地快照/缓存，不联网刷新。
- 搜索城市名或 BOSS 编码。
- 显示“城市名 · 编码”。
- 热门城市优先。
- 支持多选、移除和已选数量。
- “刷新城市列表”必须由用户主动点击。
- 刷新失败仍保留旧列表并显示“继续使用本地城市数据”。

配置中仍写城市名称数组，保持已有 `config.yaml` 兼容。

### 10.5 AI 诊断弹窗

替换当前仅显示一句结果的内嵌检测区域：

- 默认“基础检测”不产生生成 Token。
- 展示 URL、凭证、模型列表、当前模型是否存在等阶段。
- 模型列表可搜索、单选。
- 选择模型不会自动写配置。
- 点击“使用该模型”才更新模型字段。
- “高级实际测试”按钮旁明确注明“会产生少量 Token”。
- 点击后再次确认，再调用高级接口。
- 高级结果展示请求发出、响应头、首包、完成及耗时。
- 错误详情可展开，但必须是脱敏信息。

## 11. 后端建议拆分

避免继续把所有逻辑堆进 `server.py`，建议新增：

- `src/bosshunter/cities.py`：城市快照、缓存和刷新。
- `src/bosshunter/ai/diagnostics.py`：AI URL 探测、模型列表、可选高级测试。
- `src/bosshunter/job_export.py`：字段映射、CSV/XLSX 生成。
- `src/bosshunter/scoring_selection.py`：评分范围查询和预览。

`server.py` 只负责验证 HTTP 输入、调用服务、组装安全响应。

## 12. 测试要求

### 12.1 城市

- 内置快照至少 300 个唯一城市。
- 北京、上海、广州、深圳编码正确。
- 缓存损坏时回退内置快照。
- 在线返回 HTML、空 JSON、数量过少时不覆盖缓存。
- 刷新写入采用原子替换。
- 抓取器能解析新增城市。

### 12.2 AI

- LuluCoding 根地址规范化为 `/v1`。
- 已有 `/v1` 不重复追加。
- `/models` 返回 HTML 不算成功。
- 模型列表 JSON 正确解析。
- 基础检测不调用 chat completions。
- 高级接口没有 `confirmed=true` 时拒绝。
- 高级接口只发一次极短请求。
- OpenAI `auto` 不发送 Thinking。
- 明确 disabled 遇兼容错误或超时后最多回退一次。
- 空响应、HTML、非法 JSON、缺 choices 都变成明确 `AIRequestError`。
- 错误响应不包含测试 Key。

### 12.3 评分

- 默认只选 20 个 pending/failed。
- 自定义数量生效，null 表示全部。
- 已评分岗位在普通继续评分时被跳过。
- selected + force_rescore 只影响所选可重评岗位。
- all_scored 不触碰已投递状态。
- 一条非法 JSON 失败后继续下一个。
- 鉴权/余额错误立即暂停。
- 连续网络错误达到阈值后暂停。
- 用户停止显示 stopped。
- 单条失败结束显示 completed_with_errors。
- 安全暂停显示 paused，不显示 completed。

### 12.4 导出

- all、filtered、selected 范围正确。
- CSV 是 UTF-8 BOM。
- CSV 公式注入被转义。
- 含逗号、引号、换行的 JD 正确导出。
- XLSX 可被 openpyxl 重新打开。
- XLSX 岗位 URL 有 hyperlink。
- 旧岗位 city_code 为空时能按城市名补充。
- 导出不包含 API Key 或配置内容。

### 12.5 前端

在 `src/bosshunter/web/frontend` 执行：

```powershell
npm run build
```

后端执行：

```powershell
python -m unittest discover -s tests -p 'test_*.py'
```

如果使用项目虚拟环境：

```powershell
& '.\.venv\Scripts\python.exe' -m unittest discover -s tests -p 'test_*.py'
```

当前环境此前基线为 225 个测试，其中 1 个 Windows 临时静态文件句柄清理错误；Luna 必须重新运行并报告新的真实结果，不能照抄旧数字。

## 13. 手工验收流程

1. 使用临时数据库启动后端，确认配置页可显示全国城市。
2. 禁网或模拟城市接口失败，确认城市列表仍可用。
3. 确认基础 AI 检测只请求模型列表，没有 chat completions。
4. 用户主动确认后运行一次高级测试，确认阶段耗时和错误脱敏。
5. 在有 20 条以上待评分岗位的临时数据中，选择默认 20，确认只处理 20 条。
6. 模拟第一条返回非法 JSON、第二条成功，确认任务继续。
7. 模拟鉴权失败，确认任务显示 paused，未处理岗位保留。
8. 从岗位池导出全部、筛选和已选三种范围。
9. 用 Excel 或 openpyxl 打开 XLSX，点击岗位链接。
10. 检查现有 `data/bosshunter.db` 未被测试写入或覆盖。

## 14. 完成标准

- 不重新采集即可独立评分。
- 默认 20 个且数量可选，包括全部。
- 评分前显示请求规模并确认。
- 一条岗位失败不会卡住整个队列。
- 已评分岗位默认跳过，可按选中或全部重评。
- 岗位池显示全部数据，不再停在 100 条。
- XLSX/CSV 导出可用并包含岗位链接与城市编码。
- 全国城市默认完全依赖本地数据，手动刷新失败不影响使用。
- LuluCoding 根地址和 `/v1` 都能正确处理。
- 基础 AI 检测不产生生成 Token。
- 高级测试只能手动确认后运行。
- Thinking、HTML、空响应、非法 JSON 错误可被明确诊断。
- 现有数据库、API Key 和非目标业务流程没有被破坏。

## 15. 最终交付说明要求

Luna 完成后必须报告：

1. 修改了哪些文件及用途。
2. 四项需求分别如何实现。
3. 自动测试和前端构建的真实结果。
4. 是否执行过真实 AI 请求；如执行，必须说明是用户主动确认的高级测试，且不能展示 Key。
5. 是否触碰现有数据库；正常情况应回答“没有”。
6. 仍存在的已知限制或未完成项。
7. `git status --short` 的剩余改动范围，确认没有数据库、配置和导出文件。
