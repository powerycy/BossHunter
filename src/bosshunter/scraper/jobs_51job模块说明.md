# 51job 采集模块说明（jobs_51job.py）

> 最后更新：2026-08-22（含断点续采、source 字段、数据落盘说明、演进方向）
> 本模块是 BossHunter 的 51job（前程无忧）独立采集单元，**自包含**设计，可独立迁移。

---

## 一、模块定位

从 51job 搜索页（`we.51job.com/pc/search`）抓取岗位列表 + 详情页 JD 正文，入库到 BossHunter 的 `jobs` 表。

核心原则：**采集只负责抓数据，评分/投递/监测与之解耦**。

---

## 二、采集流程

```
1. 前置检查（时间窗口、随机休息日）
2. 构建搜索组合：城市 × 关键词
3. 断点续采过滤：跳过已完成的 (city, keyword) 组合
4. 打开第 1 页搜索 URL
5. 轮询等待列表渲染（SPA 二次渲染，不能只看 readyState）
6. WAF 检测（首屏滑块/验证墙）
7. 提取列表卡片（.joblist-item + sensorsdata JSON）
8. 逐个岗位：
   ├─ 去重（job_exists）
   ├─ 标题排除词 / 屏蔽公司
   ├─ 抓详情页 JD（分簇节奏 + 轮询等渲染）
   ├─ JD 排除词
   ├─ 薪资范围过滤（严格语义）
   └─ 入库（source='51job'）
9. 翻页（拟人化节奏）直到 max_pages 或末页
10. 关键词采完 → 写断点 checkpoint
```

---

## 三、配置条件适配清单

| 配置项 | 配置位置 | 落地方式 |
|--------|---------|---------|
| 搜索关键词 | `search.keywords` | 关键词 × 城市组合遍历 |
| 每关键词翻页数 | `search.max_pages` | 每关键词最多翻这么多页 |
| 城市 | `search.cities` | ⚠️ 当前只支持上海（`CITY_AREA=020000`） |
| 最低薪资 (K) | `profile.salary_min` | 入库前严格过滤 |
| 最高薪资 (K) | `profile.salary_max` | 入库前严格过滤 |
| 排除关键词 | `profile.deal_breakers` | 标题命中即跳过 |
| JD 排除关键词 | `profile.jd_deal_breakers` | JD 正文命中即跳过 |
| 屏蔽公司 | `profile.blocked_companies` | 公司名命中即跳过 |

### 薪资过滤语义（严格）

岗位薪资区间**必须完全落在** `[salary_min, salary_max]` 内才入库：

- 岗位最低薪资 ≥ 设定最低薪资
- 岗位最高薪资 ≤ 设定最高薪资
- 解析失败（"面议"、空）→ **不拦截**（保守，避免漏抓）

---

## 四、风控防护体系（重点）

### 4.1 核心洞察

**51job 的风控主要形态是「静默降级」，不是弹验证码。** 识别靠三类信号：

| 信号 | 表现 | 检测方式 |
|------|------|---------|
| 列表静默空 | cards=0 且 title 还是通用"全国招聘" | `JS_LIST_READY` 返回 `throttled` |
| 详情页 offline 飙升 | 连正常岗位都显示"下架" | offline 比例统计 |
| 渲染时间突变 | 列表渲染比基线慢 2.5 倍 | 渲染耗时样本对比 |

### 4.2 分簇节奏（BurstPacer）

详情页访问用「分簇节奏」，模拟真人"连续看几个岗位然后歇一下"：

```
簇内：连续抓 2~4 个详情页，间隔 20~28s（快，但不踩 17-27s 触发区）
簇间：长停（短停 75% 45-90s / 中停 20% 90-180s / 长停 5% 180-300s）
```

**关键：簇间长停是"压低总量"的手段**——节奏再拟真，单位时间请求总量超标照样触发风控。

### 4.3 风控常量（调整入口）

```python
# ---- 分簇节奏 ----
BURST_SIZE_MIN = 2          # 簇大小（连续抓详情页数）下限
BURST_SIZE_MAX = 4          # 簇大小上限
BURST_IN_GAP_MIN = 20.0     # 簇内详情页间隔下限（秒）
BURST_IN_GAP_MAX = 28.0     # 簇内间隔上限
BURST_BREAK_SHORT = (45.0, 90.0)    # 簇间短停（75% 概率）
BURST_BREAK_MID = (90.0, 180.0)     # 簇间中停（20% 概率）
BURST_BREAK_LONG = (180.0, 300.0)   # 簇间长停（5% 概率）

# ---- WAF 风控 ----
WAF_SLEEP_MIN = 20.0
WAF_SLEEP_MAX = 30.0

# ---- 渐进退避 ----
BACKOFF_STEP_1 = 30.0      # 第 1 次连续错误：额外等 30s
BACKOFF_STEP_2 = 120.0     # 第 2 次：额外等 120s
BACKOFF_STOP = 3           # 连续 3 次错误：停止整个采集

# ---- 静默降级检测阈值 ----
OFFLINE_RATIO_WARN = 0.5   # offline 占比 >50% → 疑似降级
OFFLINE_RATIO_STOP = 0.8   # offline 占比 >80% → 确认降级，停止本词
RENDER_SLOW_FACTOR = 2.5   # 渲染耗时超基线 2.5 倍 → 疑似限速
```

### 4.4 风险等级

| 等级 | 含义 | 触发条件 |
|------|------|---------|
| L0 | 正常 | 无信号 |
| L1 | 疑似 | 单软信号（静默空 / 渲染慢） |
| L2 | 确认 | 软信号叠加 / offline 比例超标 |
| L3 | 硬风控 | 滑块 / 验证墙 |

### 4.5 处置策略

| 信号 | 处置 |
|------|------|
| 首屏滑块/验证墙 | 记录 L3，跳过本词，退避 |
| offline 比例 >80% | 判定降级，停止本词 |
| 列表静默空 | L1 记录，加长等待再试一次 |
| 连续 3 次错误 | 停止整个采集（不硬闯风控） |
| new_tab 连续 3 次失败 | 判定浏览器断连，停止采集 |

---

## 五、风控日志

风控事件结构化落盘到 `data/risk_log/`：

```
data/risk_log/
├─ risk_events.jsonl    # 风控事件（每行一条 JSON）
└─ pace_snapshot.jsonl  # 节奏快照（每次网络动作的时间戳）
```

用节奏快照可以事后反推"安全密度边界"，替代"主动探测触发阈值"（后者会搞死 IP）。

---

## 六、自包含设计

模块内置以下能力，迁移时不依赖 `job_filters.py` / `prefilter.py`：

| 内置函数 | 作用 |
|---------|------|
| `_parse_salary_range` | 薪资解析（万/千/年薪/K 格式） |
| `_matching_deal_breaker` | 排除关键词匹配 |
| `_matching_blocked_company` | 屏蔽公司匹配 |
| `HumanPacer` / `BurstPacer` | 拟人化节奏 / 分簇节奏 |
| `RiskDetector` / `RiskLogger` / `PaceTracker` | 风控检测 / 日志 / 节奏快照 |
| `_ProgressiveBackoff` | 渐进退避 |

**外部依赖（原项目核心，迁移时需保留）**：
- `bosshunter.browser`（new_tab / close_tab / evaluate / scroll）
- `bosshunter.db`（get_db / job_exists / insert_job / get_collected_combos / mark_combo_collected）
- `bosshunter.cancellation`（get_stop_event）
- `bosshunter.throttle`（SendWindowChecker / should_take_day_off）

> 注意：`get_collected_combos` / `mark_combo_collected` 是断点续采依赖的两个 db 函数（在 db.py 中），迁移时需连同 db.py 一起带。

---

## 六.5、断点续采

### 机制

- **粒度**：关键词级——每采完一个关键词，写一次 checkpoint 到 `collect_progress` 表
- **表结构**：`collect_progress(source, city, keyword, finished_at)`，主键 `(source, city, keyword)`
- **触发**：默认自动续采——启动时读 checkpoint，跳过已完成的 (city, keyword) 组合，从剩余继续
- **幂等**：`INSERT OR IGNORE`，重复标记不报错

### 语义

```
第 1 次：IT运维 ✅ → 网络工程师 ✅ → 虚拟化 ❌（中断，不记 checkpoint）
第 2 次：跳过 IT运维、网络工程师，从「虚拟化」重新开始（中断的关键词重采）
```

中断时「当前未完成的关键词」不记 checkpoint，下次重采整个关键词（靠去重避免重复入库）。

### 进度展示

看板任务卡片会显示「断点续采：已跳过 N 个已完成关键词，本次采集 M 个」（`resume_skipped` / `resume_total` 通过进度回调上报）。

### 与 BOSS 的关系

BOSS 和 51job 各自独立断点（`source` 字段区分），互不影响。

---

## 七、调参指引（风控敏感，务必小规模验证）

> ⚠️ **节奏参数是"风控试探"，不是"代码优化"。每调一次都要用小规模验证，绝不能改完就上全量。**

| 想提速 | 改法 | 风控代价 |
|--------|------|---------|
| 簇间停时长砍半 | `BURST_BREAK_*` 区间下移 | 总量密度上升 |
| 长尾向短停倾斜 | 概率 75/20/5 调整 | 长停变少，周期性增强 |
| 簇内间隔缩短 | `BURST_IN_GAP_*` 下调 | ⚠️ 逼近 17-27s 触发区 |

**铁律**：
1. 风控是累积、单向收紧的——触发一次就立即停、充分冷却（24h+）
2. 绝不在敏感期反复试节奏（代码能改，信用分只能等冷却）
3. 改代码后必须重启看板进程，否则跑的还是旧代码

---

## 八、已知限制

1. **城市只支持上海**（`CITY_AREA=020000`）——51job 城市编码有冲突（040000 新版=上海/老版=深圳），多城市需实测校准
2. **薪资筛选用"入库前过滤"而非 URL 参数**——51job 搜索 URL 的 `salary=` 档位编码表未实测（待账号冷却后校准）
3. `HARD_MAX_PAGES=200` 是防分页器死循环的防御上限，非推荐采集页数

---

## 九、数据落盘说明（重要）

### 数据库路径

db 路径跟随 `config["_base_dir"]`（看板注入），拼成 `<base_dir>/data/bosshunter.db`；无 `_base_dir` 时用默认相对路径 `./data/bosshunter.db`。

### source 字段

入库时写 `source='51job'`，与 BOSS（`source='boss'`）在同一张 `jobs` 表逻辑分离。

### WAL 模式 + 时区（排查"数据没落盘"时注意）

1. **WAL 假象**：数据先写 `bosshunter.db-wal`，主 `.db` 文件时间戳不更新，直到 checkpoint。查数据时用 `PRAGMA wal_checkpoint(PASSIVE)` 强制读最新。
2. **时区坑**：`created_at` 存 UTC（SQLite `CURRENT_TIMESTAMP`），北京 18:00 = UTC 10:00。用北京时间条件查 SQL 会查不到，需减 8 小时。

---

## 十、演进方向（下一步）

**API 旁路监听方案**（优于当前 DOM 解析，待账号冷却后实施）：
- 监听 `we.51job.com/api/job/search-pc` 接口返回的 JSON（岗位在 `resultbody.job.items[]`）
- 优势：更快（不用等 DOM 二次渲染 15-60s）、更稳（不受 DOM 改版影响）
- 翻页需回传 requestId（第 1 页空，响应返回后第 2 页起回传）
- 参考实现：`scripts/liepin/liepin_api_cdp.js`
