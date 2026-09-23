"""本地规则研判引擎（AI 降级备用，不调用任何外部模型）。

词库命中打分 + 来源可信度加权，输出与 AI 研判同构的结果 dict，
供 agents / queue_worker 在 AI 不可用时兜底。
"""

# 词库：负面高危词 → severity=high
HIGH_WORDS = ("被执行", "失信", "查封", "冻结", "破产", "立案", "处罚", "退市",
              "欠税", "强制执行", "注销", "经营异常", "罚款", "吊销", "限制消费")
# 词库：中危词 → severity=medium
MEDIUM_WORDS = ("诉讼", "仲裁", "纠纷", "被告", "股权变更", "法定代表人变更",
                "减持", "质押", "欠薪", "开庭", "判决", "裁定")
# 词库：正面词 → sentiment=positive
POSITIVE_WORDS = ("中标", "签约", "获奖", "增资", "扩产", "上市", "回购", "战略合作")

# 来源可信度权重表（court/zxgk 类官方源高于普通新闻源）
SOURCE_WEIGHTS = {
    "zxgk": 0.95,            # 全国法院执行信息公开网
    "wenshu": 0.90,          # 中国裁判文书网
    "court": 0.90,           # 其他法院类源
    "gsxt": 0.85,            # 国家企业信用信息公示系统
    "aiqicha": 0.75,         # 爱企查（工商）
    "commercial_api": 0.75,  # 商业工商数据 API
    "baidu_news": 0.50,
    "sogou_news": 0.50,
    "newsapi": 0.50,
    "rss": 0.45,
    "istero_pengpai": 0.40,
}
DEFAULT_SOURCE_WEIGHT = 0.5

# 降级结果统一前缀（agents / queue_worker 共用）
DEGRADED_PREFIX = "[AI 不可用，自动化脚本结果]"

# 股权类工商变更关键词（用于 biz_change 相关性与严重度判断）
_EQUITY_WORDS = ("股权", "股东", "法定代表人", "转让", "增资", "减持", "质押")


def _hit(text: str, words) -> list:
    return [w for w in words if w in text]


def _source_weight(source: str) -> float:
    s = (source or "").lower()
    for k, w in SOURCE_WEIGHTS.items():
        if k in s:
            return w
    return DEFAULT_SOURCE_WEIGHT


def _cred_label(weight: float) -> str:
    if weight >= 0.8:
        return "来源可信度较高"
    if weight >= 0.5:
        return "来源可信度一般"
    return "来源可信度较低"


def assess_by_rule(title: str, summary: str, source: str, category: str,
                   ent_name: str = "") -> dict:
    """规则研判：返回 {sentiment, severity, relevance, verdict}。

    relevance：enforcement / biz_change(股权类) 默认 high，lawsuit medium，
    news 由标题/摘要是否含企业全名判断（未提供企业名时取 medium）。
    """
    text = f"{title or ''} {summary or ''}"
    high_hits = _hit(text, HIGH_WORDS)
    med_hits = _hit(text, MEDIUM_WORDS)
    pos_hits = _hit(text, POSITIVE_WORDS)
    weight = _source_weight(source)
    equity_like = any(w in text for w in _EQUITY_WORDS)

    # 情感：负面词优先，正面词仅在无负面命中时生效
    if high_hits or med_hits:
        sentiment = "negative"
    elif pos_hits:
        sentiment = "positive"
    elif category == "enforcement":
        sentiment = "negative"
    else:
        sentiment = "neutral"

    # 严重度：高危词或执行类来源 → high；中危词或涉诉类 → medium
    if high_hits or category == "enforcement":
        severity = "high"
    elif med_hits or category == "lawsuit":
        severity = "medium"
    elif category == "biz_change" and equity_like:
        severity = "medium"
    else:
        severity = "low"

    # 相关性
    if category == "enforcement":
        relevance = "high"
    elif category == "biz_change":
        relevance = "high" if equity_like else "medium"
    elif category == "lawsuit":
        relevance = "medium"
    elif ent_name:
        relevance = "high" if ent_name in text else "low"
    else:
        relevance = "medium"

    # verdict：一句话规则摘要
    if high_hits:
        head = f"命中高危词[{'、'.join(high_hits[:3])}]"
    elif med_hits:
        head = f"命中中危词[{'、'.join(med_hits[:3])}]"
    elif pos_hits:
        head = f"命中正面词[{'、'.join(pos_hits[:3])}]"
    elif category == "enforcement":
        head = "执行/法院类来源命中"
    else:
        head = "未命中风险关键词"
    if severity == "high":
        tip = "建议人工核实执行标的金额" if category == "enforcement" else "建议立即人工核实风险细节"
    elif severity == "medium":
        tip = "建议持续关注后续进展"
    elif sentiment == "positive":
        tip = "属利好信息，常规关注即可"
    else:
        tip = "常规关注即可"
    verdict = f"{head}，{_cred_label(weight)}，{tip}"
    return {"sentiment": sentiment, "severity": severity, "relevance": relevance, "verdict": verdict}
