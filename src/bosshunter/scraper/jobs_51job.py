"""51job scraper module - Extract jobs from 51job (we.51job.com).

2026-08-21 CDP 实测验证的正确采集姿势：
- 页面两阶段渲染：先 SSR 渲染 .job-item（6条占位）→ 约 8-9s 后 Vue 挂载真正的
  .joblist-item 列表（每页 20 条，sensorsdata 明文 JSON）+ .el-pagination 分页器
- 列表选择器：.joblist-item + [sensorsdata] 属性 JSON（jobId/jobTitle/jobSalary/jobArea/jobYear/jobDegree 全字段）
- 翻页：只能点 button.btn-next（箭头图标，无文字）；URL 加 page/pageNum/currPage/p 均无效
- 末页判定：button.btn-next 按钮 disabled
- 分页器：Element UI el-pagination，共约 50 页
- 风控：阿里云 WAF + 极验滑块（title=「滑动验证页面」/「请按住滑块」）

仿真人策略（参考 BossHunter throttle.py）：
- 翻页间隔用高斯分布（random.gauss），不是均匀分布——真人节奏有波动
- 5% 概率额外犹豫 2-5s（模拟真人分心）
- 翻页前随机小幅滚动（模拟真人浏览）
- 词间间隔高斯随机
- 风控信号（WAF/滑块）→ 立即停止该词，不硬闯

job_id = 51job jobId（与 BOSS /job_detail/{id} 命名空间不同，天然不冲突）。
"""

import json
import random
import re
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn

from bosshunter.browser import new_tab, close_tab, evaluate, scroll
from bosshunter.cancellation import get_stop_event
from bosshunter.db import get_db, job_exists, insert_job
from bosshunter.throttle import SendWindowChecker, should_take_day_off

console = Console()

# 51job 上海 jobArea（与扩展一致：020000=上海）
CITY_AREA = "020000"
SEARCH_URL = "https://we.51job.com/pc/search?jobArea={area}&keyword={keyword}"

# ---- 仿真人时序参数 ----
# 分簇节奏（Burst Pattern）：簇内快 + 簇间长停，模拟真人"连续看几个岗位然后歇一下"
BURST_SIZE_MIN = 2         # 簇大小（连续抓的详情页数）随机下限
BURST_SIZE_MAX = 4         # 簇大小随机上限
BURST_IN_GAP_MIN = 20.0    # 簇内详情页间隔（秒）下限——快，但不踩 17-27s 触发区
BURST_IN_GAP_MAX = 28.0    # 簇内间隔上限
BURST_BREAK_SHORT = (45.0, 90.0)     # 簇间短停（75% 概率，方案 A 向短停倾斜）
BURST_BREAK_MID = (90.0, 180.0)      # 簇间中停（20% 概率）
BURST_BREAK_LONG = (180.0, 300.0)    # 簇间长停（5% 概率）
BROWSE_BEFORE_TURN = True  # 翻页前随机小幅滚动（模拟真人浏览）
HARD_MAX_PAGES = 200     # 防御上限，防分页器失效死循环

# WAF 风控
WAF_SLEEP_MIN = 20.0
WAF_SLEEP_MAX = 30.0

# 渐进退避（参考 BossHunter ProgressiveBackoff：连续错误 → 退避递增 → 阈值停止）
BACKOFF_STEP_1 = 30.0     # 第 1 次连续错误：额外等 30s
BACKOFF_STEP_2 = 120.0    # 第 2 次：额外等 120s
BACKOFF_STOP = 3          # 连续 3 次错误：停止整个采集（不硬闯风控）

# ---- 风控信号聚合检测（静默降级是 51job 风控的主要形态，不能只看弹验证码）----
OFFLINE_RATIO_WARN = 0.5    # 详情页 offline 占比超过 50% → 疑似降级（正常下架通常 <30%）
OFFLINE_RATIO_STOP = 0.8    # offline 占比超过 80% → 确认降级，停止本词
RENDER_SLOW_FACTOR = 2.5    # 列表渲染耗时超过滚动基线 2.5 倍 → 疑似限速
RISK_LOG_DIR = "data/risk_log"  # 风控事件结构化日志目录（相对 base_dir）

# ---- 列表页提取：.joblist-item + sensorsdata JSON（CDP 实测正确选择器）----
# ⚠️ ES5 语法（CDP Runtime.evaluate 对箭头函数/模板字符串/可选链报 Uncaught）
# jobId 从标题 DIV（.joblist-item-job）的 sensorsdata 取；详情 URL 拼 jobs.51job.com/shanghai/{jobId}.html
# 过滤垃圾卡片：APP下载 / 访问验证 / 无 jobId
JS_EXTRACT_LIST = r"""
(function () {
    var cards = Array.prototype.slice.call(document.querySelectorAll('.joblist-item'));
    var kw = new URLSearchParams(location.search).get('keyword') || '';
    var jobs = [];
    for (var i = 0; i < cards.length; i++) {
        var c = cards[i];
        // 取标题 DIV 的 sensorsdata（含 jobId/jobTitle/jobSalary/jobArea）
        var jobDiv = c.querySelector('.joblist-item-job, [class*="jobname"], [class*="job-name"]');
        var sd = jobDiv ? jobDiv.querySelector('[sensorsdata]') || jobDiv : c.querySelector('[sensorsdata]');
        var meta = {};
        if (sd) { try { meta = JSON.parse(sd.getAttribute('sensorsdata') || '{}'); } catch (e) {} }
        var jobId = meta.jobId || '';
        var ZP = String.fromCharCode(25307, 32856);   // 招聘
        var title = (meta.jobTitle || '').replace(new RegExp('^' + ZP), '');
        // 垃圾卡片过滤（中文用 fromCharCode 避免 evaluate 传输损坏）
        var APP = String.fromCharCode(65, 80, 80) + String.fromCharCode(19979, 36733);   // APP下载
        var VERIFY = String.fromCharCode(35775, 38382, 39564, 35777);                     // 访问验证
        if (!jobId || !title) continue;
        if (title.indexOf(APP) !== -1 || title.indexOf(VERIFY) !== -1 || title.indexOf(String.fromCharCode(19979, 36733)) !== -1) continue;
        var salary = meta.jobSalary || '';
        var area = meta.jobArea || '';
        var parts = area.split('·');
        var city = (parts[0] || '').trim();
        // 经验/学历：sensorsdata 的 jobYear（经验）+ jobDegree（学历），合并存 experience 字段（jobs 表无独立 education 列）
        var year = meta.jobYear || '';
        var degree = meta.jobDegree || '';
        var experience = (year && degree) ? (year + '·' + degree) : (year || degree || '');
        var company = '';
        var comEl = c.querySelector('[class*="company"], [class*="cname"], [class*="comname"], .comp');
        if (comEl) { company = comEl.innerText.trim().split('\n')[0]; }
        // 公司行业 + 规模：卡片 .bc .dc 序列 = [行业, 公司性质, 规模]（最后一个是"XX-XX人"）
        var industry = '';
        var size = '';
        var dcs = Array.prototype.slice.call(c.querySelectorAll('.bc .dc, [class*="dc"]'));
        if (dcs.length >= 2) {
            industry = (dcs[0].innerText || '').trim();
        }
        for (var j = 0; j < dcs.length; j++) {
            var t2 = (dcs[j].innerText || '').trim();
            if (/\u4eba$/.test(t2)) { size = t2; break; }   // 以"人"结尾 = 规模
        }
        // 职位详情 URL：jobs.51job.com/shanghai/{jobId}.html（不能用 a[href]，那是公司链接）
        var href = 'https://jobs.51job.com/shanghai/' + jobId + '.html';
        jobs.push({ title: title, company: company, salary: salary, href: href, jobId: jobId, city: city, experience: experience, industry: industry, size: size, keyword: kw });
    }
    return JSON.stringify({ keyword: kw, jobs: jobs });
})()
"""

# ---- 详情页提取（jobs.51job.com/shanghai/{jobId}.html）----
# JD 在正文「职位描述」锚点之后；⚠️ 中文用 String.fromCharCode 避免 evaluate 传输时损坏
JS_EXTRACT_DETAIL = r"""
(function () {
    var info = {};
    var body = document.body.innerText || '';
    var JD_ANCHOR = String.fromCharCode(32844, 20301, 25551, 36848);   // 职位描述
    var ZP = String.fromCharCode(25307, 32856);                          // 招聘
    function norm(el) { return el ? el.innerText.trim() : ''; }
    var titleEl = document.querySelector('h1') || document.querySelector('[class*="job-name"]');
    info.title = norm(titleEl).replace(new RegExp('^' + ZP), '');
    if (!info.title) { info.title = document.title.split(ZP)[0].trim(); }
    var salaryEl = document.querySelector('[class*="salary"]');
    info.salary = norm(salaryEl);
    // JD：从正文「职位描述」锚点取其后内容
    var idx = body.indexOf(JD_ANCHOR);
    if (idx !== -1) {
        info.jd = body.slice(idx).replace(/\s+/g, ' ').trim();
    } else {
        info.jd = '';
    }
    var comEl = document.querySelector('[class*="company"] a, [class*="cname"] a, .comp');
    info.company = norm(comEl);
    var areaEl = document.querySelector('[class*="area"], [class*="location"]');
    info.city = norm(areaEl);
    return JSON.stringify(info);
})()
"""

# 下一页按钮是否禁用（Element UI el-pagination：末页 btn-next disabled）
JS_NEXT_DISABLED = r"""
(function () {
    var el = document.querySelector('button.btn-next');
    if (!el) return null;
    return el.disabled === true || /disabled|is-disabled/.test(el.className || '');
})()
"""

# 点击下一页按钮
JS_CLICK_NEXT = r"""
(function () {
    var el = document.querySelector('button.btn-next');
    if (!el) return false;
    if (el.disabled === true || /disabled|is-disabled/.test(el.className || '')) return false;
    el.click();
    return true;
})()
"""

# 当前页码（分页器活动项）
JS_CUR_PAGE = r"""
(function () {
    var el = document.querySelector('.el-pagination .number.active, .el-pager li.active');
    if (!el) return NaN;
    return parseInt((el.getAttribute('title') || el.innerText || '').trim(), 10) || NaN;
})()
"""

# WAF 检测（精确匹配；中文用 fromCharCode 避免 evaluate 传输损坏）
JS_WAF = r"""
(function () {
    var T1 = String.fromCharCode(28369, 21160, 39564, 35777, 39029, 38754);   // 滑动验证页面
    var T2 = String.fromCharCode(35831, 25353, 20303, 28369, 22359);          // 请按住滑块
    var T3 = String.fromCharCode(25302, 21160, 19979, 26041, 28369, 22359);   // 拖动下方滑块
    if (document.title.trim() === T1) return true;
    var t = (document.body.innerText || '').replace(/\s+/g, ' ');
    return t.indexOf(T2) !== -1 || t.indexOf(T3) !== -1;
})()
"""

# 列表页「.joblist-item」是否已渲染（SPA 二次渲染，必须轮询等 Vue 挂载完成，不能只看 readyState）
# 增加「静默空列表」识别：cards=0 且 title 是通用标题（"全国招聘"）→ 搜索词没生效，疑似限流
JS_LIST_READY = r"""
(function () {
    var VERIFY = String.fromCharCode(35775, 38382, 39564, 35777);   // 访问验证
    var b = document.body.innerText || '';
    if (b.indexOf(VERIFY) !== -1) return 'waf';
    var cards = document.querySelectorAll('.joblist-item').length;
    if (cards > 0) return 'ready';
    // 静默空列表：title 还是通用标题（非搜索词结果页）→ 疑似限流降级
    var t = document.title || '';
    var GENERIC = String.fromCharCode(20840, 22269, 25307, 32856);  // 全国招聘
    if (t.indexOf(GENERIC) !== -1 && cards === 0) return 'throttled';
    return 'loading';
})()
"""


def _wait_or_stop(stop_event, seconds: float) -> bool:
    if stop_event is not None:
        return stop_event.wait(seconds)
    time.sleep(seconds)
    return False


def _parse_salary_range(salary: str) -> tuple[float, float] | None:
    """解析薪资，返回可比较的月薪区间 (minK, maxK)，单位 K/月。

    内置在 51job 采集模块内（不依赖 job_filters.parse_monthly_salary_k），
    使模块自包含、可独立迁移。支持格式：
    - BOSS: "15-25K" / "20K"
    - 51job 中文: "8千-1.2万" / "1-2万" / "1.5-2万·13薪"
    - 51job 年薪: "25-38万/年"（换算为月薪）
    解析失败返回 None。
    """
    normalized = str(salary or "").strip()

    # 年薪格式（万/年）→ 换算月薪
    yearly_match = re.search(
        r"(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)\s*万\s*/\s*年",
        normalized,
    )
    if yearly_match:
        lo = float(yearly_match.group(1)) * 10000 / 12 / 1000
        hi = float(yearly_match.group(2)) * 10000 / 12 / 1000
        return (round(min(lo, hi), 1), round(max(lo, hi), 1))

    # 中文区间：X万-X万 / X千-X万
    cn_range = re.search(
        r"(\d+(?:\.\d+)?)\s*([千万])\s*-\s*(\d+(?:\.\d+)?)\s*万",
        normalized,
    )
    if cn_range:
        lo_val = float(cn_range.group(1))
        lo_unit = cn_range.group(2)
        hi_val = float(cn_range.group(3))
        lo_k = lo_val * (10 if lo_unit == "万" else 1)
        hi_k = hi_val * 10
        return (round(min(lo_k, hi_k), 1), round(max(lo_k, hi_k), 1))

    # 纯数字区间 + 万：如 "1-2万" / "1.5-2万"（左值无单位）
    cn_range_plain = re.search(
        r"(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)\s*万",
        normalized,
    )
    if cn_range_plain:
        lo_k = float(cn_range_plain.group(1)) * 10
        hi_k = float(cn_range_plain.group(2)) * 10
        return (round(min(lo_k, hi_k), 1), round(max(lo_k, hi_k), 1))

    # 中文单值：X万 / X千
    cn_single_wan = re.search(r"(\d+(?:\.\d+)?)\s*万", normalized)
    if cn_single_wan:
        value = round(float(cn_single_wan.group(1)) * 10, 1)
        return value, value
    cn_single_qian = re.search(r"(\d+(?:\.\d+)?)\s*千", normalized)
    if cn_single_qian:
        value = round(float(cn_single_qian.group(1)), 1)
        return value, value

    # 英文 K 格式（BOSS）
    range_match = re.search(
        r"(\d+(?:\.\d+)?)\s*[kK]?\s*-\s*(\d+(?:\.\d+)?)\s*[kK]",
        normalized,
    )
    if range_match:
        low, high = (float(value) for value in range_match.groups())
        return (min(low, high), max(low, high))

    single_match = re.search(r"(\d+(?:\.\d+)?)\s*[kK](?!\w)", normalized)
    if single_match:
        value = float(single_match.group(1))
        return value, value
    return None


def _matching_deal_breaker(text: str, deal_breakers: list[str]) -> str | None:
    """返回第一个命中的排除关键词（内置，避免依赖 job_filters）。"""
    text_lower = (text or "").lower()
    for keyword in deal_breakers or []:
        cleaned = str(keyword or "").strip()
        if cleaned and cleaned.lower() in text_lower:
            return keyword
    return None


def _matching_blocked_company(company: str, blocked_companies: list[str]) -> str | None:
    """返回第一个命中的屏蔽公司（内置，避免依赖 job_filters）。"""
    company_lower = str(company or "").strip().lower()
    for rule in blocked_companies or []:
        cleaned = str(rule or "").strip()
        if cleaned and cleaned.lower() in company_lower:
            return cleaned
    return None


class HumanPacer:
    """拟人化节奏器：打破周期性的固定节奏，注入非平稳性（真人行为特征）。

    核心思想：真人的浏览节奏不是固定间隔的机械重复，而是——
    - 大部分时间按高斯分布的间隔浏览
    - 偶尔停下来（短冷却，模拟"看看别的"）
    - 罕见地走开很久（长冷却，模拟"离开一下"）

    关键实现：间隔是「额外叠加」的（在动作之外额外 sleep），**不是**
    RequestThrottle 那种「目标间隔 − 已耗时」。后者会被"打开详情页→抓取"
    的自然耗时（17-25s）吃掉预留间隔，导致净间隔退化到 17-27s（实测触发风控）。
    """

    def __init__(
        self,
        base_mean: float,
        base_std: float,
        short_cool_prob: float = 0.25,
        long_cool_prob: float = 0.05,
        short_cool_range: tuple = (60.0, 150.0),
        long_cool_range: tuple = (180.0, 300.0),
    ) -> None:
        self._base_mean = base_mean
        self._base_std = base_std
        self._short_prob = short_cool_prob
        self._long_prob = long_cool_prob
        self._short_range = short_cool_range
        self._long_range = long_cool_range

    def pause(self, stop_event) -> str:
        """执行一次拟人化暂停（额外叠加），返回暂停描述（供日志/控制台）。"""
        r = random.random()
        if r < self._long_prob:
            secs = random.uniform(*self._long_range)
            _wait_or_stop(stop_event, secs)
            return f"长冷却{int(secs)}s"
        if r < self._long_prob + self._short_prob:
            secs = random.uniform(*self._short_range)
            _wait_or_stop(stop_event, secs)
            return f"短冷却{int(secs)}s"
        secs = max(5.0, random.gauss(self._base_mean, self._base_std))
        _wait_or_stop(stop_event, secs)
        return f"间隔{int(secs)}s"


class BurstPacer:
    """分簇节奏器（Burst Pattern）：簇内快 + 簇间长停，模拟真人"连续看几个岗位然后歇一下"。

    真人的浏览不是匀速的，而是"看到感兴趣的就连续点几个（快），看累了停下来（长停）"。
    分簇节奏把这个特征结构化：

    - 簇内：连续抓 2~4 个详情页，间隔 20~28s（快，但不踩 17-27s 触发区）
    - 簇间：停 60~480s（短停 60% / 中停 30% / 长停 10%，长尾分布）

    关键：簇间长停是"压低总量"的手段——节奏再拟真，单位时间请求总量超标照样触发风控。
    长停把平均密度拉下来，同时保留簇内"快"的真实感。
    """

    def __init__(self) -> None:
        self._in_burst = 0          # 当前簇内已抓详情页数
        self._burst_size = 0        # 当前簇大小（随机）
        self._last_ts = 0.0         # 上次详情页访问时刻
        self._roll_burst_size()

    def _roll_burst_size(self) -> None:
        self._burst_size = random.randint(BURST_SIZE_MIN, BURST_SIZE_MAX)

    def _cluster_break(self, stop_event) -> str:
        """簇间长停：短 75% / 中 20% / 长 5% 长尾分布（方案 A：向短停倾斜，压时长）。"""
        r = random.random()
        if r < 0.05:
            secs = random.uniform(*BURST_BREAK_LONG)
            _wait_or_stop(stop_event, secs)
            return f"簇间长停{int(secs)}s"
        if r < 0.25:
            secs = random.uniform(*BURST_BREAK_MID)
            _wait_or_stop(stop_event, secs)
            return f"簇间中停{int(secs)}s"
        secs = random.uniform(*BURST_BREAK_SHORT)
        _wait_or_stop(stop_event, secs)
        return f"簇间短停{int(secs)}s"

    def pause(self, stop_event) -> str:
        """执行一次分簇节奏暂停，返回暂停描述。

        逻辑：簇内（还没到簇大小）→ 快间隔 20~28s；到簇大小 → 簇间长停 + 重开新簇。

        关键：`_last_ts` 表示"上次详情页抓取完成的时刻"，由 mark_done() 更新；
        本方法用"距上次完成"的 elapsed 计算补足等待，避免被抓取耗时抵消。
        `_in_burst` 的递增在 mark_done()（方案 C）：只有真实抓完的详情页才占用簇内名额，
        失败（no_tab/stopped）的岗位不触发簇间长停。
        """
        if self._in_burst >= self._burst_size:
            desc = self._cluster_break(stop_event)
            self._in_burst = 0
            self._roll_burst_size()
            return desc
        # 簇内：快间隔（20~28s），且保证净间隔不低于下限（距上次完成不足则补足）
        target = random.uniform(BURST_IN_GAP_MIN, BURST_IN_GAP_MAX)
        elapsed = time.time() - self._last_ts
        if elapsed < target:
            _wait_or_stop(stop_event, target - elapsed)
        return f"簇内间隔{int(target)}s"

    def mark_done(self) -> None:
        """在详情页抓取完成后调用：记录"完成时刻"作为下次间隔起点，并计入簇内进度（方案 C）。"""
        self._last_ts = time.time()
        self._in_burst += 1


# 拟人化节奏器实例：翻页/词间次之（详情页走 BurstPacer 分簇节奏）
_turn_pacer = HumanPacer(15.0, 5.0, short_cool_prob=0.15, long_cool_prob=0.02,
                         short_cool_range=(45.0, 90.0), long_cool_range=(120.0, 240.0))
_keyword_pacer = HumanPacer(20.0, 6.0, short_cool_prob=0.20, long_cool_prob=0.03,
                            short_cool_range=(50.0, 100.0), long_cool_range=(150.0, 280.0))

# 详情页分簇节奏器（全局单例，跨词保持节奏连续性）
_detail_pacer = BurstPacer()


class _ProgressiveBackoff:
    """连续错误退避（参考 BossHunter ProgressiveBackoff）。连续错误 → 退避递增 → 阈值停止。"""

    def __init__(self) -> None:
        self._consecutive_errors = 0

    def record_error(self) -> float:
        """记录一次错误，返回建议额外暂停秒数。"""
        self._consecutive_errors += 1
        if self._consecutive_errors >= BACKOFF_STOP:
            return 0.0
        return BACKOFF_STEP_1 if self._consecutive_errors == 1 else BACKOFF_STEP_2

    def record_success(self) -> None:
        self._consecutive_errors = 0

    @property
    def should_stop(self) -> bool:
        return self._consecutive_errors >= BACKOFF_STOP


class RiskDetector:
    """风控信号聚合检测器：聚合硬风控 + 静默降级两类信号，输出风险等级。

    核心洞察：51job 风控主要是「静默降级」（不弹验证码），表现成：
    - 列表静默空（cards=0 且 title 通用）
    - 详情页 offline 比例飙升（连正常岗位都显示"下架"）
    - 列表渲染时间突变（比基线慢 2.5 倍以上）

    等级定义：
    - L0 正常
    - L1 疑似（单软信号）
    - L2 确认（软信号叠加 / offline 比例超标）
    - L3 硬风控（滑块/验证墙）
    """

    def __init__(self) -> None:
        self._offline = 0          # 本词详情页 offline 计数
        self._detail_ok = 0        # 本词详情页成功计数
        self._render_times = []    # 列表渲染耗时样本（用于计算基线）

    # ---- 详情页结果计数 ----
    def detail_offline(self) -> None:
        self._offline += 1

    def detail_ok(self) -> None:
        self._detail_ok += 1

    def reset_keyword(self) -> None:
        """切换关键词时重置 per-keyword 计数。"""
        self._offline = 0
        self._detail_ok = 0

    # ---- 列表渲染耗时样本 ----
    def render_elapsed(self, seconds: float) -> None:
        self._render_times.append(seconds)
        if len(self._render_times) > 6:  # 保留最近 6 次
            self._render_times = self._render_times[-6:]

    def render_slow(self, seconds: float) -> bool:
        """本次渲染是否显著慢于滚动基线。样本不足时用绝对阈值兜底。"""
        if len(self._render_times) < 2:
            return seconds > 60.0
        base = sum(self._render_times) / len(self._render_times)
        return seconds > base * RENDER_SLOW_FACTOR and seconds > 45.0

    # ---- 风险等级判定 ----
    @property
    def offline_ratio(self) -> float:
        total = self._offline + self._detail_ok
        if total == 0:
            return 0.0
        return self._offline / total

    def level(self, signal: str) -> int:
        """根据当前状态 + 新信号，返回风险等级 0-3。

        signal: 'ok' | 'waf' | 'slider' | 'throttled' | 'render_slow' | 'offline_high'
        """
        if signal in ("waf", "slider"):
            return 3
        if signal == "offline_high" or self.offline_ratio >= OFFLINE_RATIO_STOP:
            return 2
        if signal == "throttled" or signal == "render_slow":
            return 1
        return 0


class RiskLogger:
    """风控事件结构化记录器：每次风控信号都落一条 JSON，便于事后复盘。

    输出到 {base_dir}/data/risk_log/risk_events.jsonl（追加），每行一条事件：
    {ts, level, signal, keyword, page, detail_offline, detail_ok, offline_ratio,
     render_seconds, action, note}
    """

    def __init__(self, base_dir) -> None:
        self._dir = Path(base_dir) / RISK_LOG_DIR
        self._path = self._dir / "risk_events.jsonl"

    def _ensure(self) -> None:
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass

    def log(self, level: int, signal: str, keyword: str = "", page: int = 0,
            offline_ratio: float = 0.0, render_seconds: float = 0.0,
            action: str = "", note: str = "") -> None:
        """落一条结构化风控事件。"""
        self._ensure()
        event = {
            "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "level": level,
            "level_name": {0: "L0_normal", 1: "L1_suspect", 2: "L2_confirmed", 3: "L3_hard"}.get(level, "L?"),
            "signal": signal,
            "keyword": keyword,
            "page": page,
            "offline_ratio": round(offline_ratio, 3),
            "render_seconds": round(render_seconds, 1),
            "action": action,
            "note": note,
        }
        try:
            with open(self._path, "a", encoding="utf-8") as f:
                f.write(json.dumps(event, ensure_ascii=False) + "\n")
        except OSError:
            pass

    @property
    def path(self):
        return str(self._path)


class PaceTracker:
    """采集节奏快照：记录每次网络动作的精确时间戳，事后反推"安全密度边界"。

    输出到 {base_dir}/data/risk_log/pace_snapshot.jsonl，每行一条动作：
    {ts, seq, action, keyword, page, detail_ok}
    action ∈ 'list_open' / 'page_turn' / 'detail_open'
    这样每次采集结束后，可以算出：无风控时的最大请求密度、触发前的密度变化，
    用历史数据拟合出"安全区"（而不是主动探测触发阈值）。
    """

    def __init__(self, base_dir) -> None:
        self._dir = Path(base_dir) / RISK_LOG_DIR
        self._path = self._dir / "pace_snapshot.jsonl"
        self._seq = 0

    def mark(self, action: str, keyword: str = "", page: int = 0) -> None:
        """记录一次网络动作。"""
        self._seq += 1
        event = {
            "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "seq": self._seq,
            "action": action,
            "keyword": keyword,
            "page": page,
        }
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            with open(self._path, "a", encoding="utf-8") as f:
                f.write(json.dumps(event, ensure_ascii=False) + "\n")
        except OSError:
            pass

    @property
    def path(self):
        return str(self._path)


def scrape_51job(
    config: dict,
    keywords: list[str],
    limit: int | None = None,
    *,
    collected_job_ids: list[str] | None = None,
) -> int:
    """Scrape jobs from 51job search pages, store new jobs in DB.

    与 BOSS 采集共用 jobs 表与去重：job_id = 51job jobId。已存在的（含工作台导入的
    已投递/拒绝记录）自动跳过（防二次投递）。
    支持城市 × 关键词组合（search.cities，默认上海），与 BOSS 采集对齐。
    """
    # 数据库路径：优先跟随 config 里的 _base_dir（看板注入），否则用默认相对路径
    _base = config.get("_base_dir")
    if _base:
        db = get_db(Path(_base) / "data" / "bosshunter.db")
    else:
        db = get_db()
    stop_event = get_stop_event(config)
    deal_breakers = config.get("profile", {}).get("deal_breakers", [])
    jd_deal_breakers = config.get("profile", {}).get("jd_deal_breakers", [])
    blocked_companies = config.get("profile", {}).get("blocked_companies", [])
    # 薪资范围过滤（profile.salary_min / salary_max，单位 K/月）
    salary_min = config.get("profile", {}).get("salary_min", 0)
    salary_max = config.get("profile", {}).get("salary_max", 0)
    try:
        salary_min = float(salary_min or 0)
        salary_max = float(salary_max or 0)
    except (TypeError, ValueError):
        salary_min = salary_max = 0
    search_cfg = config.get("search", {})
    max_pages = min(search_cfg.get("max_pages", 3), HARD_MAX_PAGES)

    # ---- 反检测统一（复用 BossHunter throttle 配置）----
    throttle_cfg = config.get("throttle", {}) or {}
    # 时间窗口：不在窗口内则拒绝采集（默认 09:00-17:00，与投递窗口一致）
    send_windows = throttle_cfg.get("send_windows", ["09:00-17:00"])
    window_checker = SendWindowChecker(send_windows)
    if not window_checker.is_active():
        console.print(f"[yellow]⚠ 当前不在采集时间窗口内（{send_windows}），跳过 51job 采集[/yellow]")
        db.close()
        return 0
    # 随机休息日：按 day_off_probability 概率跳过当天采集（模拟真人偶尔不投）
    if should_take_day_off(float(throttle_cfg.get("day_off_probability", 0.05))):
        console.print("[dim]今日随机休息，跳过 51job 采集（反检测）[/dim]")
        db.close()
        return 0

    # 城市解析：search.cities 为空时用默认上海（51job jobArea=020000）
    cities = search_cfg.get("cities", []) or ["上海"]

    seen_count = new_count = duplicate_count = 0
    no_tab_count = 0  # 连续 new_tab 失败计数（浏览器断连告警）
    skipped_combos = 0  # 断点续采：已跳过的已完成组合数
    total_combos = 0    # 断点续采：本次搜索组合总数
    backoff = _ProgressiveBackoff()

    # 看板进度回调（与 BOSS scrape_jobs 对齐：上报 seen/new/duplicate 到工作台）
    progress_callback = config.get("_workbench_collect_progress")

    def report_progress() -> None:
        if callable(progress_callback):
            progress_callback({
                "seen": seen_count,
                "new": new_count,
                "duplicate": duplicate_count,
                "resume_skipped": skipped_combos,
                "resume_total": total_combos,
            })

    # ---- 风控检测 + 结构化记录初始化 ----
    # base_dir：优先 config 里的 base_dir，否则用当前工作目录
    base_dir = config.get("_base_dir") or Path.cwd()
    risk = RiskDetector()
    risk_log = RiskLogger(base_dir)
    pace = PaceTracker(base_dir)
    # 记录采集开始
    risk_log.log(0, "collect_start", note=f"{len(keywords)} 关键词")

    if not keywords:
        console.print("[red]没有搜索关键词[/red]")
        db.close()
        return 0

    # 构建搜索组合：城市 × 关键词（51job 城市码走 CITY_AREA，暂只支持上海）
    search_combos = []
    for city in cities:
        area = CITY_AREA if city == "上海" else None
        if area is None:
            console.print(f"[yellow]⚠ 51job 暂只支持上海（未识别城市: {city}），已跳过[/yellow]")
            continue
        for keyword in keywords:
            search_combos.append((city, area, keyword))

    # 断点续采：跳过已完成的 (city, keyword) 组合
    from bosshunter.db import get_collected_combos, mark_combo_collected
    collected = get_collected_combos(db, "51job")
    pending_combos = [
        combo for combo in search_combos
        if (combo[0], combo[2]) not in collected
    ]
    skipped_combos = len(search_combos) - len(pending_combos)
    total_combos = len(search_combos)
    if collected:
        console.print(f"[dim]断点续采：已跳过 {skipped_combos} 个已完成组合，剩余 {len(pending_combos)} 个[/dim]")
    search_combos = pending_combos

    if not search_combos:
        console.print("[green]所有组合均已采集完成，无需续采[/green]")
        db.close()
        return 0

    console.print(f"[dim]51job 采集: {len(search_combos)} 组合 ({len(cities)}城市 × {len(keywords)}关键词 × {max_pages}页)[/dim]")

    with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"), console=console) as progress:
        for city, area, kw in search_combos:
            if stop_event is not None and stop_event.is_set():
                break
            if limit is not None and new_count >= limit:
                break

            label = f"{city}/{kw}" if len(cities) > 1 else kw
            task = progress.add_task(f"51job 搜索: {label}", total=None)
            keyword_new = 0

            # 打开第 1 页（51job URL 只认 keyword，永远落第 1 页）
            search_url = SEARCH_URL.format(area=area, keyword=quote(kw))
            pace.mark("list_open", keyword=kw, page=1)
            target_id = new_tab(search_url, background=True)
            if not target_id:
                progress.update(task, description=f"[red]✗ 无法打开: {kw}[/red]")
                continue

            # ★ 关键：轮询等待 .joblist-item 真正渲染（SPA 二次渲染，不能只看 readyState）
            # 不要提前 scroll——列表未渲染时滚动会干扰 Vue 异步加载（实测提前 scroll 反而更慢）
            _t0 = time.time()
            list_state = _wait_list_ready(target_id, stop_event, timeout=90.0)
            _render_sec = time.time() - _t0
            risk.render_elapsed(_render_sec)
            # 渲染成功后再小幅滚动（模拟真人浏览）
            if list_state == "ready":
                scroll(target_id, y=random.randint(400, 900))

            # WAF 检测（首屏）
            if list_state == "stopped":
                close_tab(target_id)
                break
            if list_state == "waf" or _is_waf(target_id):
                close_tab(target_id)
                progress.update(task, description=f"[red]✗ WAF 拦截: {kw}[/red]")
                console.print(f"[yellow]⚠ 51job WAF 滑块拦截（{kw}），跳过本词[/yellow]")
                # 风控事件记录（L3 硬风控）
                risk_log.log(3, "waf_slider", keyword=kw, render_seconds=_render_sec,
                             action="stop_keyword", note="首屏滑块/验证墙")
                # 记录错误 → 退避（连续 WAF 会触发停止）
                pause = backoff.record_error()
                if backoff.should_stop:
                    console.print("[red]⚠ 连续风控错误过多，停止整个 51job 采集（不硬闯）[/red]")
                    risk_log.log(3, "waf_repeated", keyword=kw, action="stop_all",
                                 note="连续风控错误 ≥3 次，整轮停止")
                    db.close()
                    return new_count
                if pause > 0:
                    console.print(f"[dim]  退避等待 {int(pause)}s 后继续[/dim]")
                    _wait_or_stop(stop_event, pause)
                continue

            # 静默空列表（疑似限流降级）→ L1 信号，记录并加长等待
            if list_state == "throttled":
                risk_log.log(1, "list_throttled", keyword=kw, render_seconds=_render_sec,
                             action="extend_wait", note="cards=0 且 title 通用，疑似限流")
                console.print(f"[yellow]⚠ 51job 列表静默空（{kw}），疑似限流，等待更久观察[/yellow]")
                list_state = _wait_list_ready(target_id, stop_event, timeout=60.0)
                if list_state == "ready":
                    _render_sec += 60.0
                    risk.render_elapsed(_render_sec)

            if list_state != "ready":
                close_tab(target_id)
                progress.update(task, description=f"[red]✗ 列表未渲染: {kw}[/red]")
                console.print(f"[yellow]⚠ 51job 列表页未渲染出岗位（{kw}），跳过本词[/yellow]")
                risk_log.log(2, "list_timeout", keyword=kw, render_seconds=_render_sec,
                             action="stop_keyword", note="列表长时间未渲染")
                pause = backoff.record_error()
                if backoff.should_stop:
                    console.print("[red]⚠ 连续失败过多，停止整个 51job 采集[/red]")
                    db.close()
                    return new_count
                if pause > 0:
                    _wait_or_stop(stop_event, pause)
                continue

            page = 1
            while page <= max_pages and not (stop_event is not None and stop_event.is_set()):
                if limit is not None and new_count >= limit:
                    break
                if _is_waf(target_id):
                    console.print(f"[yellow]⚠ 51job WAF 拦截（{kw} 第{page}页），停止本词[/yellow]")
                    pause = backoff.record_error()
                    if backoff.should_stop:
                        console.print("[red]⚠ 连续风控错误过多，停止整个 51job 采集[/red]")
                        close_tab(target_id)
                        db.close()
                        return new_count
                    if pause > 0:
                        console.print(f"[dim]  退避等待 {int(pause)}s[/dim]")
                        _wait_or_stop(stop_event, pause)
                    break

                # 提取列表（.joblist-item + sensorsdata）
                result = evaluate(target_id, JS_EXTRACT_LIST)
                jobs_list = []
                if result:
                    try:
                        jobs_list = json.loads(result).get("jobs", [])
                    except (json.JSONDecodeError, TypeError):
                        jobs_list = []
                progress.update(task, description=f"51job: {kw} 第{page}页 ({len(jobs_list)}条)")
                report_progress()

                # 处理每张卡片
                for job_data in jobs_list:
                    if stop_event is not None and stop_event.is_set():
                        break
                    if limit is not None and new_count >= limit:
                        break
                    seen_count += 1
                    job_id = str(job_data.get("jobId") or "").strip()
                    if not job_id:
                        continue
                    # 去重（含防二次投递）
                    if job_exists(db, job_id):
                        duplicate_count += 1
                        continue
                    # 标题/公司黑名单
                    if _matching_deal_breaker(job_data.get("title", ""), deal_breakers):
                        continue
                    if _matching_blocked_company(job_data.get("company", ""), blocked_companies):
                        continue

                    # 抓详情页（JD）；返回 (detail, outcome)
                    pace.mark("detail_open", keyword=kw, page=page)
                    detail, outcome = _fetch_detail(job_data, stop_event)
                    # 浏览器连接失败告警（连续 no_tab 说明 Chrome/CDP 断了，不静默跳过）
                    if outcome == "no_tab":
                        no_tab_count += 1
                        if no_tab_count >= 3:
                            console.print("[red]⚠ 连续 3 次无法打开详情页，浏览器连接可能已断，停止采集[/red]")
                            risk_log.log(2, "browser_disconnected", keyword=kw, page=page,
                                         action="stop_all", note="new_tab 连续失败")
                            close_tab(target_id)
                            db.close()
                            return new_count
                        continue
                    # 风控信号聚合：统计详情页结果（offline / ready）
                    if outcome == "offline":
                        risk.detail_offline()
                    elif outcome == "ready":
                        risk.detail_ok()
                    # offline 比例超标 → 疑似降级（连正常岗位都显示"下架"），记录并停止本词
                    if outcome == "offline" and risk.offline_ratio >= OFFLINE_RATIO_STOP:
                        risk_log.log(2, "offline_high", keyword=kw, page=page,
                                     offline_ratio=risk.offline_ratio,
                                     action="stop_keyword",
                                     note="详情页 offline 比例异常（疑似降级，非真下架）")
                        console.print(f"[yellow]⚠ 详情页 offline 比例 {risk.offline_ratio:.0%}（{kw}），疑似风控降级，停止本词[/yellow]")
                        break
                    if detail is None:
                        continue

                    # 详情页 title 提取不准（APP下载/访问验证/审核中）→ 只用列表字段兜底 title，
                    # 但 JD 是正文「职位描述」锚点抓的，独立于 title，不能连带清空
                    detail_title = (detail.get("title") or "").strip()
                    if detail_title and ("APP下载" in detail_title or "访问验证" in detail_title or "审核中" in detail_title or "已下线" in detail_title):
                        detail = {**detail, "title": job_data.get("title", "")}

                    # JD 黑名单（jd_deal_breakers）：命中则跳过不入库（默认留空，与 BOSS 对齐）
                    if _matching_deal_breaker(detail.get("jd", ""), jd_deal_breakers):
                        continue

                    # 薪资范围过滤（profile.salary_min / salary_max，单位 K/月）
                    # 严格语义：岗位最低薪资必须 ≥ 设定最低；岗位最高薪资必须 ≤ 设定最高
                    # 解析失败（面议等）不拦截（保守，避免漏抓）
                    _salary_text = detail.get("salary") or job_data.get("salary", "")
                    if (salary_min > 0 or salary_max > 0):
                        _salary_range = _parse_salary_range(_salary_text)
                        if _salary_range is not None:
                            _job_min, _job_max = _salary_range
                            if salary_min > 0 and _job_min < salary_min:
                                continue
                            if salary_max > 0 and _job_max > salary_max:
                                continue

                    insert_job(db, {
                        "id": job_id,
                        "title": (detail.get("title") or job_data.get("title", "")).strip(),
                        "company": (detail.get("company") or job_data.get("company", "")).strip(),
                        "salary": detail.get("salary") or job_data.get("salary", ""),
                        "city": detail.get("city") or job_data.get("city") or city,
                        "experience": job_data.get("experience", ""),
                        "jd": detail.get("jd", ""),
                        "hr_name": "",
                        "hr_title": "",
                        "hr_active": "",
                        "company_size": job_data.get("size", ""),
                        "company_industry": job_data.get("industry", ""),
                        "url": job_data.get("href", ""),
                        "source": "51job",
                    })
                    if collected_job_ids is not None:
                        collected_job_ids.append(job_id)
                    new_count += 1
                    keyword_new += 1
                    report_progress()

                # 末页判定（btn-next disabled）
                nbd = evaluate(target_id, JS_NEXT_DISABLED)
                at_last = (nbd is True) or page >= HARD_MAX_PAGES
                if at_last or page >= max_pages:
                    break

                # 翻页前随机小幅滚动（模拟真人浏览后翻页）
                if BROWSE_BEFORE_TURN:
                    scroll(target_id, y=random.randint(200, 800))
                    _wait_or_stop(stop_event, random.uniform(0.5, 1.5))

                # 点 btn-next 翻页
                clicked = evaluate(target_id, JS_CLICK_NEXT)
                if clicked:
                    pace.mark("page_turn", keyword=kw, page=page + 1)
                if not clicked:
                    # 重试一次（按钮可能因渲染时序暂不可点）
                    _wait_or_stop(stop_event, 2.0)
                    clicked = evaluate(target_id, JS_CLICK_NEXT)
                    if not clicked:
                        console.print(f"[yellow]⚠ 翻页失败（{kw} 第{page}页），停止本词[/yellow]")
                        pause = backoff.record_error()
                        if backoff.should_stop:
                            console.print("[red]⚠ 连续翻页失败过多，停止整个 51job 采集[/red]")
                            close_tab(target_id)
                            db.close()
                            return new_count
                        if pause > 0:
                            console.print(f"[dim]  退避等待 {int(pause)}s[/dim]")
                            _wait_or_stop(stop_event, pause)
                        break
                else:
                    backoff.record_success()
                page += 1

                # ★ 关键：轮询等待新一页真正渲染完成（页码切到 page 且列表卡片已渲染），再进行提取
                if not _wait_page_ready(target_id, stop_event, page, timeout=90.0):
                    console.print(f"[yellow]⚠ 翻页后页面未渲染（{kw} 第{page}页），停止本词[/yellow]")
                    pause = backoff.record_error()
                    if backoff.should_stop:
                        console.print("[red]⚠ 连续翻页失败过多，停止整个 51job 采集[/red]")
                        close_tab(target_id)
                        db.close()
                        return new_count
                    if pause > 0:
                        _wait_or_stop(stop_event, pause)
                    break

                # ★ 拟人化翻页节奏（额外叠加 + 随机穿插冷却）
                _turn_pacer.pause(stop_event)

            close_tab(target_id)
            progress.update(task, description=f"51job: {kw} (新增 {keyword_new})")
            # 词结束：重置 risk 的 per-keyword 计数
            risk.reset_keyword()
            # 断点续采：标记本关键词已完成（下次启动跳过）
            mark_combo_collected(db, "51job", city, kw)
            # 拟人化词间节奏（额外叠加 + 随机穿插冷却）
            _keyword_pacer.pause(stop_event)

    db.close()
    report_progress()
    # 采集结束汇总事件（写入风控日志，独立于 console 输出，避免 Rich Progress 活跃时 print 冲突）
    risk_log.log(0, "collect_end", note=f"新增 {new_count}，去重 {duplicate_count}，共见 {seen_count}")
    return new_count


def _is_waf(target_id: str) -> bool:
    return evaluate(target_id, JS_WAF) is True


def _wait_list_ready(target_id: str, stop_event, timeout: float = 90.0) -> str:
    """轮询等待列表页 .joblist-item 真正渲染出来（SPA 二次渲染，不能只看 readyState）。

    返回 'ready'（已渲染）/ 'waf'（被验证页拦截）/ 'timeout'（超时）。
    51job 列表接口偶发响应慢（实测 15s~60s+，风控后更慢），超时放宽到 90s。
    轮询过程中偶尔轻滚辅助触发懒加载（真人滚动行为），但不频繁打断渲染。
    """
    deadline = time.time() + timeout
    tick = 0
    while time.time() < deadline:
        if stop_event is not None and stop_event.is_set():
            return "stopped"
        state = evaluate(target_id, JS_LIST_READY)
        if state == "ready":
            return "ready"
        if state == "waf":
            return "waf"
        tick += 1
        # 每 10 次（约 10s）轻滚一次辅助触发渲染（真人滚动），低频不打断
        if tick % 10 == 0:
            try:
                scroll(target_id, y=random.randint(300, 700))
            except Exception:
                pass
        _wait_or_stop(stop_event, 1.0)
    return "timeout"


def _wait_page_ready(target_id: str, stop_event, expect_page: int, timeout: float = 90.0) -> bool:
    """翻页后轮询等待新一页渲染完成：当前页码 == expect_page 且列表卡片已渲染。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if stop_event is not None and stop_event.is_set():
            return False
        cur = evaluate(target_id, JS_CUR_PAGE)
        ready = evaluate(target_id, JS_LIST_READY)
        # 页码已切到目标页，且列表已渲染出卡片
        if cur == expect_page and ready == "ready":
            return True
        if ready == "waf":
            return False
        _wait_or_stop(stop_event, 1.0)
    return False


# 详情页「职位描述」是否已渲染（readyState=complete 不代表正文渲染完；中文用 fromCharCode）
# 增加「下架/审核中」识别：51job 下架页显示"当前职位审核中或已下线"，立即返回 offline 不再空等
JS_JD_READY = r"""
(function () {
    var b = document.body.innerText || '';
    var JD_ANCHOR = String.fromCharCode(32844, 20301, 25551, 36848);   // 职位描述
    var VERIFY = String.fromCharCode(35775, 38382, 39564, 35777);       // 访问验证
    var OFFLINE = String.fromCharCode(23457, 26680, 20013, 25110, 24050, 19979, 32447);  // 审核中或已下线
    if (b.indexOf(VERIFY) !== -1) return 'waf';
    if (b.indexOf(OFFLINE) !== -1) return 'offline';
    if (b.indexOf(JD_ANCHOR) !== -1) return 'ready';
    return 'loading';
})()
"""


def _fetch_detail(job_data: dict, stop_event) -> tuple[dict | None, str]:
    """打开 51job 详情页抓完整 JD。

    返回 (detail, outcome)：
    - detail：抓到详情 dict（含 jd）；不可用时为 None
    - outcome：结果类型 'ready' / 'offline' / 'waf' / 'timeout' / 'stopped' / 'no_tab' / 'no_href'

    outcome 通过返回值携带（不用模块级全局变量），供 RiskDetector 统计 offline 比例。
    """
    href = job_data.get("href", "")
    if not href:
        return None, "no_href"
    # 详情页分簇节奏：簇内快（20~28s）+ 簇间长停（60~480s），模拟真人"连续看几个然后歇一下"
    _detail_pacer.pause(stop_event)
    if stop_event is not None and stop_event.is_set():
        return None, "stopped"
    detail_target = new_tab(href, background=True)
    if not detail_target:
        return None, "no_tab"
    try:
        # 轮询等待「职位描述」正文渲染（最多等 35s，每 1s 查一次；慢网下详情页渲染慢）
        state = "loading"
        for _ in range(35):
            if stop_event is not None and stop_event.is_set():
                return None, "stopped"
            _wait_or_stop(stop_event, 1.0)
            state = evaluate(detail_target, JS_JD_READY)
            if state in ("ready", "waf", "offline"):
                break
        # 被 WAF 拦 / 下架 / 超时未渲染出 JD → 返回 None（不入库）
        if state != "ready":
            return None, (state if state in ("waf", "offline") else "timeout")
        result = evaluate(detail_target, JS_EXTRACT_DETAIL)
        if not result:
            return None, "timeout"
        try:
            return json.loads(result), "ready"
        except (json.JSONDecodeError, TypeError):
            return None, "timeout"
    finally:
        close_tab(detail_target)
        _detail_pacer.mark_done()
