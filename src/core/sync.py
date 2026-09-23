"""同步编排：对单个企业按类目调用数据源并入库；企业名单 CSV/XLSX 导入导出。"""
import csv

from . import db
from .models import CATEGORIES, Enterprise

try:
    from ..providers.base import run_with_fallback
except ImportError:
    from src.providers.base import run_with_fallback

CSV_HEADERS = ["企业名称", "统一社会信用代码", "省份", "城市", "授信备注"]


# ---------- 同步 ----------
def run_sync(ent: Enterprise) -> dict:
    """对单个企业依次同步 news/biz_change/lawsuit/enforcement 四类事件并入库。

    返回 {category: {inserted, source, error}}；insert_event 自动按
    (enterprise_id, category, title, source) 去重。
    """
    if ent.id is None:
        ent.id = db.upsert_enterprise(ent.name, ent.credit_code,
                                      ent.province, ent.city, ent.credit_note)
    results = {}
    for cat in CATEGORIES:
        try:
            events, source = run_with_fallback(cat, ent)
            inserted = 0
            for ev in events:
                ev.enterprise_id = ent.id
                if db.insert_event(ev):
                    inserted += 1
            results[cat] = {"inserted": inserted, "source": source if events else "",
                            "error": "" if events else source}
        except Exception as e:  # run_with_fallback 内部已兜底，此处防御额外异常
            results[cat] = {"inserted": 0, "source": "", "error": str(e)}
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
    """从 CSV 导入企业名单（列：企业名称/统一社会信用代码/省份/城市/授信备注），返回导入条数。"""
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
            credit_note=(row.get("授信备注") or "").strip())
        n += 1
    return n


def export_enterprises_to_csv(path: str) -> int:
    """导出监控中企业到 CSV，返回导出条数。utf-8-sig 便于 Excel 直接打开。"""
    ents = db.list_enterprises()
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(CSV_HEADERS)
        for e in ents:
            w.writerow([e.name, e.credit_code, e.province, e.city, e.credit_note])
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
                                 credit_note=col("授信备注"))
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
        ws.append([e.name, e.credit_code, e.province, e.city, e.credit_note])
    wb.save(path)
    return len(ents)
