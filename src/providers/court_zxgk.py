"""中国执行信息公开网适配器（zxgk.court.gov.cn，被执行人查询）。

接口：http://zxgk.court.gov.cn/zhixing/new/select（POST 表单，需验证码）。
被验证码拦截时抛异常走 fallback；命中则生成 high severity 事件（含案号）。
"""
import requests

try:
    from src.core.models import Event, Enterprise
    from src.core import settings
    from src.providers.base import ProviderBase
except ImportError:
    from ..core.models import Event, Enterprise
    from ..core import settings
    from .base import ProviderBase

SELECT_URL = "http://zxgk.court.gov.cn/zhixing/new/select"

_TYPE_LABELS = ((("失信", "被列为失信被执行人"), ("限制高消费", "被列为限制高消费"),
                 ("限高", "被列为限制高消费")))


class ZxgkProvider(ProviderBase):
    name = "zxgk"
    category = "enforcement"

    def http_post(self, url: str, **kw):
        """带节流/代理的 POST（基类仅提供 GET）。"""
        self._throttle()
        kw.setdefault("headers", self.headers())
        kw.setdefault("timeout", self.timeout)
        proxy = settings.config().get("app", {}).get("proxy", "")
        if proxy:
            kw.setdefault("proxies", {"http": proxy, "https": proxy})
        return requests.post(url, **kw)

    def headers(self) -> dict:
        h = super().headers()
        h.update({"Referer": "http://zxgk.court.gov.cn/zhixing/new/index",
                  "Content-Type": "application/x-www-form-urlencoded"})
        return h

    def fetch(self, ent: Enterprise) -> list:
        form = {"searchTxt": ent.name, "pCode": "", "pName": "",
                "captchaId": "", "selectCode": ""}
        resp = self.http_post(SELECT_URL, data=form)
        if resp.status_code != 200:
            raise RuntimeError(f"执行信息网 HTTP {resp.status_code}")
        try:
            payload = resp.json()
        except ValueError:
            if "验证码" in resp.text:
                raise RuntimeError("执行信息网被验证码拦截")
            raise RuntimeError("执行信息网返回非 JSON")
        if not isinstance(payload, dict):
            raise RuntimeError("执行信息网返回结构异常")
        status = payload.get("status")
        message = str(payload.get("message") or payload.get("msg") or "")
        if status != 1:
            if "验证码" in message:
                raise RuntimeError("执行信息网被验证码拦截")
            return []  # 正常无记录
        rows = payload.get("data") or payload.get("list") or []
        events = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            case_code = str(row.get("caseCode") or row.get("casecode") or "").strip()
            exec_court = str(row.get("execCourtName") or row.get("courtName") or "").strip()
            label = "被列为被执行人"
            for key, lbl in _TYPE_LABELS:
                if key in str(row):
                    label = lbl
                    break
            title = f"{ent.name}：{label}"
            if case_code:
                title += f"（案号 {case_code}）"
            summary = f"执行法院：{exec_court or '未知'}；立案时间：{row.get('caseCreateTime') or '未知'}"
            events.append(Event(
                category="enforcement", title=title, summary=summary,
                url="http://zxgk.court.gov.cn/zhixing/new/index", source=self.name,
                occurred_at=str(row.get("caseCreateTime") or "")[:10],
                severity="high", sentiment="negative", relevance="high"))
        return events
