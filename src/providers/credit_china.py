"""信用中国适配器（creditchina.gov.cn，免费官方源，等级③ 尽力而为）。

覆盖「行政处罚 / 失信 / 重大税收违法失信（欠税）」→ 统一归入既有 ``enforcement``
类目（零契约迁移，ARCH §1.6.4）。该站点为公开政务源，接口/反爬策略会变，
**端点运行时核实**：此处的默认端点仅为兜底，可由 ``config.yaml`` 的
``credit_china.params.endpoint`` 覆盖。任何触网/解析失败一律抛 ``RuntimeError``，
交由 ``run_with_fallback`` 逐源兜底（**绝不冒泡到 GUI、绝不阻塞刷新**）。

实测（2026-10）
--------------
该站部署了加速乐 WAF：首页与 API 端点对裸 HTTP 统一返回 **HTTP 412**
（响应体是 JS 挑战页，会种 ``__jsluid_s`` Cookie）；即便带上完整浏览器请求头
与 cookie jar 仍被拦。走浏览器渲染后端（Obscura）能打开页面但正文为空
（挑战脚本需要真实指纹环境），因此当前**无法稳定取数**。

结论：本源按**尽力而为**处理——保留配置位、不静默失败、错误信息明确告诉
用户「怎么补」（连接器代查 / 改用 rmfygg），不虚报可用。
"""
from typing import List

try:
    from src.core.models import Event, Enterprise
    from src.providers.base import ProviderBase
except ImportError:
    from ..core.models import Event, Enterprise
    from .base import ProviderBase

# 默认查询端点（兜底；真实端点可能随站点改版变化，配置项 endpoint 可覆盖，不臆造更多字段）
DEFAULT_ENDPOINT = "https://public.creditchina.gov.cn/private-api/catalogSearchHome"

# 反爬/验证页特征
_BLOCK_WORDS = ("请开启JavaScript", "安全验证", "验证码", "访问过于频繁", "人机验证")


def _records(data) -> list:
    """从嵌套 JSON 中提取记录列表（兼容 list/result/data/rows/records 等包装）。"""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("list", "result", "data", "rows", "records", "items"):
            v = data.get(key)
            if isinstance(v, list):
                return v
            if isinstance(v, dict):
                sub = _records(v)
                if sub:
                    return sub
    return []


class CreditChinaProvider(ProviderBase):
    """信用中国：行政处罚 / 失信 / 重大税收违法失信 → ``enforcement`` 事件。"""

    name = "credit_china"
    category = "enforcement"

    def headers(self) -> dict:
        h = super().headers()
        h.update({"Referer": "https://www.creditchina.gov.cn/",
                  "Accept": "application/json, text/plain, */*"})
        return h

    def fetch(self, ent: Enterprise) -> List[Event]:
        endpoint = self.params.get("endpoint") or DEFAULT_ENDPOINT
        resp = self.http_get(endpoint, params={"keyword": ent.name, "searchState": "1", "page": 1})
        if resp.status_code in (401, 403):
            raise RuntimeError(
                f"信用中国未授权/鉴权失败（HTTP {resp.status_code}）。"
                "请在 config.yaml 的 credit_china.params.endpoint 配置可用镜像端点，"
                "或改用连接器代查后导入。")
        if resp.status_code == 412:
            raise RuntimeError(
                "信用中国 HTTP 412（加速乐 WAF 的 JS 挑战，裸 HTTP 无法通过）。"
                "建议改用连接器代查（企查查/天眼查/启信慧眼/水滴信用）后导入，"
                "或依赖人民法院公告网（rmfygg）获取执行/失信线索。")
        if resp.status_code != 200:
            raise RuntimeError(
                f"信用中国 HTTP {resp.status_code}（端点可能已变更）。"
                "请在 config.yaml 的 credit_china.params.endpoint 更新为可用端点；"
                "或改用连接器代查后导入。")
        # JSON 优先；非 JSON 再按反爬页判定（避免把合法 JSON 误判为反爬）
        try:
            data = resp.json()
        except ValueError:
            text = resp.text or ""
            if len(text) < 50 or any(w in text for w in _BLOCK_WORDS):
                raise RuntimeError("信用中国反爬拦截（需验证码/JS 挑战）")
            raise RuntimeError("信用中国返回非 JSON")
        return self._parse(data, ent)

    def _parse(self, data, ent: Enterprise) -> List[Event]:
        events: List[Event] = []
        for it in _records(data):
            if not isinstance(it, dict):
                continue
            blob = " ".join(str(v) for v in it.values())
            name = str(it.get("name") or it.get("entityName") or it.get("companyName") or "").strip()
            # 仅保留与本企业相关的记录（关键词命中企业名）
            if name and ent.name and ent.name not in name and name not in ent.name:
                continue
            if "税收" in blob and ("欠税" in blob or "违法" in blob):
                label, severity = "重大税收违法失信", "high"
            elif "失信" in blob or "被执行" in blob:
                label, severity = "失信被执行人", "high"
            else:
                label, severity = "行政处罚", "medium"
            case = str(it.get("caseNo") or it.get("caseCode") or it.get("id") or "").strip()
            title = f"{ent.name}：{label}" + (f"（{case}）" if case else "")
            date = str(it.get("publishDate") or it.get("date") or it.get("time") or "")[:10]
            url = str(it.get("url") or it.get("detailUrl") or "https://www.creditchina.gov.cn/")
            events.append(Event(
                category="enforcement", title=title,
                summary=blob[:300], url=url, source=self.name,
                occurred_at=date, severity=severity, sentiment="negative", relevance="high"))
        return events
