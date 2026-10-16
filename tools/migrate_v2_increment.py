"""BankRiskMonitor v2 增量一次性迁移脚本（幂等，可 ``--dry-run``）。

职责（ARCH-v2 §4）：

1. **备份**：迁移前 ``shutil.copy2(db, db + ".bak-v2inc-<YYYYmmdd-HHMMSS>")``，打印备份路径；
2. **DDL 迁移**：``enterprise.loan_date`` 幂等补列 + ``baseline_snapshot`` 表；
3. **存量清洗**：把 ``tianyancha_mcp`` 档案核验快照被污染的 ``occurred_at``
   （被写成入库日）清空为 ``''``（日期未知），并打印命中行数；
4. **放款日写入**：按企业名幂等写入三家真实企业的放款日。

约定：失败**非零退出**；先备份再改；重复执行除新增备份文件外**无其它副作用**。

用法::

    python tools/migrate_v2_increment.py            # 迁移默认库 src.core.paths.db_path()
    python tools/migrate_v2_increment.py --dry-run  # 只统计将改行数，不落库、不备份
    python tools/migrate_v2_increment.py --db <path> [--dry-run]
"""
from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import sys
from datetime import datetime
from typing import Dict, List, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.core import db  # noqa: E402
from src.core import paths  # noqa: E402

# 被污染识别条件（冻结，与 ARCH §1.4 一致）：
#   source='tianyancha_mcp' 且 occurred_at 恰等于入库日（created_at 前 10 位）
_POLLUTED_WHERE = ("source='tianyancha_mcp' "
                   "AND occurred_at = substr(created_at,1,10)")

# 三家真实企业放款日（按企业名匹配，幂等）
LOAN_DATES: Dict[str, str] = {
    "辽宁省沈抚示范区鑫泰房地产开发有限公司": "2026-09-29",
    "辽宁省沈抚示范区民泰房地产开发有限公司": "2026-09-29",
    "辽宁省沈抚示范区开发投资集团有限公司": "2026-10-10",
}


def open_db(db_file: str) -> sqlite3.Connection:
    """打开目标数据库连接（同 ``db.connect`` 的 row_factory/WAL 配置，但作用于指定文件）。"""
    conn = sqlite3.connect(db_file)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def apply_schema(conn: sqlite3.Connection) -> None:
    """在给定连接上执行 DDL：建全表 + 幂等补 ``enterprise.loan_date`` 列。

    复用 ``db.SCHEMA`` / ``db._ensure_column``，确保与运行期 DDL 同源、老库零破坏。
    """
    conn.executescript(db.SCHEMA)
    db._ensure_column(conn, "enterprise", "loan_date", "loan_date TEXT DEFAULT ''")


def _checkpoint(db_file: str) -> None:
    """尽力把 WAL 内容合并回主库文件（失败忽略），保证 ``copy2`` 备份完整。"""
    try:
        conn = sqlite3.connect(db_file)
        try:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            conn.commit()
        finally:
            conn.close()
    except sqlite3.Error:
        pass


def backup_db(db_file: str) -> str:
    """备份数据库文件，返回备份路径（命名与本库既有 ``monitor.db.bak-*`` 一致）。

    备份前先尽力 checkpoint（WAL → 主库），再 ``shutil.copy2``；时间戳精度为秒，
    同一秒内重复执行时追加 ``-N`` 序号，避免互相覆盖。
    """
    _checkpoint(db_file)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    dst = f"{db_file}.bak-v2inc-{ts}"
    if os.path.exists(dst):
        i = 1
        while os.path.exists(f"{dst}-{i}"):
            i += 1
        dst = f"{dst}-{i}"
    shutil.copy2(db_file, dst)
    return dst


def count_polluted(conn: sqlite3.Connection) -> int:
    """统计被污染的 ``tianyancha_mcp`` ``occurred_at`` 行数。"""
    return int(conn.execute(f"SELECT COUNT(*) FROM event WHERE {_POLLUTED_WHERE}").fetchone()[0])


def clean_occurred_at(conn: sqlite3.Connection, dry_run: bool = False) -> int:
    """清空被污染的 ``occurred_at``（→ ``''``），返回命中行数。``dry_run`` 只统计不改。"""
    n = count_polluted(conn)
    if not dry_run and n:
        conn.execute(f"UPDATE event SET occurred_at='' WHERE {_POLLUTED_WHERE}")
    return n


def set_loan_dates(conn: sqlite3.Connection, mapping: Dict[str, str] = None,
                   dry_run: bool = False) -> List[Tuple[str, int, str]]:
    """按企业名幂等写入放款日，返回 ``[(企业名, 企业id, 放款日)]``。"""
    mapping = mapping or LOAN_DATES
    applied: List[Tuple[str, int, str]] = []
    for name, date in mapping.items():
        rows = conn.execute("SELECT id FROM enterprise WHERE name=?", (name,)).fetchall()
        for r in rows:
            if not dry_run:
                conn.execute("UPDATE enterprise SET loan_date=? WHERE id=?", (date, r["id"]))
            applied.append((name, int(r["id"]), date))
    return applied


def verify(db_file: str) -> int:
    """迁移后自检：打印三家放款日与剩余污染行数；不满足返回非零。"""
    ok = True
    with open_db(db_file) as conn:
        print("—— 迁移后自检 ——")
        for name, date in LOAN_DATES.items():
            row = conn.execute("SELECT id, loan_date FROM enterprise WHERE name=?",
                               (name,)).fetchone()
            if row is None:
                print(f"  [WARN] 未找到企业：{name}（跳过）")
                continue
            got = row["loan_date"] or ""
            if got != date:
                ok = False
            flag = "OK" if got == date else "MISMATCH"
            print(f"  [{flag}] {name} id={row['id']} loan_date={got!r} (期望 {date})")
        remain = count_polluted(conn)
        if remain:
            ok = False
        print(f"  剩余被污染 occurred_at 行数：{remain}（期望 0）")
    return 0 if ok else 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="BankRiskMonitor v2 增量迁移（幂等）")
    parser.add_argument("--db", default="", help="数据库路径（默认 src.core.paths.db_path()）")
    parser.add_argument("--dry-run", action="store_true", help="只统计将改行数，不落库、不备份")
    args = parser.parse_args(argv)

    db_file = os.path.abspath(args.db) if args.db else paths.db_path()
    print("=" * 68)
    print("BankRiskMonitor v2 增量迁移")
    print("数据库:", db_file)
    print("模式:", "DRY-RUN（不落库）" if args.dry_run else "执行")
    print("=" * 68)

    if not os.path.isfile(db_file):
        print(f"[FAIL] 数据库文件不存在：{db_file}")
        return 2

    # 1) 备份（dry-run 不备份）
    if args.dry_run:
        print("[i] dry-run 跳过备份")
    else:
        try:
            bak = backup_db(db_file)
        except OSError as exc:
            print(f"[FAIL] 备份失败：{exc}")
            return 3
        print(f"[OK] 已备份 -> {bak}")

    try:
        conn = open_db(db_file)
        try:
            # 2) DDL 迁移（幂等补列 + 建表）；dry-run 不改库，故跳过
            if not args.dry_run:
                apply_schema(conn)
                print("[OK] DDL 迁移完成：enterprise.loan_date + baseline_snapshot（幂等）")
            else:
                print("[i] dry-run 跳过 DDL 迁移")

            # 3) 存量 occurred_at 清洗
            n = clean_occurred_at(conn, dry_run=args.dry_run)
            print(f"[OK] {'将清洗' if args.dry_run else '已清洗'}被污染 occurred_at 行数：{n}")

            # 4) 放款日写入
            applied = set_loan_dates(conn, LOAN_DATES, dry_run=args.dry_run)
            verb = "将写入" if args.dry_run else "已写入"
            if not applied:
                print(f"[WARN] {verb}公司放款日 0 条（无匹配企业名）")
            for name, eid, date in applied:
                print(f"[OK] {verb}放款日：{name} (id={eid}) -> {date}")
            if not args.dry_run:
                conn.commit()
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001 - 迁移失败非零退出
        print(f"[FAIL] 迁移异常：{exc!r}")
        return 4

    if args.dry_run:
        print("[i] dry-run 结束（未修改数据库）")
        return 0

    rc = verify(db_file)
    print("=" * 68)
    print("[OK] 迁移完成" if rc == 0 else "[WARN] 迁移完成但自检存在偏差")
    return rc


if __name__ == "__main__":
    sys.exit(main())
