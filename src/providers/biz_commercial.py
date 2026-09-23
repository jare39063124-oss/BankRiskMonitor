"""商业工商数据 API 通用骨架：天眼查 / 企查查 / 白牛，params 声明 vendor。

params: base_url（如 https://open.api.tianyancha.com）、api_key、vendor。
base_url/api_key 为空抛异常跳过；目前实现 tianyancha，其余 vendor 留 TODO。
"""
from datetime import date

try:
    from src.core.models import Event, Enterprise
    from src.providers.base import ProviderBase
except ImportError:
    from ..core.models import Event, Enterprise
    from .base import ProviderBase

_BAD_STATUS = ("注销", "吊销", "经营异常", "迁出", "严重违法")


class CommercialApiProvider(ProviderBase):
    name = "commercial_api"
    category = "biz_change"

    def fetch(self, ent: Enterprise) -> list:
        base = (self.params.get("base_url") or "").rstrip("/")
        api_key = self.params.get("api_key") or ""
        vendor = (self.params.get("vendor") or "").lower()
        if not base or not api_key:
            raise RuntimeError("商业 API base_url/api_key 未配置，跳过该源")
        if vendor == "tianyancha":
            return self._tianyancha(ent, base, api_key)
        # TODO: 实现 qichacha / bainiu 等其余供应商适配
        raise NotImplementedError(f"商业 API vendor={vendor} 暂未实现")

    def _tianyancha(self, ent: Enterprise, base: str, api_key: str) -> list:
        """调用 baseinfoV2/2.0 与 holder/2.0，映射为工商事件。"""
        headers = self.headers()
        headers["Authorization"] = api_key  # 天眼查以 token 作鉴权头
        baseinfo = self.http_get(
            f"{base}/services/open/ic/baseinfoV2/2.0",
            params={"name": ent.name}, headers=headers).json()
        item = baseinfo.get("result") or {}
        if not item:
            raise RuntimeError(f"天眼查未查到企业: {baseinfo.get('error_msg') or baseinfo.get('msg')}")
        holders = self.http_get(
            f"{base}/services/open/ic/holder/2.0",
            params={"name": ent.name, "pageSize": 20, "pageNum": 1},
            headers=headers).json()
        hlist = (holders.get("result") or {}).get("result") or []

        status = str(item.get("regStatus") or "")
        bad = any(k in status for k in _BAD_STATUS)
        top = "、".join(
            f"{h.get('name','')}{h.get('ratio','') or ''}".strip()
            for h in hlist[:5] if h.get("name"))
        summary = (f"法定代表人：{item.get('legalPersonName') or '未知'}；"
                   f"注册资本：{item.get('regCapital') or '未知'}；"
                   f"主要股东：{top or '未知'}")
        company_id = item.get("id") or ""
        url = f"https://www.tianyancha.com/company/{company_id}" if company_id else ""
        events = [Event(
            category=self.category,
            title=f"{ent.name}：登记状态{status}" if status else f"{ent.name}：工商登记信息",
            summary=summary, url=url, source=self.name,
            occurred_at=date.today().strftime("%Y-%m-%d"),
            severity="high" if bad else "low",
            sentiment="negative" if bad else "neutral",
            relevance="high")]
        return events
