"""v2 地基单测（T01）：``loan_date`` 幂等迁移 + ``baseline_snapshot`` 表/CRUD
+ CSV/XLSX 放款日列 + ``tools/migrate_v2_increment.py`` 幂等迁移脚本。

全部离线、使用临时 SQLite，不污染项目 data 目录。

运行：python -m pytest tests/test_db_migration.py -q
"""
import csv
import gc
import glob
import importlib.util
import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (ROOT, os.path.join(ROOT, "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

from src.core import db  # noqa: E402
from src.core import sync as sync_mod  # noqa: E402
from src.core.models import BaselineSnapshot, Enterprise, Event  # noqa: E402


def _load_migration_module():
    """按文件路径加载迁移脚本（tools 非包，避免命名空间包歧义）。"""
    path = os.path.join(ROOT, "tools", "migrate_v2_increment.py")
    spec = importlib.util.spec_from_file_location("migrate_v2_increment", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


mig = _load_migration_module()

TODAY = datetime.now().strftime("%Y-%m-%d")


class DbBase(unittest.TestCase):
    """把 SQLite 重定向到临时文件。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self._db_file = os.path.join(self._tmp.name, "test.db")
        patcher = mock.patch.object(db, "db_path", return_value=self._db_file)
        patcher.start()
        self._db_patcher = patcher
        db.init_db()

    def tearDown(self):
        self._db_patcher.stop()
        gc.collect()
        self._tmp.cleanup()

    def _table_columns(self, table: str):
        conn = sqlite3.connect(self._db_file)
        try:
            return [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
        finally:
            conn.close()

    def _has_table(self, table: str) -> bool:
        conn = sqlite3.connect(self._db_file)
        try:
            r = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                             (table,)).fetchone()
        finally:
            conn.close()
        return r is not None


# ---------------------------------------------------------------------------
# 1) DDL / 幂等补列
# ---------------------------------------------------------------------------
class SchemaTest(DbBase):
    def test_new_db_has_loan_date_and_baseline_table(self):
        self.assertIn("loan_date", self._table_columns("enterprise"))
        self.assertTrue(self._has_table("baseline_snapshot"))
        # baseline_snapshot 列与 DDL 一一对应
        cols = set(self._table_columns("baseline_snapshot"))
        self.assertEqual(cols, {
            "id", "enterprise_id", "loan_date", "legal_person", "reg_capital",
            "shareholders", "business_scope", "business_status", "abnormal_flag",
            "penalty_count", "lawsuit_count", "enforcement_status", "tax_status",
            "risk_tags", "source", "dim_sources", "note", "built_at", "updated_at",
        })

    def test_init_db_idempotent(self):
        db.init_db()
        db.init_db()
        self.assertEqual(self._table_columns("enterprise").count("loan_date"), 1)


class LegacyDbMigrationTest(unittest.TestCase):
    """老库（enterprise 无 loan_date、无 baseline_snapshot）经 init_db 幂等升级。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self._db_file = os.path.join(self._tmp.name, "legacy.db")
        p = mock.patch.object(db, "db_path", return_value=self._db_file)
        p.start()
        self._p = p
        # 构造老库
        conn = sqlite3.connect(self._db_file)
        conn.execute("CREATE TABLE enterprise "
                     "(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, credit_code TEXT)")
        conn.execute("INSERT INTO enterprise(name, credit_code) VALUES('老企业', '')")
        conn.commit()
        conn.close()

    def tearDown(self):
        self._p.stop()
        gc.collect()
        self._tmp.cleanup()

    def _cols(self):
        conn = sqlite3.connect(self._db_file)
        try:
            return [r[1] for r in conn.execute("PRAGMA table_info(enterprise)")]
        finally:
            conn.close()

    def test_ensure_column_added_once_and_data_preserved(self):
        db.init_db()
        self.assertIn("loan_date", self._cols())
        db.init_db()  # 再次执行不报错、不重复加列
        self.assertEqual(self._cols().count("loan_date"), 1)
        ent = db.get_enterprise(1)
        self.assertEqual(ent.name, "老企业")
        self.assertEqual(ent.loan_date, "")  # 老行补列后默认空


# ---------------------------------------------------------------------------
# 2) loan_date 写入 / 保真
# ---------------------------------------------------------------------------
class EnterpriseLoanDateTest(DbBase):
    def test_insert_with_loan_date(self):
        eid = db.upsert_enterprise("甲公司", loan_date="2026-01-15")
        self.assertEqual(db.get_enterprise(eid).loan_date, "2026-01-15")

    def test_update_without_loan_date_preserves_existing(self):
        eid = db.upsert_enterprise("甲公司", loan_date="2026-01-15")
        db.upsert_enterprise("甲公司", city="杭州市")  # 未传 loan_date
        self.assertEqual(db.get_enterprise(eid).loan_date, "2026-01-15")  # 不被清空

    def test_update_with_loan_date_overrides(self):
        eid = db.upsert_enterprise("甲公司", loan_date="2026-01-15")
        db.upsert_enterprise("甲公司", loan_date="2026-02-20")
        self.assertEqual(db.get_enterprise(eid).loan_date, "2026-02-20")

    def test_default_loan_date_empty(self):
        eid = db.upsert_enterprise("乙公司")
        self.assertEqual(db.get_enterprise(eid).loan_date, "")


# ---------------------------------------------------------------------------
# 3) baseline_snapshot CRUD
# ---------------------------------------------------------------------------
class BaselineCrudTest(DbBase):
    def _seed_ent(self) -> int:
        return db.upsert_enterprise("甲公司", loan_date="2026-09-29")

    def test_upsert_and_get(self):
        eid = self._seed_ent()
        row_id = db.upsert_baseline(
            eid, loan_date="2026-09-29", legal_person="张三", reg_capital="1000万",
            shareholders='["张三 100%"]', business_status="存续",
            abnormal_flag=0, penalty_count=0, lawsuit_count=1,
            enforcement_status="无", tax_status="正常", risk_tags="[]",
            source="manual", dim_sources='{"legal_person":"manual"}', note="人工补录")
        self.assertGreater(row_id, 0)
        snap = db.get_baseline(eid)
        self.assertIsInstance(snap, BaselineSnapshot)
        self.assertEqual(snap.enterprise_id, eid)
        self.assertEqual(snap.legal_person, "张三")
        self.assertEqual(snap.penalty_count, 0)
        self.assertEqual(snap.lawsuit_count, 1)
        self.assertEqual(snap.source, "manual")

    def test_upsert_is_single_row_and_preserves_built_at(self):
        eid = self._seed_ent()
        db.upsert_baseline(eid, legal_person="张三")
        first = db.get_baseline(eid)
        db.upsert_baseline(eid, legal_person="李四")  # 同企业二次写入
        again = db.get_baseline(eid)
        self.assertEqual(again.id, first.id)                 # 同一行（UNIQUE）
        self.assertEqual(again.legal_person, "李四")          # 值被更新
        self.assertEqual(again.built_at, first.built_at)      # built_at 不覆盖
        self.assertEqual(len(db.list_baselines()), 1)

    def test_get_missing_returns_none(self):
        self.assertIsNone(db.get_baseline(999))

    def test_delete(self):
        eid = self._seed_ent()
        db.upsert_baseline(eid, legal_person="张三")
        self.assertEqual(db.delete_baseline(eid), 1)
        self.assertIsNone(db.get_baseline(eid))
        self.assertEqual(db.delete_baseline(eid), 0)

    def test_unknown_fields_ignored(self):
        eid = self._seed_ent()
        db.upsert_baseline(eid, legal_person="张三", bogus="x")  # 非白名单字段被忽略
        self.assertEqual(db.get_baseline(eid).legal_person, "张三")


# ---------------------------------------------------------------------------
# 4) CSV / XLSX 放款日列
# ---------------------------------------------------------------------------
class SyncLoanDateTest(DbBase):
    CSV_CONTENT = ("企业名称,统一社会信用代码,省份,城市,授信备注,放款日期\n"
                   "甲公司,91330100MA27AAAA1A,浙江省,杭州市,授信500万,2026-09-29\n"
                   "乙公司,,,宁波市,,\n")

    def test_csv_roundtrip_with_loan_date(self):
        src = os.path.join(self._tmp.name, "in.csv")
        with open(src, "w", encoding="utf-8-sig", newline="") as f:
            f.write(self.CSV_CONTENT)
        self.assertEqual(sync_mod.import_enterprises_from_csv(src), 2)
        # 导入即写入放款日
        self.assertEqual(db.get_enterprise(1).loan_date, "2026-09-29")
        dst = os.path.join(self._tmp.name, "out.csv")
        self.assertEqual(sync_mod.export_enterprises_to_csv(dst), 2)
        with open(dst, "r", encoding="utf-8-sig", newline="") as f:
            rows = list(csv.reader(f))
        self.assertEqual(rows[0], sync_mod.CSV_HEADERS)
        self.assertEqual(rows[0], ["企业名称", "统一社会信用代码", "省份", "城市", "授信备注", "放款日期"])
        self.assertEqual(rows[1], ["甲公司", "91330100MA27AAAA1A", "浙江省", "杭州市", "授信500万", "2026-09-29"])

    def test_csv_missing_loan_date_column_tolerated(self):
        src = os.path.join(self._tmp.name, "old.csv")
        with open(src, "w", encoding="utf-8-sig", newline="") as f:
            f.write("企业名称,统一社会信用代码,省份,城市,授信备注\n"
                    "丙公司,,,,\n")
        self.assertEqual(sync_mod.import_enterprises_from_csv(src), 1)
        self.assertEqual(db.get_enterprise(1).loan_date, "")


# ---------------------------------------------------------------------------
# 5) 迁移脚本（备份 / 清洗 / 放款日 / 幂等 / dry-run）
# ---------------------------------------------------------------------------
class MigrationScriptTest(DbBase):
    """在临时库上构造污染数据与目标企业，验证迁移函数与 main() 幂等。"""

    NAMES = list(mig.LOAN_DATES.keys())

    def _seed(self, polluted: int = 7):
        for i, name in enumerate(self.NAMES, start=1):
            db.upsert_enterprise(name)  # 初始无放款日
        for i in range(polluted):
            # occurred_at 写成入库日（=created_at 前 10 位）→ 被污染；标题唯一以绕过唯一约束去重
            db.insert_event(Event(enterprise_id=1, category="biz_change",
                                  title=f"工商档案核验（天眼查）{i}", source="tianyancha_mcp",
                                  occurred_at=TODAY))
        # 一条真实日期的他源事件，不得被清洗
        db.insert_event(Event(enterprise_id=1, category="lawsuit", title="真实涉诉",
                              source="court_announce", occurred_at="2026-08-14"))

    def _polluted_now(self) -> int:
        conn = sqlite3.connect(self._db_file)
        try:
            return conn.execute(
                "SELECT COUNT(*) FROM event WHERE source='tianyancha_mcp' "
                "AND occurred_at=substr(created_at,1,10)").fetchone()[0]
        finally:
            conn.close()

    def test_clean_and_set_loan_dates(self):
        self._seed()
        conn = mig.open_db(self._db_file)
        try:
            mig.apply_schema(conn)
            self.assertEqual(mig.count_polluted(conn), 7)
            self.assertEqual(mig.clean_occurred_at(conn), 7)
            applied = mig.set_loan_dates(conn)
            conn.commit()
        finally:
            conn.close()
        self.assertEqual(self._polluted_now(), 0)
        self.assertEqual(len(applied), 3)
        # 三家放款日写入正确
        by_name = {e.name: e for e in db.list_enterprises()}
        for name, date in mig.LOAN_DATES.items():
            self.assertEqual(by_name[name].loan_date, date)
        # 他源真实日期未被清洗
        evs = db.list_events(source="court_announce")
        self.assertEqual(evs[0].occurred_at, "2026-08-14")

    def test_dry_run_changes_nothing(self):
        self._seed()
        conn = mig.open_db(self._db_file)
        try:
            n = mig.clean_occurred_at(conn, dry_run=True)
            applied = mig.set_loan_dates(conn, dry_run=True)
        finally:
            conn.close()
        self.assertEqual(n, 7)
        self.assertEqual(len(applied), 3)
        self.assertEqual(self._polluted_now(), 7)  # 未改
        self.assertEqual(db.get_enterprise(1).loan_date, "")  # 未写入

    def test_main_run_and_idempotent(self):
        self._seed()
        self.assertEqual(mig.main(["--db", self._db_file]), 0)
        backups = glob.glob(self._db_file + ".bak-v2inc-*")
        self.assertEqual(len(backups), 1)
        self.assertEqual(self._polluted_now(), 0)
        by_name = {e.name: e for e in db.list_enterprises()}
        for name, date in mig.LOAN_DATES.items():
            self.assertEqual(by_name[name].loan_date, date)
        # 再次执行：幂等、不报错、仅多出一个备份，其它无副作用
        self.assertEqual(mig.main(["--db", self._db_file]), 0)
        self.assertEqual(len(glob.glob(self._db_file + ".bak-v2inc-*")), 2)
        self.assertEqual(self._polluted_now(), 0)
        for name, date in mig.LOAN_DATES.items():
            self.assertEqual(by_name[name].loan_date, date)

    def test_main_dry_run_no_backup_no_change(self):
        self._seed()
        self.assertEqual(mig.main(["--db", self._db_file, "--dry-run"]), 0)
        self.assertEqual(glob.glob(self._db_file + ".bak-v2inc-*"), [])
        self.assertEqual(self._polluted_now(), 7)
        self.assertEqual(db.get_enterprise(1).loan_date, "")

    def test_main_missing_db_returns_code(self):
        self.assertEqual(mig.main(["--db", os.path.join(self._tmp.name, "nope.db")]), 2)


if __name__ == "__main__":
    unittest.main()
