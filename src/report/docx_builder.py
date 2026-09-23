"""标准行内监测报告 docx 构建。

格式规范：
- 标题：黑体小二（18pt）居中；报告期间与编制人：宋体小四居中；
- 一级章节：黑体四号（14pt），按出现顺序自动编号“一、二、三…”；
- 正文：宋体小四（12pt），1.5 倍行距；表格文字 10.5pt（五号）。
- sections 由 GUI 按勾选组装，缺失章节自动跳过。
"""
import os

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Pt

# 章节定义：key -> 标题（编号按出现顺序自动生成）
CHAPTER_DEFS = [
    ("overview", "监测对象概况"),
    ("events", "监测事件明细"),
    ("ai", "AI 影响研判"),
    ("penetration", "穿透图谱说明"),
    ("alerts", "预警汇总"),
    ("advice", "处置建议"),
]

_FONT_SONG = "宋体"
_FONT_HEI = "黑体"
_SIZE_TITLE = 18     # 小二
_SIZE_HEAD = 14      # 四号
_SIZE_BODY = 12      # 小四
_SIZE_TABLE = 10.5   # 五号


def _style_run(run, east=_FONT_SONG, size=_SIZE_BODY, bold=False):
    run.font.name = east
    run._element.rPr.rFonts.set(qn("w:eastAsia"), east)
    run.font.size = Pt(size)
    run.font.bold = bold


def _add_para(doc, text, east=_FONT_SONG, size=_SIZE_BODY, bold=False,
              align=None, line_spacing=1.5, space_after_pt=4):
    p = doc.add_paragraph()
    if align is not None:
        p.alignment = align
    pf = p.paragraph_format
    pf.line_spacing = line_spacing
    pf.space_after = Pt(space_after_pt)
    run = p.add_run(text)
    _style_run(run, east=east, size=size, bold=bold)
    return p


def _add_table(doc, headers, rows):
    table = doc.add_table(rows=1, cols=len(headers))
    table.style = "Table Grid"

    def fill(cell, text, bold=False):
        cell.text = ""
        run = cell.paragraphs[0].add_run(str(text if text is not None else ""))
        _style_run(run, size=_SIZE_TABLE, bold=bold)

    for i, h in enumerate(headers):
        fill(table.rows[0].cells[i], h, bold=True)
    for row in rows:
        cells = table.add_row().cells
        for i in range(len(headers)):
            val = row[i] if i < len(row) else ""
            fill(cells[i], val)
    return table


def _enterprise_rows(overview) -> list:
    """overview 数据归一为 [企业名, 地区, 授信备注] 行。"""
    rows = []
    for item in overview or []:
        if isinstance(item, dict):
            region = item.get("region") or " ".join(
                x for x in (item.get("province"), item.get("city")) if x)
            rows.append([item.get("name", ""), region, item.get("credit_note", "")])
        else:  # Enterprise 对象
            region = " ".join(x for x in (getattr(item, "province", ""), getattr(item, "city", "")) if x)
            rows.append([getattr(item, "name", ""), region, getattr(item, "credit_note", "")])
    return rows


def _norm_rows(data) -> list:
    """把 list[dict] / list[Enterprise] 归一为表格行。"""
    rows = []
    for item in data or []:
        if isinstance(item, dict):
            rows.append(list(item.values()))
        elif hasattr(item, "__dataclass_fields__"):
            rows.append([getattr(item, k) for k in
                         ("name", "province", "relation", "pen_level") if hasattr(item, k)])
        elif isinstance(item, (list, tuple)):
            rows.append(list(item))
        else:
            rows.append([str(item)])
    return rows


def build_report(title: str, period_start: str, period_end: str, author: str,
                 sections: dict, event_rows: list, save_path: str) -> str:
    """构建并保存 docx 报告，返回 save_path。

    sections 约定：
      overview    -> list[dict{name, region, credit_note}] 或 Enterprise 列表
      events      -> True/非空即生成（数据取 event_rows）
      ai          -> 正文文本（AI 影响研判）
      penetration -> 正文文本（穿透图谱说明）
      alerts      -> 正文文本，或 list[dict]（渲染为表格）
      advice      -> 正文文本（处置建议）
    """
    doc = Document()

    # 标题（黑体小二居中）
    _add_para(doc, title, east=_FONT_HEI, size=_SIZE_TITLE, bold=False,
              align=WD_ALIGN_PARAGRAPH.CENTER, space_after_pt=6)
    # 报告期间与编制人（宋体小四居中）
    _add_para(doc, f"报告期间：{period_start} 至 {period_end}　　编制人：{author}",
              align=WD_ALIGN_PARAGRAPH.CENTER, space_after_pt=12)

    chapter_no = 0
    for key, heading in CHAPTER_DEFS:
        value = sections.get(key)
        if not value and key != "events":
            continue
        if key == "events" and not (value or event_rows):
            continue
        chapter_no += 1
        num = "一二三四五六七八九十"[chapter_no - 1] if chapter_no <= 10 else str(chapter_no)
        _add_para(doc, f"{num}、{heading}", east=_FONT_HEI, size=_SIZE_HEAD,
                  bold=False, space_after_pt=6)

        if key == "overview":
            rows = _enterprise_rows(value)
            if rows:
                _add_table(doc, ["企业名", "地区", "授信备注"], rows)
            else:
                _add_para(doc, "本期无监测对象。")
        elif key == "events":
            if event_rows:
                _add_table(doc, ["时间", "企业", "类型", "标题", "严重度", "研判"], event_rows)
            else:
                _add_para(doc, "报告期内无监测事件。")
        elif key == "alerts":
            if isinstance(value, list):
                rows = _norm_rows(value)
                if rows:
                    _add_table(doc, ["时间", "企业", "类型", "标题", "严重度"], rows)
                else:
                    _add_para(doc, "报告期内无预警事件。")
            else:
                _add_para(doc, str(value))
        else:  # ai / penetration / advice：正文文本
            for seg in str(value).splitlines():
                if seg.strip():
                    _add_para(doc, seg.strip())

    os.makedirs(os.path.dirname(os.path.abspath(save_path)), exist_ok=True)
    doc.save(save_path)
    return save_path
