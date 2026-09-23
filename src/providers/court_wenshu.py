"""裁判文书网公开查询适配器（wenshu.court.gov.cn，涉诉类目）。

该站点需 JS 计算Cookie（vjk5/VLMDfL）方可查询，这里做尽力而为的尝试；
任何拦截/失败都抛异常，由 run_with_fallback 接管。
"""
import json

import requests

try:
    from src.core.models import Event, Enterprise
    from src.core import settings
    from src.providers.base import ProviderBase
except ImportError:
    from ..core.models import Event, Enterprise
    from ..core import settings
    from .base import ProviderBase

HOME_URL = "https://wenshu.court.gov.cn/"
LIST_URL = "https://wenshu.court.gov.cn/List/ListContent"


class WenshuProvider(ProviderBase):
    name = "wenshu"
    category = "lawsuit"

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
        h.update({"Referer": HOME_URL,
                  "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"})
        return h

    def fetch(self, ent: Enterprise) -> list:
        # 先访问首页获取基础 Cookie，再按站点旧接口格式提交查询
        home = self.http_get(HOME_URL)
        if home.status_code != 200:
            raise RuntimeError(f"裁判文书网首页 HTTP {home.status_code}")
        param = {"index": 1, "page": 10, "order": "法院层级", "direction": "desc",
                 "keyword": "", "judicialQuestion": "", "caseType": "",
                 "court": "", "party": ent.name, "startdate": "", "enddate": ""}
        resp = self.http_post(LIST_URL, data={"param": json.dumps(param, ensure_ascii=False)})
        if resp.status_code != 200:
            raise RuntimeError(f"裁判文书网 HTTP {resp.status_code}")
        text = resp.text
        if "验证" in text or "请开启JavaScript" in text or text.strip().startswith("<!DOCTYPE"):
            raise RuntimeError("裁判文书网反爬拦截（需 JS 计算 Cookie/验证码）")
        try:
            payload = json.loads(text)
        except ValueError:
            raise RuntimeError("裁判文书网返回非 JSON")
        rows = payload if isinstance(payload, list) else (payload.get("data") or [])
        events = []
        for row in rows:
            if not isinstance(row, dict):
                continue  # 首条可能为统计字段
            case_name = str(row.get("caseName") or row.get("title") or "").strip()
            case_no = str(row.get("caseNo") or row.get("caseno") or "").strip()
            if not case_name:
                continue
            title = f"{ent.name}：涉诉文书（{case_name}）"
            if case_no:
                title += f" 案号 {case_no}"
            events.append(Event(
                category="lawsuit", title=title,
                summary=str(row.get("judgmentResult") or "")[:200],
                url=str(row.get("url") or ""), source=self.name,
                occurred_at=str(row.get("judgementDate") or row.get("publicDate") or "")[:10],
                severity="medium", sentiment="negative", relevance="high"))
        if not events:
            raise RuntimeError("裁判文书网未解析到文书条目")
        return events
