"""连接器代查补充通路（ARCH-v2-increment §1.6.3，冻结契约）。

主 Agent 经 MCP 工商连接器（tyc-mcp / qcc-company / qixinhuiyan-mcp / shuidi-credit）
代查企业档案 → 落成批次 JSON → 本脚本归一为 ``Event`` 幂等入库（``monitor.db``）。
脚本为**离线 CLI**，不改 GUI 主流程。

输入 JSON schema（冻结，§1.6.3.1）::

    {
      "schema_version": "1.0",
      "generated_at": "2026-10-16T14:30:00",   // 仅溯源，绝不写入 occurred_at
      "connector": "tyc_mcp",
      "companies": [{
        "name": "...", "credit_code": "...",
        "profile": {...}, "changes": [{change_item,before,after,change_date}],
        "lawsuits": [...], "enforcements": [...], "url": "..."
      }]
    }

硬约束：
* ``change_date`` 有则入 ``occurred_at``；**无则空串**（并追加摘要标注）；
  **即便 ``change_date == generated_at``（抓取日）也留空**（历史 7 行污染的病根）。
* 幂等去重：``insert_event`` 唯一键 ``(enterprise_id, category, title, source)``；**title 不含日期**。
* ``source`` 命名冻结：``tyc_mcp``/``qcc_company``/``qixinhuiyan_mcp``/``shuidi_credit``；
  未知 → ``connector_unknown`` 并 warning，**不阻断**。
* 未授权（401/403/无权限）→ ``reason=unauthorized``，**不重试、不弹窗**；
  失败/空 → 优雅跳过并写 ``data/logs/connector_<ts>.log``；**绝不抛异常或阻塞**。

用法::

    python tools/connector_ingest.py --file batch.json [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.core import db  # noqa: E402
from src.core import paths  # noqa: E402
from src.core.models import Event  # noqa: E402

log = logging.getLogger("connector_ingest")

# source 命名冻结（ARCH §1.6.3.1）；未知值 → connector_unknown（warning，不阻断）
VALID_CONNECTORS = ("tyc_mcp", "qcc_company", "qixinhuiyan_mcp", "shuidi_credit")
UNKNOWN_CONNECTOR = "connector_unknown"

# 未授权识别关键词
_UNAUTHORIZED_WORDS = ("unauthor", "401", "403", "permission", "无权限", "未授权", "forbidden")


@dataclass
class CompanyProfile:
    """单企业代查结果（§1.6.3.1 companies[] 元素）。"""

    name: str = ""
    credit_code: str = ""
    profile: dict = field(default_factory=dict)
    changes: list = field(default_factory=list)
    lawsuits: list = field(default_factory=list)
    enforcements: list = field(default_factory=list)
    url: str = ""


@dataclass
class ConnectorProfile:
    """一个代查批次（§1.6.3.1 顶层对象）。"""

    schema_version: str = ""
    generated_at: str = ""
    connector: str = ""
    companies: List[CompanyProfile] = field(default_factory=list)
    error: str = ""
    status: str = ""


# ---------------------------------------------------------------------------
# 解析与归一
# ---------------------------------------------------------------------------
def _norm_date(value) -> str:
    """归一为 ``'YYYY-MM-DD'``；任何无法解析的值（None/''/脏值）→ ``''``（§1.6.3.2）。"""
    s = str(value or "").strip()[:10]
    if len(s) == 10 and s[4] == "-" and s[7] == "-" and s.replace("-", "").isdigit():
        return s
    return ""


def _occurred_at(change_date, *, generated_at: str) -> tuple:
    """返回 ``(occurred_at, date_note)``。硬约束：**绝不回退到 generated_at**（§1.6.3.2）。"""
    d = _norm_date(change_date)
    if d and d != _norm_date(generated_at):  # 即便显式给了，也拒绝等于抓取日
        return d, ""
    return "", "（变更日期缺失，来源档案未提供变更日）"


def _map_connector(name) -> str:
    """连接器标识 → 入库 ``source``；未知 → ``connector_unknown``（不阻断）。"""
    n = str(name or "").strip()
    return n if n in VALID_CONNECTORS else UNKNOWN_CONNECTOR


def _cget(company, key, default=None):
    """同时兼容 dataclass 与 dict 取值。"""
    if isinstance(company, dict):
        return company.get(key, default)
    return getattr(company, key, default)


def load(path: str) -> ConnectorProfile:
    """读取批次 JSON 并归一为 ``ConnectorProfile``；结构非法抛 ``ValueError``。"""
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("顶层 JSON 必须为对象")
    companies: List[CompanyProfile] = []
    for c in data.get("companies") or []:
        if not isinstance(c, dict):
            continue
        companies.append(CompanyProfile(
            name=str(c.get("name") or "").strip(),
            credit_code=str(c.get("credit_code") or "").strip(),
            profile=c.get("profile") or {},
            changes=c.get("changes") or [],
            lawsuits=c.get("lawsuits") or [],
            enforcements=c.get("enforcements") or [],
            url=str(c.get("url") or ""),
        ))
    return ConnectorProfile(
        schema_version=str(data.get("schema_version") or ""),
        generated_at=str(data.get("generated_at") or ""),
        connector=str(data.get("connector") or ""),
        companies=companies,
        error=str(data.get("error") or ""),
        status=str(data.get("status") or ""),
    )


def _match_enterprise(company) -> int:
    """按 ``credit_code`` 精确优先、``name`` 次之匹配企业；未命中返回 ``0``（不新建）。"""
    cc = (_cget(company, "credit_code", "") or "").strip()
    name = (_cget(company, "name", "") or "").strip()
    ents = db.list_enterprises(include_stopped=True)
    if cc:
        for e in ents:
            if e.credit_code and e.credit_code == cc:
                return int(e.id)
    if name:
        for e in ents:
            if e.name == name:
                return int(e.id)
    return 0


def build_events(company, source: str, generated_at: str,
                 ent_id: int, ent_name: str) -> List[Event]:
    """把单企业代查结果归一为 ``Event`` 列表（title 不含日期，稳定去重，§1.6.3.3）。"""
    events: List[Event] = []
    url = _cget(company, "url", "") or ""

    # 变更记录 → biz_change（R1 基线首选来源；变更日只进 occurred_at/摘要，不进 title）
    for ch in _cget(company, "changes", []) or []:
        if not isinstance(ch, dict):
            continue
        item = str(ch.get("change_item") or "").strip()
        before = str(ch.get("before") or "").strip() or "未知"
        after = str(ch.get("after") or "").strip() or "未知"
        occ, note = _occurred_at(ch.get("change_date"), generated_at=generated_at)
        if item:
            title = f"{ent_name}：{item}由{before}变更为{after}"
        else:
            title = f"{ent_name}：{item or '工商变更'}：{after}"
        summary = f"{item or '工商变更'}：{before} -> {after}{note}"
        events.append(Event(enterprise_id=ent_id, category="biz_change", title=title,
                            summary=summary, url=url, source=source, occurred_at=occ,
                            severity="medium", sentiment="neutral", relevance="high"))

    # 涉诉 → lawsuit
    for ls in _cget(company, "lawsuits", []) or []:
        if not isinstance(ls, dict):
            continue
        t = str(ls.get("title") or "涉诉").strip()
        cn = str(ls.get("case_no") or "").strip()
        events.append(Event(enterprise_id=ent_id, category="lawsuit",
                            title=f"{ent_name}：涉诉 {cn or t}",
                            summary=f"{t} {cn}".strip(), url=url, source=source,
                            occurred_at=_norm_date(ls.get("date")), severity="high",
                            sentiment="negative", relevance="high"))

    # 被执行/失信 → enforcement
    for enf in _cget(company, "enforcements", []) or []:
        if not isinstance(enf, dict):
            continue
        t = str(enf.get("title") or "被执行").strip()
        cn = str(enf.get("case_no") or "").strip()
        events.append(Event(enterprise_id=ent_id, category="enforcement",
                            title=f"{ent_name}：{t} {cn}".strip(),
                            summary=cn, url=url, source=source,
                            occurred_at=_norm_date(enf.get("date")), severity="high",
                            sentiment="negative", relevance="high"))
    return events


def _write_log(result: dict) -> None:
    """把本批次结果写入 ``data/logs/connector_<ts>.log``（失败忽略，绝不阻断）。"""
    try:
        log_dir = os.path.join(paths.data_dir(), "logs")
        os.makedirs(log_dir, exist_ok=True)
        path = os.path.join(log_dir, f"connector_{datetime.now():%Y%m%d-%H%M%S}.log")
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] connector={result.get('connector')} "
                    f"source={result.get('source')} companies={result.get('companies')} "
                    f"inserted={result.get('inserted')} skipped={result.get('skipped')}\n")
            for r in result.get("reasons", []):
                f.write(f"  - company={r.get('company')!r} reason={r.get('reason')}\n")
    except OSError:
        pass


# ---------------------------------------------------------------------------
# 运行
# ---------------------------------------------------------------------------
def run(path: str, dry_run: bool = False) -> Dict:
    """执行一个批次的入库；返回 ``{connector, source, companies, inserted, skipped, reasons}``。

    任何异常均被内部捕获（优雅跳过），**绝不抛出**，保证不阻塞主流程。
    """
    result: Dict = {"connector": "", "source": "", "companies": 0,
                    "inserted": 0, "skipped": 0, "reasons": [], "dry_run": dry_run}
    try:
        prof = load(path)
    except Exception as exc:  # noqa: BLE001 - 解析失败优雅跳过
        result["reasons"].append({"connector": "", "company": "", "reason": f"load_error: {exc}"})
        _write_log(result)
        log.warning("connector_ingest 解析失败：%s", exc)
        return result

    result["connector"] = prof.connector
    result["source"] = _map_connector(prof.connector)
    if result["source"] == UNKNOWN_CONNECTOR:
        log.warning("未知连接器 %r → source=%s（不阻断）", prof.connector, UNKNOWN_CONNECTOR)

    # 未授权（401/403/无权限）→ 记录原因，不重试、不弹窗
    err_blob = f"{prof.error} {prof.status}".lower()
    if any(w in err_blob for w in _UNAUTHORIZED_WORDS):
        result["reasons"].append({"connector": prof.connector, "company": "",
                                  "reason": "unauthorized"})
        _write_log(result)
        log.warning("连接器 %s 未授权，优雅跳过", prof.connector)
        return result

    if not prof.companies:
        result["reasons"].append({"connector": prof.connector, "company": "", "reason": "empty"})
        _write_log(result)
        return result

    result["companies"] = len(prof.companies)
    for comp in prof.companies:
        eid = _match_enterprise(comp)
        if not eid:
            result["skipped"] += 1
            result["reasons"].append({"connector": prof.connector, "company": comp.name,
                                      "reason": "no_enterprise"})
            log.warning("连接器 %s：企业 %r 未匹配到监控企业，跳过（不新建）",
                        prof.connector, comp.name)
            continue
        ent = db.get_enterprise(eid)
        ent_name = ent.name if ent else comp.name
        events = build_events(comp, result["source"], prof.generated_at,
                              ent_id=eid, ent_name=ent_name)
        for ev in events:
            if dry_run:
                result["inserted"] += 1
            elif db.insert_event(ev):
                result["inserted"] += 1
            else:
                result["skipped"] += 1  # 唯一键命中 → 幂等去重

    _write_log(result)
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="连接器代查档案 → Event 幂等入库")
    parser.add_argument("--file", required=True, help="批次 JSON 路径")
    parser.add_argument("--dry-run", action="store_true", help="只统计不落库")
    args = parser.parse_args(argv)

    if not os.path.isfile(args.file):
        print(f"[FAIL] 文件不存在：{args.file}")
        return 2
    result = run(args.file, dry_run=args.dry_run)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
