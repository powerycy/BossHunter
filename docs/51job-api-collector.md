# 51job API 采集器设计说明

本文档面向维护者，说明 `collection/platforms/job51.py` 从 DOM 解析切换为 API 抓取后的核心设计，以及配套的断点续采与编排器改动。阅读本文有助于理解「为什么这么写」，避免在后续维护中把**刻意设计**当作 BUG 修复。

---

## 1. 概述

原实现通过 DOM 选择器解析 51job 搜索/列表页（依赖 `sensorsdata` 属性），并对每条职位单独打开详情页读取 JD，脆弱且缓慢。

新实现在页面上下文内直接调用 `we.51job.com/api/job/search-pc` 搜索 API，单次请求同时返回职位列表与 JD（`jobDescribe`），**无需逐条打开详情页**。对外保持 `collection/` 共享管线契约不变（`Job51Collector` 实现 `Collector` 协议、`PlatformCollectionRequest` / `JobCandidate` / `CollectionBlockedError` 语义一致），因此 orchestrator 与 registry 无需结构性改造。

涉及的三个文件：

| 文件 | 改动 |
|------|------|
| `collection/platforms/job51.py` | 核心重写（API 抓取 + 组合式采样 + 主动末页判定 + 风控分级 + 自适应速率） |
| `db.py` | 追加两级断点续采（2 张表 + 8 个辅助函数） |
| `collection/orchestrator.py` | +2 行，向默认 registry 的 `Job51Collector` 注入 `config` 与 `safety_conn` |

---

## 2. 关键常量

```python
API_PAGE_SIZE = 20   # 每页条数（搜索 API 上限 20，实测稳定）
HARD_MAX_PAGES = 50  # 51job 搜索结果封顶 50 页（页面上限，非防御值）
```

- `HARD_MAX_PAGES = 50` 是 51job 搜索结果的**原生封顶**（20 条/页 × 50 页 = 1000 条），不是人为设置的防御上限。超过该值的翻页请求不会返回数据。
- `totalCount` 是「关键词 + 城市」的**全词命中总数**，与 `pageNum` 无关；真实可翻页数须由它反推（见第 4 节）。

---

## 3. 组合式采样策略

这是本模块最核心的设计，**请勿当作冗余逻辑删改**。目标：在「页面上限 50 页」与「真实末页可能远小于 50」两种情况下，都能以有限请求覆盖足够多的职位，同时避免对 51job 造成压力。

采样由多段**刻意叠加**的针组成，顺序如下：

1. **分布式探针（前密后疏 + 随机 + 不相邻）**
   在页码范围前半段密度高、后半段密度低地随机撒点，且任意两针不相邻。确认是零结果或越过真实末页时才跳过；其他异常立即停止。

2. **保底针（补足 ~70% 覆盖，固定纳入 P2）**
   探针的不相邻约束会在页面之间留下缝隙，保底针用于把这些缝隙补到约 70% 覆盖。
   > ⚠️ **固定 P2 保底针是刻意设计，不是 BUG**：因为探针段不允许相邻，第 2 页会天然落入「相邻缝隙」，必须由保底针补上。看到 `page=2` 固定出现时请勿「优化」掉。

3. **热区 ±3 扫描**
   对命中热点页（有有效职位且相关性高的页）向左右各扩 3 页精细扫描，提升命中区域覆盖率。

4. **随机插一针**
   在未覆盖区间再随机补一针，降低分布的系统性偏差。

5. **续采起点 / 邻位强制纳入**
   断点续采恢复后，续采起点页与其相邻页强制纳入本轮采样；正式热区按页码升序处理，使单页码检查点始终可以安全地从 `N+1` 恢复。

6. **饱和跳过**
   连续数页无新职位（去重后）则判定该关键词已饱和，提前结束，避免无谓翻页。

### 3.1 空页与风控的区分（关键）

探针段遇到**空页**是正常现象（尤其真实末页远小于 50 时），必须与「风控拦截导致的非 JSON」区分开：

- `status=1`、`totalCount=0` → 合法的零结果，继续其他关键词。
- `items` 为空且当前页大于 `totalCount` 推导出的真实末页 → 合法越界页，收窄页数后跳过。
- 风控拦截、限流、解析失败或有效范围内异常空页 → 抛 `CollectionBlockedError`，不把关键词标记成已完成。

若把「翻到不存在页」误当风控，会导致整个平台误停 —— 这正是第 4 节「主动末页判定」要解决的历史问题。

---

## 4. 主动末页判定

问题：用户设定 50 页，但某关键词实际只有 30 页（`totalCount` 较小）。若仍按 50 页硬翻，翻到第 31 页时 API 返回体与正常空页不同，极易被误判为风控（`non_json` L3），从而误停整个平台。

解法：用 `totalCount` 反推**真实末页**，把热区扩展 / 保底针 / 随机针的有效页码范围收窄到真实末页之内。

```python
total_pages = (total + API_PAGE_SIZE - 1) // API_PAGE_SIZE
return min(fallback, max(1, total_pages))
```

- `_real_last_page(analysis, fallback)`：从响应里的 `totalCount` 计算 `ceil(total/20)`，与传入的 fallback 取小，且不小于 1。
- `_effective_max`：用主动末页判定结果收窄后续采样的最大页。

效果（实机验证）：关键词设 `max_pages=50` 而实际末页 36 时，探针全部落在有效范围内，保底针全部 ≤ 36，越界第 50 页返回 `empty_items`（**非** `non_json`），不产生误停。

---

## 5. L0-L3 风控分级

`_analyze_api_response(http_status, content_type, body)` 对每次 API 响应分类：

| 级别 | 含义 | 处理 |
|------|------|------|
| L0 | `status=1`，有岗位或明确 `totalCount=0` | 继续 |
| L1 | JSON 解析失败 | 停止并提示接口结构可能变化 |
| L2 | 普通限流，或有效页 `items` 为空但 `totalCount>0` | 停止，不重试 |
| L3 | HTTP 非 200、非 JSON、登录/验证码/封禁信号 | 立即停止，不重试 |

设计原则：**宁可漏采，不可硬刚**。所有非正常响应均 fail-closed；只有能够由 `totalCount` 明确证明的正常空结果或越界页可以跳过。

---

## 6. API 自适应速率

`_ApiRateLimiter` 按**总请求数**动态升档（三档，阈值约 65/130），同时叠加「聚类间隔 + 每分钟滑动窗口上限」两条约束。

> ⚠️ **本节逻辑请勿修改 / 放宽。** 速率自适应没有设置回落降档是有意的——51job 对请求频率的容忍度是**账号级累积、单向收紧**的，一旦被识别为高频抓取，放宽一个档位也可能触发封禁。宁可慢，不可快。历史产品决策明确记录了「不要动这里的档位与阈值」。

---

## 7. 两级断点续采

`db.py` 新增两类检查点，均由 `_init_collect_progress(conn)` 建表，并在 `_init_tables` 中随库初始化自动创建：

- **词级**（表 `collect_progress`）：某「城市 + 关键词」组合完成采集即标记，默认 24h 内整词跳过，避免重复高频抓同一个词。
- **页级**（表 `collect_progress_page`）：记录每个词已采到的页码，续采从 `N+1` 开始，而不是从 0 重来。

新增函数（共 8 个）：

| 词级 | 页级 |
|------|------|
| `get_collected_combos` | `get_page_progress` |
| `clear_collected_combos` | `clear_page_progress` |
| `prune_collected_combos` | `prune_page_progress` |
| `mark_combo_collected` | `upsert_page_progress` / `delete_page_progress` |

> 说明：底座库已有 `mark_combo_collected` 语义的函数（`mark_combo_collected` 为新增覆盖），词级检查点复用了「source 维度」的分区方式，页级检查点是 51job API 采集特有（DOM 解析逐条翻页无此概念）的增量。

---

## 8. 编排器注入（orchestrator.py）

默认 registry 下，`Job51Collector` 现在与 `BossCollector` 一样拿到 `config` 与 `safety_conn`：

```python
collector = (
    BossCollector(config=self.config, safety_conn=conn)
    if platform == "boss" and self._uses_default_registry
    else Job51Collector(config=self.config, safety_conn=conn)
    if platform == "51job" and self._uses_default_registry
    else self.registry.get(platform)
)
```

否则断点检查点拿不到 DB 连接，续采逻辑会静默失效。

---

## 9. 验证记录

- 三个文件均通过 `ast.parse`。
- 项目 venv 内 import 级检查：所有符号可解析。
- 临时库往返测试：`_init_tables` 自动建两张检查点表；词级 / 页级检查点读写往返正确。
- 实机冒烟（dev 分支、同一逻辑）：单关键词 `max_pages=50`，探针全部落在有效范围，`totalCount` 把有效末页收窄到 36，保底针全部 ≤ 36，越界页返回 `empty_items` 而非 `non_json`，**无误停**。
- `tests/test_job51_collector.py` 覆盖响应分级、末页判定、探针规划、异常 fail-closed 与升序断点流程。
