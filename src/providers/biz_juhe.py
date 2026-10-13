"""聚合数据（juhe.cn）企业工商信息适配器。

接口：《企业工商信息基础版》https://apis.juhe.cn/entpriseBase/query
- key 申请：juhe.cn 注册实名 → 搜索"企业工商信息基础版"（ID 515）→ 首次申请送 5 次
- 数据来源：国家公示网，权威；查得才计费
- params.api_key 留空时自动跳过该源（由 run_with_fallback 切换下一源）

归一化：登记状态异常（注销/吊销/经营异常等）记高危，正常登记记 low（重复
标题由库表唯一键去重，状态变化会产生新标题从而入库形成"变更"轨迹）。
"""
from datetime import date

try:
    from src.core.models import Event, Enterprise
    from src.providers.base import ProviderBase
except ImportError:
    from ..core.models import Event, Enterprise
    from .base import ProviderBase

API_URL = "https://apis.juhe.cn/entpriseBase/query"

_BAD_STATUS = ("注销", "吊销", "迁出", "清算", "停业", "严重违法", "经营异常")
# 兼容聚合数据 PascalCase 与其他供应商 snake_case 两种返回风格
_FIELD_ALIASES = {
    "name": ("Name", "name", "entName"),
    "status": ("Status", "status", "regStatus"),
    "oper": ("OperName", "operName", "legalPersonName"),
    "capital": ("RegCapital", "regCapital", "regCapitalAmount"),
    "credit_code": ("CreditCode", "creditCode"),
    "start": ("StartDate", "termStart", "establishTime"),
    "address": ("Address", "address"),
    "scope": ("Scope", "scope", "businessScope"),
    "belong_org": ("BelongOrg", "belongOrg", "registrationAuthority"),
}


def _get(item: dict, key: str) -> str:
    for alias in _FIELD_ALIASES.get(key, (key,)):
        v = item.get(alias)
        if v not in (None, ""):
            return str(v).strip()
    return ""


def parse_events(item: dict, ent_name: str) -> list:
    """把聚合数据返回的 result 节点归一化为工商事件（纯函数，便于单测）。"""
    if not item:
        return []
    status = _get(item, "status")
    name = _get(item, "name") or ent_name
    oper = _get(item, "oper") or "未知"
    capital = _get(item, "capital") or "未知"
    credit_code = _get(item, "credit_code")
    partners = item.get("Partner") or item.get("partner") or []
    if isinstance(partners, dict):
        partners = partners.get("list") or partners.get("result") or []
    top = "、".join(
        "{}{}".format(
            p.get("StockName") or p.get("stockName") or p.get("name") or "",
            p.get("StockPercent") or p.get("stockPercent") or p.get("subRate") or "")
        for p in partners[:5]
        if isinstance(p, dict) and (p.get("StockName") or p.get("stockName") or p.get("name")))
    summary = (f"法定代表人：{oper}；注册资本：{capital}；"
               f"登记状态：{status or '未知'}；主要股东：{top or '未知'}")
    if credit_code:
        summary += f"；信用代码：{credit_code}"
    bad = any(k in status for k in _BAD_STATUS)
    title = f"{name}：工商登记状态【{status}】" if status else f"{name}：工商登记信息"
    return [Event(
        category="biz_change", title=title, summary=summary[:300],
        url="https://www.gsxt.gov.cn/", source="juhe_ent",
        occurred_at=date.today().strftime("%Y-%m-%d"),
        severity="high" if bad else "low",
        sentiment="negative" if bad else "neutral",
        relevance="high")]


class JuheEnterpriseProvider(ProviderBase):
    name = "juhe_ent"
    category = "biz_change"

    def fetch(self, ent: Enterprise) -> list:
        key = (self.params.get("api_key") or "").strip()
        if not key:
            raise RuntimeError("聚合数据 api_key 未配置（juhe.cn 申请企业工商信息接口），跳过")
        resp = self.http_get(API_URL, params={
            "key": key, "entName": ent.name}).json()
        code = resp.get("error_code")
        if code not in (0, "0"):
            raise RuntimeError(f"聚合数据 error_code={code}: {resp.get('reason')}")
        result = resp.get("result") or {}
        if isinstance(result, list):  # 部分接口返回列表，取首个精确匹配
            result = next((x for x in result
                           if str(x.get("Name") or x.get("name") or "") == ent.name),
                          result[0] if result else {})
        events = parse_events(result if isinstance(result, dict) else {}, ent.name)
        if not events:
            raise RuntimeError("聚合数据未查到该企业")
        return events
