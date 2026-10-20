"""同步编排：对单个企业按类目调用数据源并入库；企业名单 CSV/XLSX 导入导出。"""
import csv
from datetime import datetime

from . import db
from .models import CATEGORIES, Enterprise
from .relevance import filter_relevant

try:
    from ..providers.base import last_run, run_with_fallback
except ImportError:
    from src.providers.base import last_run, run_with_fallback

CSV_HEADERS = ["企业名称", "统一社会信用代码", "省份", "城市", "授信备注", "放款日期"]


# ---------- 同步 ----------
def lookback_cutoff() -> str:
    """追溯窗口起始日期（YYYY-MM-DD）：app.lookback_years，缺省 3 年；0/负数=不过滤。"""
    years = 3
    try:
        years = float(settings_config().get("app", {}).get("lookback_years", 3) or 3)
    except (TypeError, ValueError):
        years = 3.0
    if years <= 0:
        return ""
    return (datetime.now().replace(year=datetime.now().year - int(years))
            .strftime("%Y-%m-%d"))


def settings_config() -> dict:
    from . import settings
    return settings.config()


def _in_lookback(occurred_at: str, cutoff: str) -> bool:
    """无日期事件保留；有日期的按 ISO 字符串比较过滤。"""
    if not cutoff or not occurred_at:
        return True
    return str(occurred_at)[:10] >= cutoff


def run_sync(ent: Enterprise) -> dict:
    """对单个企业依次同步 news/biz_change/lawsuit/enforcement/social 等类目事件并入库。

    返回 {category: {inserted, source, error}}；insert_event 自动按
    (enterprise_id, category, title, source) 去重。
    入库前按 app.lookback_years 追溯窗口过滤（无日期事件保留）。
    """
    if ent.id is None:
        ent.id = db.upsert_enterprise(ent.name, ent.credit_code,
                                      ent.province, ent.city, ent.credit_note)
    cutoff = lookback_cutoff()
    rel_mode = "strict"
    try:
        rel_mode = str(settings_config().get("app", {}).get("relevance_filter", "strict")
                       or "strict").lower()
    except (TypeError, ValueError):
        rel_mode = "strict"
    results = {}
    for cat in CATEGORIES:
        try:
            events, source = run_with_fallback(cat, ent)
            fetched = len(events)
            events = filter_relevant(events, ent.name, rel_mode)  # 相关性过滤
            inserted = 0
            dropped = 0
            for ev in events:
                if not _in_lookback(ev.occurred_at, cutoff):
                    dropped += 1
                    continue
                ev.enterprise_id = ent.id
                if db.insert_event(ev):
                    inserted += 1
            # 每源明细（成功/失败/跳过），供「源健康度看板」与同步报告使用
            detail = last_run(cat) or {}
            results[cat] = {"inserted": inserted, "dropped_old": dropped,
                            "filtered": fetched - len(events),
                            "source": source if events else "",
                            "error": "" if events else source,
                            "sources": list(detail.get("sources") or []),
                            "stats": dict(detail.get("stats") or {}),
                            "errors": dict(detail.get("errors") or {})}
        except Exception as e:  # run_with_fallback 内部已兜底，此处防御额外异常
            results[cat] = {"inserted": 0, "dropped_old": 0, "source": "", "error": str(e),
                            "sources": [], "stats": {}, "errors": {}}
    return results


# ---------- CSV ----------
def _read_csv_rows(path: str) -> list:
    """兼容 utf-8-sig / gbk 编码读取 CSV。"""
    last_err = None
    for enc in ("utf-8-sig", "gbk"):
        try:
            with open(path, "r", encoding=enc, newline="") as f:
                return list(csv.DictReader(f))
        except UnicodeDecodeError as e:
            last_err = e
    raise ValueError(f"无法识别 CSV 编码: {path} ({last_err})")


def import_enterprises_from_csv(path: str) -> int:
    """从 CSV 导入企业名单（列：企业名称/统一社会信用代码/省份/城市/授信备注/放款日期），返回导入条数。

    「放款日期」列为可选：缺失或为空时按空串处理（不覆盖既有放款日）。
    """
    n = 0
    for row in _read_csv_rows(path):
        name = (row.get("企业名称") or "").strip()
        if not name:
            continue
        db.upsert_enterprise(
            name,
            credit_code=(row.get("统一社会信用代码") or "").strip(),
            province=(row.get("省份") or "").strip(),
            city=(row.get("城市") or "").strip(),
            credit_note=(row.get("授信备注") or "").strip(),
            loan_date=(row.get("放款日期") or "").strip())
        n += 1
    return n


def export_enterprises_to_csv(path: str) -> int:
    """导出监控中企业到 CSV，返回导出条数。utf-8-sig 便于 Excel 直接打开。"""
    ents = db.list_enterprises()
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(CSV_HEADERS)
        for e in ents:
            w.writerow([e.name, e.credit_code, e.province, e.city, e.credit_note, e.loan_date])
    return len(ents)


# ---------- XLSX ----------
def import_enterprises_from_xlsx(path: str) -> int:
    """从 Excel 导入企业名单（首行为表头），返回导入条数。"""
    from openpyxl import load_workbook
    wb = load_workbook(path, read_only=True, data_only=True)
    try:
        rows = wb.active.iter_rows(values_only=True)
        header = next(rows, None) or ()
        idx = {str(h).strip(): i for i, h in enumerate(header) if h}
        n = 0
        for r in rows:
            if r is None:
                continue

            def col(key):
                i = idx.get(key)
                if i is None or i >= len(r) or r[i] is None:
                    return ""
                return str(r[i]).strip()

            name = col("企业名称")
            if not name:
                continue
            db.upsert_enterprise(name, credit_code=col("统一社会信用代码"),
                                 province=col("省份"), city=col("城市"),
                                 credit_note=col("授信备注"), loan_date=col("放款日期"))
            n += 1
        return n
    finally:
        wb.close()


def export_enterprises_to_xlsx(path: str) -> int:
    """导出监控中企业到 Excel，返回导出条数。"""
    from openpyxl import Workbook
    ents = db.list_enterprises()
    wb = Workbook()
    ws = wb.active
    ws.title = "企业名单"
    ws.append(CSV_HEADERS)
    for e in ents:
        ws.append([e.name, e.credit_code, e.province, e.city, e.credit_note, e.loan_date])
    wb.save(path)
    return len(ents)
