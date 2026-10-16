"""T02 单元测试：放款前基线 + 放款后差异对比引擎（``core.baseline_compare``）。

覆盖：
1. 日期工具 ``parse_date`` / ``date_ge``（脏日期：空串/非法/带时间戳）；
2. 判定矩阵 N1/N2/N3/N4 + 同日边界 + 脏日期 → ``undetermined``；
3. 11 个对比维度各 ≥2 例（变更识别 / 较基线无变化）；
4. ``build_baseline`` 三源降级链（①源 ②手动 ③历史）+ 三维皆无返回 ``None``；
5. ``BaselineStore`` CRUD 薄封装。

``classify`` 为纯函数（无 DB/网络），相关用例毫秒级；涉及 DB 的用例把 SQLite
重定向到临时文件，不污染项目 ``data`` 目录。

运行：python -m pytest tests/test_baseline.py -q
"""
import gc
import os
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (ROOT, os.path.join(ROOT, "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

from src.core import baseline_compare as bc  # noqa: E402
from src.core import db  # noqa: E402
from src.core.models import BaselineSnapshot, Enterprise, Event  # noqa: E402

LOAN = "2026-09-29"
AFTER = "2026-10-05"   # 放款后
BEFORE = "2026-09-20"  # 放款前


def ent(loan_date=LOAN, ent_id=1, name="测试公司"):
    return Enterprise(id=ent_id, name=name, loan_date=loan_date)


def ev(category="biz_change", title="", occurred_at=AFTER, summary="",
       severity="medium", sentiment="neutral", source="test"):
    return Event(enterprise_id=1, category=category, title=title, summary=summary,
                 occurred_at=occurred_at, severity=severity, sentiment=sentiment,
                 source=source)


def baseline(**kw):
    return BaselineSnapshot(enterprise_id=kw.pop("enterprise_id", 1),
                            loan_date=kw.pop("loan_date", LOAN), **kw)


class DbBase(unittest.TestCase):
    """把 SQLite 重定向到临时文件。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self._db_file = os.path.join(self._tmp.name, "test.db")
        p = mock.patch.object(db, "db_path", return_value=self._db_file)
        p.start()
        self._db_patcher = p
        db.init_db()

    def tearDown(self):
        self._db_patcher.stop()
        gc.collect()
        self._tmp.cleanup()


# ---------------------------------------------------------------------------
# 1) 日期工具
# ---------------------------------------------------------------------------
class ParseDateTest(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(bc.parse_date("2026-09-29"), "2026-09-29")

    def test_empty_and_none(self):
        self.assertEqual(bc.parse_date(""), "")
        self.assertEqual(bc.parse_date(None), "")
        self.assertEqual(bc.parse_date("   "), "")

    def test_timestamp_truncated(self):
        self.assertEqual(bc.parse_date("2026-09-29 10:00:00"), "2026-09-29")
        self.assertEqual(bc.parse_date("2026-10-05T23:59:59"), "2026-10-05")

    def test_illegal(self):
        self.assertEqual(bc.parse_date("2026/10/5"), "")   # 非 ISO 分隔
        self.assertEqual(bc.parse_date("2026-9-5"), "")    # 位数不足
        self.assertEqual(bc.parse_date("abc"), "")
        self.assertEqual(bc.parse_date("20260929"), "")    # 缺分隔符

    def test_date_ge(self):
        self.assertTrue(bc.date_ge("2026-10-05", "2026-09-29"))
        self.assertTrue(bc.date_ge("2026-09-29", "2026-09-29"))   # 同日 → True
        self.assertFalse(bc.date_ge("2026-09-20", "2026-09-29"))
        self.assertFalse(bc.date_ge("", "2026-09-29"))            # 任一为空 → False
        self.assertFalse(bc.date_ge("2026-10-05", "2026/10/1"))   # 非法 → False


# ---------------------------------------------------------------------------
# 2) 判定矩阵 N1–N4 + 边界 + 脏日期
# ---------------------------------------------------------------------------
class ClassifyMatrixTest(unittest.TestCase):
    def test_N1_change(self):
        b = baseline(legal_person="张三")
        v = bc.classify(ev(title="甲公司：法定代表人由张三变更为赵六"),
                        ent(), b)
        self.assertEqual(v.bucket, bc.BUCKET_CHANGE)
        self.assertTrue(v.is_problem())
        self.assertTrue(v.diff_items)               # 有差异明细
        self.assertEqual(v.diff_items[0].dim, "legal_person")

    def test_N1_boundary_same_day(self):
        b = baseline(legal_person="张三")
        # 变更日 == 放款日 == 2026-09-29 → 同日也算问题（change）
        v = bc.classify(ev(title="甲公司：法定代表人由张三变更为赵六", occurred_at=LOAN),
                        ent(), b)
        self.assertEqual(v.bucket, bc.BUCKET_CHANGE)

    def test_N2_before_loan(self):
        b = baseline(legal_person="张三")
        v = bc.classify(ev(title="甲公司：法定代表人由张三变更为赵六", occurred_at=BEFORE),
                        ent(), b)
        self.assertEqual(v.bucket, bc.BUCKET_BASELINE)
        self.assertFalse(v.is_problem())

    def test_N2_no_change_after_loan(self):
        # 放款后事件，但内容与基线一致 → 无变化仅记录（baseline，非问题）
        b = baseline(legal_person="赵六")
        v = bc.classify(ev(title="甲公司：法定代表人由赵六变更为赵六"), ent(), b)
        self.assertEqual(v.bucket, bc.BUCKET_BASELINE)
        self.assertFalse(v.is_problem())
        self.assertEqual(v.diff_items, [])

    def test_N3_no_baseline(self):
        v = bc.classify(ev(title="甲公司：法定代表人由张三变更为赵六"), ent(), None)
        self.assertEqual(v.bucket, bc.BUCKET_UNDETERMINED)
        self.assertEqual(v.subtype, "no_baseline")
        self.assertIn("基线缺失", v.reason)
        self.assertFalse(v.is_problem())

    def test_N4_no_loan_date(self):
        v = bc.classify(ev(title="甲公司：法定代表人由张三变更为赵六"),
                        ent(loan_date=""), baseline(legal_person="张三"))
        self.assertEqual(v.bucket, bc.BUCKET_UNDETERMINED)
        self.assertEqual(v.subtype, "no_loan")
        self.assertIn("未设放款日", v.reason)
        self.assertFalse(v.is_problem())  # 无放款日 ≠ 非问题（不得误判为 baseline）

    def test_ent_none_is_no_loan(self):
        v = bc.classify(ev(title="x"), None, None)
        self.assertEqual(v.subtype, "no_loan")

    def test_dirty_event_date(self):
        b = baseline(legal_person="张三")
        for dirty in ("", "2026/10/5", "abc"):
            with self.subTest(dirty=dirty):
                v = bc.classify(ev(title="甲公司：法定代表人由张三变更为赵六",
                                   occurred_at=dirty), ent(), b)
                self.assertEqual(v.bucket, bc.BUCKET_UNDETERMINED)
                self.assertEqual(v.subtype, "no_date")


# ---------------------------------------------------------------------------
# 3) 11 维对比（变更 / 未变更）
# ---------------------------------------------------------------------------
class DimensionCompareTest(unittest.TestCase):
    """每个维度 2 例：变更识别（change）+ 较基线无变化（baseline）。"""

    def _check(self, b, event, expect):
        v = bc.classify(event, ent(), b)
        self.assertEqual(v.bucket, expect,
                         msg=f"{event.title} -> {v.bucket}({v.reason})")

    def test_legal_person(self):
        self._check(baseline(legal_person="张三"),
                    ev(title="甲公司：法定代表人由张三变更为赵六"), bc.BUCKET_CHANGE)
        self._check(baseline(legal_person="赵六"),
                    ev(title="甲公司：法定代表人由赵六变更为赵六"), bc.BUCKET_BASELINE)

    def test_reg_capital(self):
        self._check(baseline(reg_capital="1000万"),
                    ev(title="甲公司：注册资本由1000万变更为3000万"), bc.BUCKET_CHANGE)
        self._check(baseline(reg_capital="3000万"),
                    ev(title="甲公司：注册资本由3000万变更为3000万"), bc.BUCKET_BASELINE)

    def test_shareholders(self):
        self._check(baseline(shareholders="李四 100%"),
                    ev(title="甲公司：股东由李四变更为王五"), bc.BUCKET_CHANGE)
        self._check(baseline(shareholders="王五"),
                    ev(title="甲公司：股东由王五变更为王五"), bc.BUCKET_BASELINE)

    def test_business_scope(self):
        self._check(baseline(business_scope="房地产开发"),
                    ev(title="甲公司：经营范围由房地产开发变更为房地产开发；物业管理"),
                    bc.BUCKET_CHANGE)
        self._check(baseline(business_scope="房地产开发；物业管理"),
                    ev(title="甲公司：经营范围由房地产开发变更为房地产开发；物业管理"),
                    bc.BUCKET_BASELINE)

    def test_business_status(self):
        self._check(baseline(business_status="存续"),
                    ev(title="甲公司：登记状态由存续变更为注销"), bc.BUCKET_CHANGE)
        self._check(baseline(business_status="注销"),
                    ev(title="甲公司：登记状态由存续变更为注销"), bc.BUCKET_BASELINE)

    def test_abnormal_flag(self):
        # 放款后新列入经营异常 → change
        self._check(baseline(abnormal_flag=0),
                    ev(title="甲公司：被列入经营异常名录"), bc.BUCKET_CHANGE)
        # 基线已列入 → 无新增 → 仅记录
        self._check(baseline(abnormal_flag=1),
                    ev(title="甲公司：被列入经营异常名录"), bc.BUCKET_BASELINE)

    def test_penalty_count(self):
        # 放款后新增行政处罚（基线 0 条 → 1 条）→ change
        self._check(baseline(penalty_count=0),
                    ev(title="甲公司：新增行政处罚（罚款50万）"), bc.BUCKET_CHANGE)
        # 该维基线缺失（None）→ 无法判定增量，仅记录
        self._check(baseline(penalty_count=None),
                    ev(title="甲公司：新增行政处罚（罚款50万）"), bc.BUCKET_BASELINE)

    def test_lawsuit_count(self):
        # 涉诉按"较基线新增"识别，不看存量总量
        self._check(baseline(lawsuit_count=1),
                    ev(category="lawsuit", title="甲公司：涉诉 (2026)辽01民初123号"),
                    bc.BUCKET_CHANGE)
        self._check(baseline(lawsuit_count=None),
                    ev(category="lawsuit", title="甲公司：涉诉 (2026)辽01民初456号"),
                    bc.BUCKET_BASELINE)

    def test_enforcement_status(self):
        self._check(baseline(enforcement_status=""),
                    ev(category="enforcement", title="甲公司：被列为被执行人（案号 X）"),
                    bc.BUCKET_CHANGE)
        self._check(baseline(enforcement_status="被执行人"),
                    ev(category="enforcement", title="甲公司：被列为被执行人（案号 Y）"),
                    bc.BUCKET_BASELINE)

    def test_tax_status(self):
        self._check(baseline(tax_status=""),
                    ev(category="enforcement", title="甲公司：重大税收违法失信"), bc.BUCKET_CHANGE)
        self._check(baseline(tax_status="重大税收违法"),
                    ev(category="enforcement", title="甲公司：重大税收违法失信"), bc.BUCKET_BASELINE)

    def test_risk_tags(self):
        # 全量类别统一适用：放款后新增负面舆情 → 风险标签增量 → change
        self._check(baseline(risk_tags="[]"),
                    ev(category="news", title="甲公司：媒体曝出债务违约",
                       sentiment="negative", severity="high"), bc.BUCKET_CHANGE)
        # 基线已含相同标签 → 无新增 → 仅记录
        self._check(baseline(risk_tags='["负面", "高风险"]'),
                    ev(category="news", title="甲公司：媒体曝出债务违约",
                       sentiment="negative", severity="high"), bc.BUCKET_BASELINE)

    def test_all_11_dims_covered(self):
        """确保 11 维（STATE_DIMS ∪ NEW_DIMS）与 DIMENSIONS 一一对应。"""
        self.assertEqual(set(bc.STATE_DIMS) | set(bc.NEW_DIMS), {d for d, _ in bc.DIMENSIONS})
        self.assertEqual(len(bc.DIMENSIONS), 11)


# ---------------------------------------------------------------------------
# 4) dimension_of 映射
# ---------------------------------------------------------------------------
class DimensionOfTest(unittest.TestCase):
    def test_category_mapping(self):
        self.assertEqual(bc.dimension_of(ev(category="lawsuit", title="x")), "lawsuit_count")
        self.assertEqual(bc.dimension_of(ev(category="enforcement", title="被列为被执行人")),
                         "enforcement_status")
        self.assertEqual(bc.dimension_of(ev(category="enforcement", title="重大税收违法失信")),
                         "tax_status")
        self.assertEqual(bc.dimension_of(ev(category="news", title="任意新闻")), "risk_tags")

    def test_keyword_mapping(self):
        for title, dim in (("法定代表人变更", "legal_person"),
                           ("注册资本变更", "reg_capital"),
                           ("股东变更", "shareholders"),
                           ("经营范围变更", "business_scope"),
                           ("经营异常名录", "abnormal_flag"),
                           ("行政处罚决定", "penalty_count"),
                           ("登记状态注销", "business_status")):
            with self.subTest(title=title):
                self.assertEqual(bc.dimension_of(ev(title=title)), dim)


# ---------------------------------------------------------------------------
# 5) build_baseline 三源降级
# ---------------------------------------------------------------------------
class BuildBaselineTest(DbBase):
    def test_no_loan_date_returns_none(self):
        self.assertIsNone(bc.build_baseline(ent(loan_date="")))

    def test_all_empty_returns_none(self):
        # 有放款日，但三源皆无数据 → None（基线缺失）
        self.assertIsNone(bc.build_baseline(ent()))

    def test_source_priority_auto(self):
        # ① 数据源带日期变更记录（仅消费放款前记录）
        sources = [{
            "source": "tyc_mcp",
            "profile": {"legal_person": "张三", "reg_capital": "1000万",
                        "shareholders": [{"name": "李四", "ratio": "60%"}]},
            "changes": [
                {"change_item": "注册资本", "before": "500万", "after": "1000万",
                 "change_date": "2026-09-01"},   # 放款前 → 采纳
                {"change_item": "法定代表人", "before": "张三", "after": "赵六",
                 "change_date": "2026-10-05"},   # 放款后 → 忽略
            ],
        }]
        snap = bc.build_baseline(ent(), sources)
        self.assertIsNotNone(snap)
        self.assertEqual(snap.legal_person, "张三")
        self.assertEqual(snap.reg_capital, "1000万")
        self.assertIn("李四", snap.shareholders)
        self.assertEqual(snap.source, "tyc_mcp")

    def test_source_priority_manual(self):
        # ② 用户手动补录（baseline_snapshot 行）
        db.upsert_baseline(1, legal_person="李四", lawsuit_count=2, source="manual")
        snap = bc.build_baseline(ent())
        self.assertIsNotNone(snap)
        self.assertEqual(snap.legal_person, "李四")
        self.assertEqual(snap.lawsuit_count, 2)
        self.assertEqual(snap.source, "manual")

    def test_source_priority_history(self):
        # ③ 放款前已采集的历史事件（放款后事件不得混入）
        db.insert_event(Event(enterprise_id=1, category="lawsuit", title="甲公司：涉诉旧案",
                              source="court_announce", occurred_at="2026-08-01"))
        db.insert_event(Event(enterprise_id=1, category="biz_change",
                              title="甲公司：法定代表人由张三变更为赵六",
                              source="qcc_api", occurred_at="2026-08-15"))
        db.insert_event(Event(enterprise_id=1, category="lawsuit", title="甲公司：涉诉新案",
                              source="court_wenshu", occurred_at="2026-10-05"))  # 放款后
        snap = bc.build_baseline(ent())
        self.assertIsNotNone(snap)
        self.assertEqual(snap.legal_person, "赵六")
        self.assertEqual(snap.lawsuit_count, 1)   # 仅放款前 1 条
        self.assertEqual(snap.source, "history")

    def test_mixed_source(self):
        # ① 提供 legal_person，② 手动提供 reg_capital → 整体 source='mixed'
        db.upsert_baseline(1, reg_capital="2000万", source="manual")
        sources = [{"source": "tyc_mcp", "profile": {"legal_person": "张三"}}]
        snap = bc.build_baseline(ent(), sources)
        self.assertEqual(snap.source, "mixed")
        dim_src = bc.json.loads(snap.dim_sources)
        self.assertEqual(dim_src.get("legal_person"), "tyc_mcp")
        self.assertEqual(dim_src.get("reg_capital"), "manual")

    def test_priority_auto_wins_over_manual(self):
        # 同维：① 优先于 ②
        db.upsert_baseline(1, legal_person="手动李四", source="manual")
        sources = [{"source": "tyc_mcp", "profile": {"legal_person": "自动张三"}}]
        snap = bc.build_baseline(ent(), sources)
        self.assertEqual(snap.legal_person, "自动张三")

    def test_source_spec_exception_isolated(self):
        # 单个来源异常不影响其余来源（绝不抛异常）
        bad = {"source": "boom", "changes": "not-a-list"}
        good = [bad, {"source": "tyc_mcp", "profile": {"legal_person": "张三"}}]
        snap = bc.build_baseline(ent(), good)
        self.assertIsNotNone(snap)
        self.assertEqual(snap.legal_person, "张三")


# ---------------------------------------------------------------------------
# 6) BaselineStore
# ---------------------------------------------------------------------------
class BaselineStoreTest(DbBase):
    def test_save_get_list_delete(self):
        snap = baseline(enterprise_id=1, legal_person="张三", lawsuit_count=1, source="manual")
        row_id = bc.BaselineStore.save(snap)
        self.assertGreater(row_id, 0)
        got = bc.BaselineStore.get(1)
        self.assertEqual(got.legal_person, "张三")
        self.assertEqual(got.lawsuit_count, 1)
        self.assertEqual(len(bc.BaselineStore.list_all()), 1)
        # 二次保存幂等（同一行）
        bc.BaselineStore.save(baseline(enterprise_id=1, legal_person="李四", source="manual"))
        self.assertEqual(bc.BaselineStore.get(1).id, got.id)
        self.assertEqual(bc.BaselineStore.get(1).legal_person, "李四")
        self.assertEqual(bc.BaselineStore.delete(1), 1)
        self.assertIsNone(bc.BaselineStore.get(1))

    def test_build_and_save(self):
        db.insert_event(Event(enterprise_id=1, category="lawsuit", title="甲公司：涉诉",
                              source="court_announce", occurred_at="2026-08-01"))
        snap = bc.BaselineStore().build_and_save(ent())
        self.assertIsNotNone(snap)
        self.assertIsNotNone(bc.BaselineStore.get(1))
        self.assertEqual(bc.BaselineStore.get(1).lawsuit_count, 1)


# ---------------------------------------------------------------------------
# 7) 回归（QA 发现缺陷修复）：FIX-1 历史/自动源取"放款前最近状态"
# ---------------------------------------------------------------------------
class ParseDateSemanticRangeTest(unittest.TestCase):
    """FIX-2：``parse_date`` 追加月/日范围校验（非法月/日 → ''）。"""

    def test_invalid_month(self):
        self.assertEqual(bc.parse_date("2026-13-45"), "")
        self.assertEqual(bc.parse_date("2026-00-10"), "")

    def test_invalid_day(self):
        self.assertEqual(bc.parse_date("2026-10-00"), "")
        self.assertEqual(bc.parse_date("2026-04-31"), "")   # 4 月仅 30 天
        self.assertEqual(bc.parse_date("2026-02-30"), "")   # 2 月上限 28/29

    def test_leap_year(self):
        self.assertEqual(bc.parse_date("2026-02-29"), "")            # 2026 非闰年
        self.assertEqual(bc.parse_date("2024-02-29"), "2024-02-29")  # 2024 闰年
        self.assertEqual(bc.parse_date("2000-02-29"), "2000-02-29")  # 400 整除 → 闰年
        self.assertEqual(bc.parse_date("1900-02-29"), "")            # 100 整除非 400 → 平年

    def test_valid_boundaries_still_ok(self):
        self.assertEqual(bc.parse_date("2026-01-31"), "2026-01-31")
        self.assertEqual(bc.parse_date("2026-12-31"), "2026-12-31")
        self.assertEqual(bc.parse_date("2026-09-29 10:00:00"), "2026-09-29")
        self.assertEqual(bc.parse_date("  2026-09-29  "), "2026-09-29")

    def test_semantically_invalid_date_classify_undetermined(self):
        b = baseline(legal_person="张三")
        v = bc.classify(ev(title="甲公司：法定代表人由张三变更为赵六",
                           occurred_at="2026-13-45"), ent(), b)
        self.assertEqual(v.bucket, bc.BUCKET_UNDETERMINED)
        self.assertEqual(v.subtype, "no_date")


class ClassifyOrderTest(unittest.TestCase):
    """FIX-3：放款前事件先判 ``baseline``（无需基线参与），放款后才检查无基线。"""

    def test_preloan_event_without_baseline_is_baseline(self):
        # 放款前事件 + 无基线快照 → baseline（放款前一律记录、不视为问题）
        v = bc.classify(ev(title="甲公司：法定代表人由张三变更为赵六",
                           occurred_at=BEFORE), ent(), None)
        self.assertEqual(v.bucket, bc.BUCKET_BASELINE)
        self.assertEqual(v.subtype, "baseline")
        self.assertFalse(v.is_problem())

    def test_postloan_event_without_baseline_is_undetermined(self):
        # 放款后事件 + 无基线 → undetermined(no_baseline)（不得降级）
        v = bc.classify(ev(title="甲公司：法定代表人由张三变更为赵六",
                           occurred_at=AFTER), ent(), None)
        self.assertEqual(v.bucket, bc.BUCKET_UNDETERMINED)
        self.assertEqual(v.subtype, "no_baseline")

    def test_no_loan_still_undetermined(self):
        # 无放款日优先级最高
        v = bc.classify(ev(title="x", occurred_at=BEFORE), ent(loan_date=""), None)
        self.assertEqual(v.subtype, "no_loan")


class HistoryLatestPreLoanStateTest(DbBase):
    """FIX-1：历史来源建基线取"放款前最近一次"变更后的状态。"""

    def _seed(self):
        # 放款前两次法定代表人变更：8 月 张三→李四，9 月 李四→王五
        db.insert_event(Event(enterprise_id=1, category="biz_change",
                              title="甲公司：法定代表人由张三变更为李四",
                              source="gsxt", occurred_at="2026-08-01"))
        db.insert_event(Event(enterprise_id=1, category="biz_change",
                              title="甲公司：法定代表人由李四变更为王五",
                              source="gsxt", occurred_at="2026-09-10"))

    def test_history_uses_latest_preloan_state(self):
        self._seed()
        s = bc.build_baseline(ent())
        self.assertIsNotNone(s)
        self.assertEqual(s.legal_person, "王五")
        self.assertEqual(s.source, "history")

    def test_history_latest_no_false_negative(self):
        self._seed()
        s = bc.build_baseline(ent())
        v = bc.classify(ev(title="甲公司：法定代表人由王五变更为李四"), ent(), s)
        self.assertEqual(v.bucket, bc.BUCKET_CHANGE)

    def test_history_latest_no_false_positive(self):
        self._seed()
        s = bc.build_baseline(ent())
        v = bc.classify(ev(title="甲公司：法定代表人由赵六变更为王五"), ent(), s)
        self.assertEqual(v.bucket, bc.BUCKET_BASELINE)

    def test_history_three_changes_takes_last(self):
        db.insert_event(Event(enterprise_id=1, category="biz_change",
                              title="甲公司：法定代表人由甲变更为乙",
                              source="gsxt", occurred_at="2026-08-01"))
        db.insert_event(Event(enterprise_id=1, category="biz_change",
                              title="甲公司：法定代表人由乙变更为丙",
                              source="gsxt", occurred_at="2026-08-20"))
        db.insert_event(Event(enterprise_id=1, category="biz_change",
                              title="甲公司：法定代表人由丙变更为丁",
                              source="gsxt", occurred_at="2026-09-10"))
        s = bc.build_baseline(ent())
        self.assertEqual(s.legal_person, "丁")

    def test_history_ignores_postloan_event(self):
        # 放款后事件不得进入基线（即便日期更近）
        self._seed()
        db.insert_event(Event(enterprise_id=1, category="biz_change",
                              title="甲公司：法定代表人由王五变更为赵六",
                              source="gsxt", occurred_at="2026-10-05"))
        s = bc.build_baseline(ent())
        self.assertEqual(s.legal_person, "王五")


class AutoSourceLatestPreLoanChangeTest(DbBase):
    """FIX-1：数据源 changes 也按 change_date 取放款前最近一次。"""

    def test_changes_take_latest_preloan(self):
        sources = [{
            "source": "tyc_mcp",
            "changes": [
                {"change_item": "法定代表人", "before": "张三", "after": "李四",
                 "change_date": "2026-08-01"},
                {"change_item": "法定代表人", "before": "李四", "after": "王五",
                 "change_date": "2026-09-10"},
                {"change_item": "法定代表人", "before": "王五", "after": "赵六",
                 "change_date": "2026-10-05"},   # 放款后 → 忽略
            ],
        }]
        snap = bc.build_baseline(ent(), sources)
        self.assertIsNotNone(snap)
        self.assertEqual(snap.legal_person, "王五")

    def test_profile_outranks_history_same_dim(self):
        # 跨来源优先级不变：①自动源 profile 已写入的维，历史来源不覆盖
        db.insert_event(Event(enterprise_id=1, category="biz_change",
                              title="甲公司：法定代表人由张三变更为李四",
                              source="gsxt", occurred_at="2026-08-01"))
        sources = [{"source": "tyc_mcp", "profile": {"legal_person": "王五"}}]
        snap = bc.build_baseline(ent(), sources)
        self.assertEqual(snap.legal_person, "王五")


if __name__ == "__main__":
    unittest.main()
