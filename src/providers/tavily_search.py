"""Tavily 搜索适配器：定向检索权威站点，作为涉诉/被执行/工商信息的跨域补充源。

为什么用它
----------
裁判文书网、执行信息公开网等官方站点反爬严格，纯 HTTP 往往拿不到结果；
而人民法院公告网这类站点又需要真实渲染才出数据。Tavily（tavily.com）是面向
LLM/RAG 的搜索 API，返回结构化结果（title/url/content/score），支持
``include_domains`` 精确限定站点、``search_depth=advanced`` 深度检索——
把它接在涉诉/执行/工商域下，用 ``include_domains`` 圈定官方域名，可以补上
反爬站点拿不到的公开线索（判决书标题、执行公告、工商变更新闻等）。

接口
----
``POST https://api.tavily.com/search``::

    {"query": "...", "search_depth": "advanced", "max_results": N,
     "include_domains": ["wenshu.court.gov.cn", ...], "include_raw_content": false}

鉴权：新接口用 ``Authorization: Bearer tvly-xxx``，旧接口也接受 body 里的 ``api_key``，
因此这里**两处都带**（与 apizero 的双通道写法一致）。

返回::

    {"results": [{"title": "...", "url": "...", "content": "...", "score": 0.9}]}

登记方式（config.yaml）
----------------------
同一个类按类目重复登记，各自给不同 params：``query``（含 ``{name}`` 占位符）、
``include_domains``、``event_category``（事件归入哪个域）。未配置 api_key 时抛
``RuntimeError`` 由调度器跳过，不影响其它源。

实测结论（两个主体 × 多组参数，2026-10）
------------------------------------------
1. **query 只写企业全名（``"{name}"``）精度最高**。加「被执行/失信/工商变更」
   等关键词后，Tavily 的语义排序会偏向"关键词页面"而非"该企业页面"，
   命中该企业的结果反而降到 0。
2. ``include_domains`` 限定到工商平台（qcc/天眼查/爱企查/公示网）能稳定拿到
   该企业的档案页；限定到法院域名（wenshu/zxgk/rmfygg）则**拿不到结果**——
   Tavily 索引对法院站覆盖有限。故涉诉/被执行交给 rmfygg（tier 1、免 Key、
   无限次），本适配器默认只登记在 ``biz_change``。
与本项目的相关性过滤（``core.relevance``，strict）配合：命中的结果由同步层
按企业全名/核心词二次过滤，落库的才是真正与该企业相关的条目。
"""
try:
    from src.core.models import Event, Enterprise
    from src.core.relevance import relevance_of
    from src.providers.base import ProviderBase
except ImportError:
    from ..core.models import Event, Enterprise
    from ..core.relevance import relevance_of
    from .base import ProviderBase

API_URL = "https://api.tavily.com/search"

# 涉诉/执行类：命中即高危；工商/新闻类：按提示性关键词判定
_HIGH_WORDS = ("被执行", "失信", "查封", "冻结", "拍卖", "变卖", "破产", "限高",
               "限制消费", "强制执行", "执行款", "清算")
_MED_WORDS = ("诉讼", "起诉", "开庭", "传票", "判决", "裁定", "调解", "上诉",
              "纠纷", "注销", "吊销", "经营异常", "行政处罚", "欠税", "股权变更",
              "法定代表人变更", "立案")
_NEGATIVE = ("被执行", "失信", "查封", "冻结", "破产", "注销", "吊销", "经营异常",
             "行政处罚", "欠税", "违法", "纠纷", "起诉", "诉讼")


def classify(text: str) -> tuple:
    """按关键词判定 ``(severity, sentiment)``。"""
    t = text or ""
    if any(w in t for w in _HIGH_WORDS):
        return "high", "negative"
    if any(w in t for w in _MED_WORDS):
        return "medium", ("negative" if any(w in t for w in _NEGATIVE) else "neutral")
    return "low", ("negative" if any(w in t for w in _NEGATIVE) else "neutral")


def render_query(template: str, ent_name: str) -> str:
    """把 ``{name}`` 占位符替换为企业全名；无占位符则原样追加企业名。"""
    tmpl = (template or "").strip()
    if not tmpl:
        return f'"{ent_name}"'
    if "{name}" in tmpl:
        return tmpl.replace("{name}", ent_name)
    return f"{tmpl} {ent_name}"


def parse_results(payload: dict, ent_name: str, event_category: str) -> list:
    """把 Tavily 返回体归一化为事件列表（纯函数，便于单测）。"""
    out = []
    for r in (payload or {}).get("results") or []:
        if not isinstance(r, dict):
            continue
        title = str(r.get("title") or "").strip()
        content = str(r.get("content") or "").strip()
        if not title and not content:
            continue
        severity, sentiment = classify(f"{title} {content}")
        out.append(Event(
            category=event_category or "news",
            title=title or content[:60],
            summary=content[:300],
            url=str(r.get("url") or ""),
            source="tavily",
            occurred_at="",  # Tavily 不返回发布时间，交由人工/AI 研判
            severity=severity, sentiment=sentiment,
            relevance=relevance_of(title, content, ent_name)))
    return out


class TavilyProvider(ProviderBase):
    name = "tavily"
    category = "lawsuit"

    def fetch(self, ent: Enterprise) -> list:
        api_key = (self.params.get("api_key") or "").strip()
        if not api_key:
            raise RuntimeError("Tavily api_key 未配置，跳过该源")
        body = {
            "query": render_query(self.params.get("query") or "", ent.name),
            "search_depth": self.params.get("search_depth") or "advanced",
            "max_results": int(self.params.get("max_results") or 5),
            "include_raw_content": bool(self.params.get("include_raw_content", False)),
        }
        domains = self.params.get("include_domains") or []
        if domains:
            body["include_domains"] = list(domains)
        headers = self.headers()
        headers["Authorization"] = f"Bearer {api_key}"
        headers["Content-Type"] = "application/json"
        body["api_key"] = api_key  # 兼容旧接口（body 传 key）
        payload = self._json_post(API_URL, json=body, headers=headers)
        return parse_results(payload, ent.name,
                             self.params.get("event_category") or self.category)
