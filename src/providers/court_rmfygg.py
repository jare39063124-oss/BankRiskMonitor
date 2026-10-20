"""人民法院公告网适配器（rmfygg.court.gov.cn）——涉诉/执行公告的可靠源。

为什么可靠
----------
裁判文书网（wenshu）需要 token 与验证码、执行信息公开网（zxgk）直接 412、
信用中国接口已下线，这几个官方渠道在纯 HTTP 下全部走不通。人民法院公告网
（最高法主办，公告当事人涉诉/执行/送达/拍卖信息）**页面可公开访问**，是当前
唯一可稳定直连的司法公告源。

抓取通路（两处实测结论，缺一不可）
----------------------------------
1. 该站为 Liferay portal + DataTables 服务端分页，公告列表由 JS 异步渲染。
   纯 HTTP 打接口恒定返回 ``iTotalRecords=0``（服务端按来源/会话过滤），
   **必须用真实浏览器渲染**。
2. URL 上的 ``searchContent`` 参数会被服务端**忽略**（照样返回全量最新公告），
   真正的搜索是页面内的搜索框 + ``#searchButton``（``searchNoticeList('click')``）。
   因此必须：打开页面 → 设 ``#rmfy-form`` → 点 ``#searchButton`` → 等异步回填。

渲染后端由 ``src/core/cdp.py`` 统一管理：公开站点默认走 Obscura 轻量无头引擎
（自动拉起、无窗口），失败再回退到已运行的 Chrome。**必须使用 https 地址**：
Obscura 不跟随 http→https 跳转，若停在 http 源，其异步接口走 https 会被判为跨源。

公告类型（页面渲染出的中文文本）
--------------------------------
* 「裁判文书」「起诉状副本及开庭传票」「开庭传票」等 → 涉诉（category=lawsuit）
* 「执行文书、拍卖」等 → 执行（category=enforcement，高危）
"""
import json
import re
import time
from typing import List

from bs4 import BeautifulSoup

try:
    from src.core.models import Event, Enterprise
    from src.providers.base import ProviderBase
except ImportError:
    from ..core.models import Event, Enterprise
    from .base import ProviderBase

# 必须 https（见模块 docstring：Obscura 不跟随 http→https 跳转）
LIST_URL = "https://rmfygg.court.gov.cn/web/rmfyportal/noticeinfo"

# 页面内搜索脚本：写关键词 → 触发 input/change → 点真实搜索按钮
_SEARCH_JS = """(function () {
  var kw = %s;
  var inp = document.getElementById('rmfy-form')
        || document.querySelector('input[name="searchContent"]');
  if (!inp) { return 'NO_INPUT'; }
  inp.value = kw;
  inp.dispatchEvent(new Event('input', {bubbles: true}));
  inp.dispatchEvent(new Event('change', {bubbles: true}));
  var btn = document.getElementById('searchButton')
        || document.querySelector('[onclick*="searchNoticeList"]');
  if (!btn) { return 'NO_BUTTON'; }
  btn.click();
  return 'CLICKED';
})()"""

# 执行/处置类关键词：命中即高危执行事件
_HIGH_KEYWORDS = ("执行", "拍卖", "变卖", "查封", "冻结", "失信", "限制消费", "执行款")
# 诉讼/裁判类关键词：命中记中危涉诉事件
_LAWSUIT_KEYWORDS = ("裁判", "诉讼", "开庭", "传票", "起诉", "判决", "裁定", "调解", "上诉", "公告")

# 同一次页面搜索同时覆盖「涉诉」与「执行」两类公告，而两类的采集流程会分别调用本适配器，
# 因此按企业名做短时记忆：同一企业短时间内重复调用直接复用上次结果，避免重复开页面。
_MEMO: dict = {}
_MEMO_TTL = 600.0


def _memo_get(name: str):
    hit = _MEMO.get(name)
    if hit and time.time() - hit[0] < _MEMO_TTL:
        return hit[1]
    return None


def _memo_put(name: str, events: List[Event]) -> None:
    _MEMO[name] = (time.time(), events)


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


def parse_notice_rows(html: str) -> List[dict]:
    """从渲染后的公告列表页解析出公告行（纯函数，便于单测）。

    表格结构（DataTables 渲染后）：每行 5 列 —
    公告人(法院) / 当事人 / 公告类型 / 发布时间 / 下载打印。
    """
    soup = BeautifulSoup(html, "html.parser")
    rows: List[dict] = []
    table = soup.find("table", id="noticeListTable") or soup.find("table")
    if table is None:
        return rows
    for tr in table.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) < 4:
            continue
        cells = [_clean(td.get_text(" ", strip=True)) for td in tds]
        if not cells[0] or "公告人" in cells[0]:  # 表头
            continue
        rows.append({
            "court": cells[0] if len(cells) > 0 else "",
            "parties": cells[1] if len(cells) > 1 else "",
            "notice_type": cells[2] if len(cells) > 2 else "",
            "publish_date": cells[3] if len(cells) > 3 else "",
        })
    return rows


def match_party(parties: str, ent_name: str) -> bool:
    """公告当事人是否命中监控企业。

    公告里当事人常写成「甲公司;乙公司」，且可能不含行政区划前缀，
    因此按**核心词**（去掉省市区与常见后缀）做包含判断，避免漏配。
    """
    if not parties or not ent_name:
        return False
    if ent_name in parties:
        return True
    core = _core_name(ent_name)
    if len(core) < 4:
        return False
    return core in parties


def _core_name(name: str) -> str:
    """企业核心词：剥离行政区划前缀与「有限公司」等后缀。"""
    s = _clean(name)
    for prefix in ("辽宁省", "辽宁", "吉林省", "黑龙江省", "内蒙古", "北京市", "上海市",
                   "天津市", "重庆市", "河北省", "山西省", "山东省", "河南省", "湖北省",
                   "湖南省", "广东省", "广西", "海南省", "四川省", "贵州省", "云南省",
                   "陕西省", "甘肃省", "青海省", "台湾省", "新疆", "西藏", "宁夏",
                   "省", "市", "区", "县"):
        if s.startswith(prefix):
            s = s[len(prefix):]
            break
    for suffix in ("有限责任公司", "股份有限公司", "有限公司", "集团有限公司", "集团",
                   "公司", "企业", "厂", "店", "中心", "合作社"):
        if s.endswith(suffix):
            s = s[: -len(suffix)]
            break
    return s.strip()


def classify_notice(rendered_type: str, parties: str = "") -> tuple:
    """按页面渲染的公告类型文本判定 ``(category, severity)``。

    页面列出的类型是中文文本（如「执行文书、拍卖」「裁判文书」「起诉状副本及开庭传票」），
    据此分流到执行 / 涉诉，并按关键词定严重度。
    """
    text = f"{rendered_type} {parties}"
    if any(k in text for k in _HIGH_KEYWORDS):
        return "enforcement", "high"
    if any(k in text for k in _LAWSUIT_KEYWORDS):
        return "lawsuit", "medium"
    return "lawsuit", "low"


class RmfyggProvider(ProviderBase):
    """人民法院公告网：涉诉（裁判文书/开庭传票）与执行（执行文书、拍卖）公告。"""

    name = "rmfygg"
    category = "lawsuit"  # 实际按公告类型分流到 lawsuit / enforcement

    def headers(self) -> dict:
        h = super().headers()
        h.update({"Referer": LIST_URL})
        return h

    def _settle_ms(self) -> int:
        """异步回填的额外等待毫秒：params 优先，其次 config 的 browser.settle_ms。"""
        configured = 0
        try:
            from ..core import cdp
            configured = int(cdp.browser_conf().get("settle_ms") or 0)
        except ImportError:
            pass
        return int(self.params.get("settle_ms") or configured or 3000)

    def _page_timeout(self) -> float:
        return float(self.params.get("timeout_seconds") or 30)

    def fetch(self, ent: Enterprise) -> List[Event]:
        """在渲染后端里打开公告页 → 页面内搜索本企业 → 解析命中的公告。"""
        cached = _memo_get(ent.name)
        if cached is not None:
            return list(cached)

        try:
            from ..core import cdp
        except ImportError:  # pragma: no cover - 顶层 providers 导入路径
            from src.core import cdp

        settle = self._settle_ms()
        session = cdp.public_session(timeout=self._page_timeout())
        try:
            session.open(LIST_URL, settle_ms=min(settle, 2500))
            # 表单就绪后再搜索，避免过早点击落在未初始化的页面上
            session.wait_until("document.getElementById('rmfy-form')", timeout=8)
            verdict = session.evaluate(_SEARCH_JS % json.dumps(ent.name, ensure_ascii=False))
            if str(verdict).startswith("NO_"):
                raise RuntimeError(f"人民法院公告网页面结构变化（{verdict}），搜索未执行")
            session.wait_until(
                "document.querySelectorAll('#gg-list tr').length > 0", timeout=12)
            time.sleep(settle / 1000.0)
            html = session.html()
        finally:
            session.close()

        events = self._parse(html, ent)
        _memo_put(ent.name, events)
        return list(events)

    def _parse(self, html: str, ent: Enterprise) -> List[Event]:
        events: List[Event] = []
        for row in parse_notice_rows(html):
            parties = row.get("parties") or ""
            if not match_party(parties, ent.name):
                continue
            rendered_type = row.get("notice_type") or ""
            category, severity = classify_notice(rendered_type, parties)
            title = f"{ent.name}：{rendered_type or '司法公告'}"
            if row.get("court"):
                title += f"（{row['court']}）"
            summary = f"当事人：{parties}"
            if row.get("publish_date"):
                summary += f"；发布时间：{row['publish_date']}"
            events.append(Event(
                category=category,
                title=title[:300],
                summary=summary[:300],
                url=LIST_URL,
                source=self.name,
                occurred_at=(row.get("publish_date") or "")[:10],
                severity=severity,
                sentiment="negative",
                relevance="high"))
        return events
