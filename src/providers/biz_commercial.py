"""商业工商数据 API 通用骨架：天眼查 / 企查查 / 白牛，params 声明 vendor。

params: base_url（如 https://api.qichacha.com / https://open.api.tianyancha.com）、
api_key（企查查=AppKey；天眼查=TOKEN）、secret_key（企查查=SecretKey）、vendor。
base_url/api_key 为空抛异常跳过；实现 tianyancha / qichacha。
"""
import hashlib
import time
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
        if not api_key:
            raise RuntimeError("商业 API api_key 未配置，跳过该源")
        if vendor == "tianyancha":
            if not base:
                base = "https://open.api.tianyancha.com"
            return self._tianyancha(ent, base, api_key)
        if vendor == "qichacha":
            if not base:
                base = "https://api.qichacha.com"
            secret = self.params.get("secret_key") or ""
            return self._qichacha(ent, base, api_key, secret)
        raise NotImplementedError(f"商业 API vendor={vendor} 暂未实现")

    # ---------- 企查查开放平台 ----------
    @staticmethod
    def _qcc_token(app_key: str, secret_key: str, timespan: str) -> str:
        """企查查协议：Token = MD5(AppKey + Timespan + SecretKey) 32 位大写。"""
        raw = f"{app_key}{timespan}{secret_key}".encode("utf-8")
        return hashlib.md5(raw).hexdigest().upper()

    def _qcc_call(self, base: str, app_key: str, secret_key: str,
                  path: str, params: dict) -> dict:
        timespan = str(int(time.time()))
        headers = self.headers()
        headers["Token"] = self._qcc_token(app_key, secret_key, timespan)
        headers["Timespan"] = timespan
        params = dict(params, key=app_key)
        resp = self.http_get(f"{base}{path}", params=params, headers=headers)
        if resp.status_code != 200:
            raise RuntimeError(f"企查查 HTTP {resp.status_code}")
        data = resp.json()
        if str(data.get("Status") or data.get("status") or "") not in ("200", "201", "0", 200, 201):
            raise RuntimeError(f"企查查 Status={data.get('Status')}: {data.get('Message') or data.get('message')}")
        return data.get("Result") or data.get("result") or {}

    def _qichacha(self, ent: Enterprise, base: str, api_key: str, secret_key: str) -> list:
        """410 企业工商信息（基础）+ 735 变更记录 -> biz_change 事件。"""
        if not secret_key:
            raise RuntimeError("企查查 secret_key 未配置（开放平台账号安全页获取），跳过")
        item = self._qcc_call(base, api_key, secret_key,
                              "/ECIV4/GetBasicDetailsByName", {"keyword": ent.name})
        status = str(item.get("Status") or "")
        bad = any(k in status for k in _BAD_STATUS)
        reg_cap = item.get("RegistCapi") or item.get("RegisteredCapital") or "未知"
        summary = (f"法定代表人：{item.get('OperName') or '未知'}；"
                   f"注册资本：{reg_cap}；"
                   f"登记状态：{status or '未知'}；"
                   f"信用代码：{item.get('CreditCode') or '未知'}")
        events = [Event(
            category=self.category,
            title=f"{ent.name}：工商登记状态【{status}】" if status else f"{ent.name}：工商登记信息",
            summary=summary[:300], url="https://www.qcc.com/", source=self.name,
            occurred_at=date.today().strftime("%Y-%m-%d"),
            severity="high" if bad else "low",
            sentiment="negative" if bad else "neutral", relevance="high")]
        # 735 详情：变更记录 -> 独立事件（失败不影响基础事件）
        try:
            detail = self._qcc_call(base, api_key, secret_key,
                                    "/ECIV4/GetDetailInfoByName",
                                    {"searchKey": ent.name})
            for rec in (detail.get("ChangeRecords") or [])[:10]:
                item_name = str(rec.get("ChangeItem") or "")
                before = str(rec.get("BeforeContent") or "")[:40]
                after = str(rec.get("AfterContent") or "")[:40]
                if not item_name:
                    continue
                events.append(Event(
                    category=self.category,
                    title=f"{ent.name}：{item_name}由{before}变更为{after}",
                    summary=f"{item_name}：{before} -> {after}",
                    url="https://www.qcc.com/", source=self.name,
                    occurred_at=str(rec.get("ChangeDate") or "")[:10],
                    severity="medium" if item_name in ("股东", "法定代表人(负责人)变更", "法定代表人") else "low",
                    sentiment="neutral", relevance="high"))
        except Exception:  # noqa: BLE001  735 未开通/失败时仅返回基础信息
            pass
        return events

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
