# -*- coding: utf-8 -*-
"""
harness/ingest/search.py —— 搜索服务网关占位（S2-03/04）

设计（B4 阶段只做"未配置即禁用"的边界与记账字段，不接任何具体服务商）：
- SEARCH_PROVIDER 未配置（默认）→ 搜索明确禁用：任何入口都得到可操作的
  SearchNotConfigured 提示，绝不静默退回“假装搜过”或改用 Mock 数据；
- 配置后由实施按官方文档核验接口/费用（S2-04）再把具体 provider 接入；
- 每次搜索调用预留 S2-04/S4-10 记账字段（root_job_id、query、ranked结果、
  已知/未知费用、耗时），未知费用显式标记 unknown，不记零。

本模块不发起任何网络请求（未配置 provider）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
import re


class SearchNotConfigured(ValueError):
    """搜索服务未配置：任务应明确告知用户，而不是用假结果继续。"""


class MockSearchInRealMode(ValueError):
    """真实模式不得使用模拟搜索：宁可不搜，也不能把模拟结果当真实资料。"""


class SearchError(RuntimeError):
    """真实搜索失败（被反爬拦截/无结果/解析失败）：明确报错，绝不假装搜过。"""


class RealSearchInMockMode(ValueError):
    """模拟（离线）模式不发起真实网络搜索：保持 Mock 离线确定性。"""


# D3-01（用户指令 2026-09-11 变更）：搜索先行采用"结果页抓取"（bing_scrape，
# 用现有 fetcher 直抓 Bing 结果页并解析，零新依赖零 Key；反爬升级时明确报错）。
# 百度官方 API 保留为后续稳定化选项（原 D3-01 路径不删除）。
SUPPORTED_PROVIDERS: tuple[str, ...] = ("mock", "bing_scrape")
REAL_PROVIDERS: tuple[str, ...] = ("bing_scrape",)
CONFIG_KEYS = ("SEARCH_PROVIDER", "SEARCH_API_KEY", "SEARCH_BASE_URL",
               "SEARCH_MAX_RESULTS")


def search_enabled(search_provider: str = "") -> bool:
    return bool((search_provider or "").strip())


def check_configured(provider: str, settings) -> None:
    """调用前检查；provider 空或不是已实现的服务商一律明确失败。"""
    provider = (provider or "").strip().lower()
    if not provider:
        raise SearchNotConfigured(
            "搜索服务未配置：请在 .env 设置 SEARCH_PROVIDER / SEARCH_API_KEY；"
            "未配置时本任务不会搜索，仅使用用户提供的资料与链接")
    if provider not in SUPPORTED_PROVIDERS:
        raise SearchNotConfigured(
            f"搜索服务 {provider} 尚未接入：由实施按官方接口与费用核验后接入（S2-03/04）")


def ensure_provider_allowed(provider: str, run_mode: str = "mock") -> None:
    """模式红线：模拟搜索只允许在 Mock/离线模式使用，真实模式明确拒绝。"""
    if (provider or "").strip().lower() == "mock" and run_mode == "real":
        raise MockSearchInRealMode(
            "真实模式禁止使用模拟搜索（mock provider）："
            "真实任务请配置真实搜索服务或改为仅用给定资料")


def ensure_provider_mode(provider: str, run_mode: str = "mock") -> None:
    """模式红线：mock 只许离线用；真实抓取/搜索提供方只在真实模式发起网络请求。"""
    provider = (provider or "").strip().lower()
    if provider == "mock" and run_mode == "real":
        raise MockSearchInRealMode(
            "真实模式禁止使用模拟搜索（mock provider）："
            "真实任务请配置真实搜索服务或改为仅用给定资料")
    if provider in REAL_PROVIDERS and run_mode == "mock":
        raise RealSearchInMockMode(
            f"模拟模式不发起真实网络搜索（{provider}）："
            "Mock 保持离线确定性；真实联网请使用真实模式并显式允许联网")


def run_search(provider: str, query: str, *, max_results: int = 8,
               fetch=None) -> list:
    """按提供方执行一次真实搜索；失败抛 SearchError，绝不返回假结果。"""
    provider = (provider or "").strip().lower()
    if provider == "mock":
        return mock_search(query, max_results=max_results)
    if provider == "bing_scrape":
        return bing_scrape_search(query, max_results=max_results, fetch=fetch)
    raise SearchNotConfigured(f"搜索服务 {provider} 尚未接入")


# ---- D3-01：bing_scrape（Bing 结果页抓取）-----------------------------------

_BING_SEARCH_URL = "https://cn.bing.com/search?q={query}"


def bing_scrape_search(query: str, *, max_results: int = 8, fetch=None) -> list:
    """抓取 Bing 结果页并解析出候选（真实网络请求；被拦/无结果明确报错）。"""
    from urllib.parse import quote

    from src.harness.ingest.fetcher import fetch_url

    query = (query or "").strip()
    if not query:
        raise SearchError("搜索词为空")
    max_results = max(1, min(int(max_results or 8), 10))
    fetch_fn = fetch or fetch_url
    result = fetch_fn(_BING_SEARCH_URL.format(query=quote(query)))
    if getattr(result, "status", "") != "ok":
        raise SearchError(f"Bing 结果页抓取失败：status={getattr(result, 'status', '?')}"
                          f" {getattr(result, 'error', '')}")
    html = (getattr(result, "raw", b"") or b"").decode("utf-8", errors="ignore")
    if "b_algo" not in html:
        raise SearchError("Bing 结果页未包含结果条目（可能被反爬拦截或改版）："
                          "明确失败，不使用缓存或猜测结果")
    candidates = parse_bing_results(html, max_results=max_results)
    if not candidates:
        raise SearchError("Bing 结果页解析出 0 条候选（结构可能已改版）：明确失败")
    return candidates


def _extract_published_date(snippet: str) -> tuple[str, str]:
    """从可见摘要提取日期；不猜测时明确返回 unavailable。"""
    text = (snippet or "").strip()
    patterns = (
        re.compile(r"(?<!\d)(20\d{2})年(\d{1,2})月(\d{1,2})日"),
        re.compile(r"(?<!\d)(20\d{2})-(\d{1,2})-(\d{1,2})(?!\d)"),
    )
    for pattern in patterns:
        match = pattern.search(text)
        if match:
            year, month, day = match.groups()
            return f"{int(year):04d}-{int(month):02d}-{int(day):02d}", "snippet"
    return "", "unavailable"


def parse_bing_results(html: str, *, max_results: int = 8) -> list:
    """解析 Bing 结果页（b_algo 条目 → 标题/链接/摘要/排名/日期）；标准库实现，容忍结构小变化。"""
    from html.parser import HTMLParser

    class _Parser(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.results = []
            self._algo_depth = 0
            self._current = None
            self._in_title_link = False
            self._in_snippet = False
            self._in_h2 = False

        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            classes = attrs.get("class", "")
            if tag == "li" and "b_algo" in classes:
                self._algo_depth += 1
                self._current = {"title": "", "url": "", "snippet": ""}
                return
            if not self._algo_depth or self._current is None:
                return
            if tag == "h2":
                self._in_h2 = True
                return
            if tag == "a" and attrs.get("href"):
                if self._in_h2:
                    # h2 内的锚是标题链接：URL 与标题以它为准（覆盖站点面包屑兜底锚）
                    self._current["url"] = attrs["href"]
                    self._in_title_link = True
                elif not self._current["url"]:
                    self._current["url"] = attrs["href"]
            elif tag == "p" and not self._current["snippet"]:
                self._in_snippet = True

        def handle_data(self, data):
            if self._current is None:
                return
            if self._in_title_link:
                self._current["title"] += data
            elif self._in_snippet:
                self._current["snippet"] += data

        def handle_endtag(self, tag):
            if tag == "a" and self._in_title_link:
                self._in_title_link = False
            elif tag == "h2" and self._in_h2:
                self._in_h2 = False
            elif tag == "p" and self._in_snippet:
                self._in_snippet = False
            elif tag == "li" and self._algo_depth:
                self._algo_depth -= 1
                cur = self._current
                self._current = None
                url = (cur.get("url") or "").strip()
                title = (cur.get("title") or "").strip()
                if url.startswith(("http://", "https://")) and title:
                    snippet = (cur.get("snippet") or "").strip()
                    published_date, date_source = _extract_published_date(snippet)
                    self.results.append(SearchResult(
                        title=title, url=url, snippet=snippet,
                        rank=len(self.results) + 1,
                        published_date=published_date, date_source=date_source,
                        mock=False))

    parser = _Parser()
    parser.feed(html)
    return parser.results[:max(1, min(int(max_results or 8), 10))]


# ---- D3-03：检索相关性过滤（Q3-02 联网专项）--------------------------------
# 实测（eval/reports/q2_web_baseline.json，12 题真基线）：长句查询在 cn.bing.com 上
# 会"退化匹配"——只命中查询里的某个泛词，返回「混合（汉语词语）_百度百科」
# 「generate - 搜索 词典」「2026年日历…放假安排」这类页面；12 题里有 6 题因此拿不到
# 任何相关来源而只能 unable。这里做两件事：查询改成短关键词（见 QUERY_PLANNER_PROMPT），
# 结果按关键词重合度 + 页面类型过滤，绝不把词典/日历/下载页当研究来源。
_GENERIC_TERMS = (
    "研究", "现状", "趋势", "分析", "报告", "影响", "应用", "发展", "情况", "问题",
    "主要", "相关", "模式", "方案", "对比", "评估", "设计", "系统", "方法", "进展",
)
_STOP_CHARS = "的与和及对在为是了把被将以之其这那有并或等把从到"
_QUERY_FILTER_RE = re.compile(r"^(?:site|after):", re.IGNORECASE)
_LEXICON_TITLE = re.compile(
    r"(汉语词语|汉语汉字|汉语文字|汉典|词典|拼音|部首|笔画|笔顺|组词|造句|的意思|是什么意思"
    r"|_翻译|翻译[—\-]|音标|读音|近义词|反义词|维基词典|在线翻译|百科$|_百度百科)")
_CALENDAR_TITLE = re.compile(r"(日历|放假|调休|节假日|万年历|节日一览|补班)")
_LANDING_TITLE = re.compile(r"(官网|下载|免费试用|立即购买|优惠|客服|注册|登录|首页)")


def query_terms(query: str) -> list[str]:
    """查询关键词：ASCII 词（≥2 字符）+ 中文二元组（无分词依赖的近似口径）。

    去掉 site:/after: 过滤条件、含停用字的二元组与「研究/现状」这类泛词，
    避免泛词把不相干页面算成"相关"。
    """
    text = " ".join(part for part in (query or "").split()
                    if not _QUERY_FILTER_RE.match(part))
    terms: list[str] = []
    for token in re.findall(r"[A-Za-z][A-Za-z0-9+#.\-]{1,}", text):
        terms.append(token.lower())
    for block in re.findall(r"[\u4e00-\u9fff]+", text):
        if len(block) == 1:
            terms.append(block)
            continue
        for index in range(len(block) - 1):
            bigram = block[index:index + 2]
            if any(ch in _STOP_CHARS for ch in bigram):
                continue
            if bigram in _GENERIC_TERMS:
                continue
            terms.append(bigram)
    return list(dict.fromkeys(terms))


def result_relevance(query: str, title: str, snippet: str) -> float:
    """0~1：查询关键词在标题/摘要里的覆盖度（标题命中权重 1.0，仅摘要命中 0.5）。"""
    terms = query_terms(query)
    if not terms:
        return 0.0
    title_text = (title or "").lower()
    snippet_text = (snippet or "").lower()
    score = 0.0
    for term in terms:
        if term in title_text:
            score += 1.0
        elif term in snippet_text:
            score += 0.5
    return min(1.0, score / len(terms))


def filter_search_results(query: str, results: list,
                          min_relevance: float = 0.2,
                          fallback_keep: int = 2) -> tuple[list, list[dict]]:
    """按相关性过滤候选；返回 (保留结果, 丢弃说明)。

    保留：关键词重合度达标的条目（以及词典/日历/落地页规则之外的相关条目）。
    丢弃说明逐条给出理由与重合度，便于人工复核与报告呈现。
    安全阀：整组都不达标时按重合度保留前 `fallback_keep` 条（并在丢弃说明里标
    `fallback=True`），避免"一条都不留"让某个查询彻底落空；上层据此如实记录。
    """
    scored: list[tuple[float, object, str, bool]] = []
    query_text = query or ""
    if not query_terms(query_text):
        # 查询里没有可用关键词（全是泛词/停用字）：无法判断相关性，不做过滤
        return list(results or []), []
    wants_calendar = bool(re.search(r"(日历|放假|调休|节假日|万年历)", query_text))
    for result in results or []:
        title = str(getattr(result, "title", "") or "")
        snippet = str(getattr(result, "snippet", "") or "")
        relevance = result_relevance(query_text, title, snippet)
        reason = ""
        rescuable = False
        if _LEXICON_TITLE.search(title) and relevance < 0.6:
            reason = "词典/字词释义页，非研究材料"
        elif _CALENDAR_TITLE.search(title) and not wants_calendar:
            reason = "日历/节假日页，与主题无关"
        elif _LANDING_TITLE.search(title) and relevance < 0.5:
            reason = "产品官网/下载页，非研究材料"
        elif relevance < min_relevance:
            reason = "查询关键词重合不足"
            rescuable = True            # 只是重合度低：可能是英文/同义页面，允许安全阀保留
        scored.append((relevance, result, reason, rescuable))
    kept = [result for relevance, result, reason, _ in scored if not reason]
    dropped: list[dict] = []
    if not kept and fallback_keep > 0:
        # 安全阀：整组都不达标时保留重合度最高的少数条目（只救"重合不足"，
        # 不救词典/日历/下载页这类明显非研究材料），避免某个查询彻底落空。
        rescued = sorted((row for row in scored if row[3]),
                         key=lambda row: row[0], reverse=True)[:fallback_keep]
        for row in rescued:
            relevance, result, reason, _ = row
            kept.append(result)
            dropped.append({"title": str(getattr(result, "title", "") or "")[:80],
                            "url": str(getattr(result, "url", "") or ""),
                            "relevance": round(relevance, 3),
                            "reason": f"{reason}（按安全阀保留，待人工复核）",
                            "fallback": True})
            scored.remove(row)
    for relevance, result, reason, _ in scored:
        if not reason:
            continue
        dropped.append({"title": str(getattr(result, "title", "") or "")[:80],
                        "url": str(getattr(result, "url", "") or ""),
                        "relevance": round(relevance, 3), "reason": reason})
    return kept, dropped


# ---- D3-02：有界查询规划 -----------------------------------------------------

QUERY_PLANNER_PROMPT = (
    "你是检索规划器。把研究主题拆成 2~4 个互补的搜索查询词，供搜索引擎使用。"
    "要求：① 每个查询是 2~4 个关键词、用空格分隔的短查询——像真人在搜索框里输入，"
    "不要整句、不要标点、不要只用年份或时间；"
    "② 关键词选主题里最有区分度的专有名词/术语（政策与法规名、机构名、产品名、"
    "行业术语），不要用「研究/现状/趋势/影响/分析/应用」这类泛词单独成词；"
    "③ 各查询覆盖不同子问题，不要同义重复。"
    "只输出 JSON：{\"queries\":[\"关键词 关键词\",\"关键词 关键词\"]}，不要编号与解释。")


def _apply_query_filters(query: str, *, site: str = "", since: str = "") -> str:
    """把可解释的站点/起始日期条件附加到查询；参数错误显式拒绝。"""
    query = (query or "").strip()
    site = (site or "").strip().lower()
    since = (since or "").strip()
    if site and any(ch.isspace() for ch in site):
        raise ValueError("site必须为不含空白的域名或站点条件")
    if since:
        from datetime import date
        try:
            date.fromisoformat(since)
        except ValueError:
            raise ValueError("since必须为YYYY-MM-DD") from None
    filters = []
    if site:
        filters.append(f"site:{site}")
    if since:
        filters.append(f"after:{since}")
    return " ".join([query, *filters]).strip()


def plan_queries(topic: str, llm=None, *, max_queries: int = 4,
                 site: str = "", since: str = "") -> list[str]:
    """有界查询规划（D3-02）：拆分 2~4 个查询，并可附加站点/起始日期过滤。"""
    topic = (topic or "").strip()
    if not topic:
        return []
    filtered_topic = _apply_query_filters(topic, site=site, since=since)
    if llm is None:
        return [filtered_topic][:max_queries]
    from src.harness.model_gateway import model_call
    from src.harness.structured import extract_json

    messages = [
        {"role": "system", "content": QUERY_PLANNER_PROMPT},
        {"role": "user", "content": f"研究主题：{topic}\n请输出检索查询 JSON。"},
    ]
    try:
        reply = model_call(llm, messages, purpose="search_planning", role="planner")
        data = extract_json(getattr(reply, "content", "") or "")
        queries = [str(q).strip() for q in (data or {}).get("queries", [])
                   if str(q).strip()]
        if not queries:
            raise ValueError("空查询清单")
    except Exception:  # noqa: BLE001 —— 规划失败退回单查询，不阻塞搜索
        return [filtered_topic][:max_queries]
    deduped = list(dict.fromkeys(queries))[:max(1, min(max_queries, 4))]
    return [_apply_query_filters(q, site=site, since=since) for q in deduped] or [filtered_topic]


@dataclass
class SearchResult:
    """一条搜索候选；snippet 只作检索摘要，不能当作已读正文或证据。"""
    title: str
    url: str
    snippet: str
    rank: int = 0
    published_date: str = ""
    date_source: str = "unavailable"
    mock: bool = False


def mock_search(query: str, max_results: int = 5) -> list[SearchResult]:
    """确定性模拟搜索（S8 模拟先行）：不发起任何网络请求，结果逐条带 mock 标记。

    只用于离线开发与评测：打通"拆检索问题 → 搜索 → 读原文 → 去重 → 证据"的
    链路逻辑与记账字段；产物若引用模拟结果必须带"模拟搜索"标记（诚实红线）。
    """
    query = (query or "").strip()
    max_results = max(1, min(int(max_results or 1), 10))
    return [SearchResult(
        title=f"[模拟搜索] 「{query}」候选 {i + 1}",
        url=f"https://mock.example/search?q={query}&rank={i + 1}",
        snippet="（模拟搜索结果，不含真实事实；仅用于离线链路验证，"
                "不可作为研究证据引用）",
        rank=i + 1, published_date="", date_source="mock", mock=True,
    ) for i in range(max_results)]


@dataclass
class SearchRecord:
    """一次搜索调用的账本记录骨架（S2-04：付费搜索计入根账本）。"""
    query: str
    provider: str
    root_job_id: str = ""
    max_results: int = 5
    started_at: str = ""
    elapsed_seconds: float = 0.0
    estimated_cost_usd: float | None = None   # 无法估算时保持 None=未知，不记零
    cost_known: bool = False
    urls: list[str] = field(default_factory=list)
    error: str = ""
    mock: bool = False   # S8 模拟先行：模拟搜索的调用也记账，且显式带 mock 标记

    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in (
            "query", "provider", "root_job_id", "max_results", "started_at",
            "elapsed_seconds", "estimated_cost_usd", "cost_known", "urls", "error",
            "mock")}
