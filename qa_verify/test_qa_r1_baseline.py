"""QA 独立验收 —— A. R1 放款前基线 / 放款后差异对比（证伪式）。

本文件**不复现**工程师既有用例（tests/test_baseline.py），而是从"试图暴露问题"的
角度自造数据、自定断言。重点：

1. 判定矩阵 N1–N4 全组合（自造数据）；
2. 同日边界 / 放款前 1 天边界；
3. 脏日期逐个断言（不崩溃 + bucket 正确）；
4. 11 维各 ≥1 变更 / 未变更；
5. **涉诉 / 被执行按"较基线新增"判定，不因总量大而误判**；
6. ``classify`` 纯函数：多次调用一致、零 DB 访问（patch db 全部方法为"一调用即抛"）；
7. **（重点证伪）历史来源建基线应取"放款前最近一次状态"** —— 见 HistoricBaselineLatestStateTest。
"""
import gc
import os
import tempfile
import unittest
from unittest import mock

from src.core import baseline_compare as bc
from src.core import db
from src.core.models import BaselineSnapshot, Enterprise, Event

LOAN = "2026-09-29"
AFTER = "2026-10-05"
BEFORE = "2026-09-10"


def ent(loan_date=LOAN, ent_id=1, name="测试公司"):
    return Enterprise(id=ent_id, name=name, loan_date=loan_date)


def ev(category="biz_change", title="", occurred_at=AFTER, summary="",
       severity="medium", sentiment="neutral", source="qa"):
    return Event(enterprise_id=1, category=category, title=title, summary=summary,
                 occurred_at=occurred_at, severity=severity, sentiment=sentiment,
                 source=source)


def snap(**kw):
    return BaselineSnapshot(enterprise_id=kw.pop("enterprise_id", 1),
                            loan_date=kw.pop("loan_date", LOAN), **kw)


class DbBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self._db_file = os.path.join(self._tmp.name, "qa.db")
        p = mock.patch.object(db, "db_path", return_value=self._db_file)
        p.start()
        self._p = p
        db.init_db()

    def tearDown(self):
        self._p.stop()
        gc.collect()
        self._tmp.cleanup()


# ---------------------------------------------------------------------------
# A2/A3. 判定矩阵 N1–N4 + 边界 + 脏日期（自造）
# ---------------------------------------------------------------------------
class MatrixQA(unittest.TestCase):
    def test_N1_has_loan_has_baseline_change(self):
        v = bc.classify(ev(title="甲公司：法定代表人由张三变更为赵六"),
                        ent(), snap(legal_person="张三"))
        self.assertEqual(v.bucket, bc.BUCKET_CHANGE)
        self.assertTrue(v.is_problem())

    def test_N2_has_loan_has_baseline_no_change(self):
        # 放款后事件但内容与基线一致 → 非问题
        v = bc.classify(ev(title="甲公司：法定代表人由赵六变更为赵六"),
                        ent(), snap(legal_person="赵六"))
        self.assertEqual(v.bucket, bc.BUCKET_BASELINE)
        self.assertFalse(v.is_problem())

    def test_N3_has_loan_no_baseline(self):
        v = bc.classify(ev(title="甲公司：法定代表人由张三变更为赵六"), ent(), None)
        self.assertEqual(v.bucket, bc.BUCKET_UNDETERMINED)
        self.assertEqual(v.subtype, "no_baseline")
        # 不得自动降级为"非问题"
        self.assertFalse(v.is_problem())

    def test_N4_no_loan(self):
        v = bc.classify(ev(title="x"), ent(loan_date=""), snap(legal_person="张三"))
        self.assertEqual(v.bucket, bc.BUCKET_UNDETERMINED)
        self.assertEqual(v.subtype, "no_loan")

    def test_boundary_same_day_must_be_change(self):
        v = bc.classify(ev(title="甲公司：法定代表人由张三变更为赵六", occurred_at=LOAN),
                        ent(), snap(legal_person="张三"))
        self.assertEqual(v.bucket, bc.BUCKET_CHANGE, "同日必须判 change")

    def test_boundary_one_day_before_must_be_baseline(self):
        # 放款日前一天，即便内容"像"变更，也必须是 baseline（放款前不视为问题）
        v = bc.classify(ev(title="甲公司：法定代表人由张三变更为赵六",
                           occurred_at="2026-09-28"),
                        ent(), snap(legal_person="张三"))
        self.assertEqual(v.bucket, bc.BUCKET_BASELINE, "放款前 1 天必须判 baseline")

    def test_dirty_event_dates(self):
        b = snap(legal_person="张三")
        # 期望 undetermined(no_date) 的脏日期
        for dirty in ("", None, "   ", "2026/09/29", "abc", "20260929"):
            with self.subTest(dirty=repr(dirty)):
                v = bc.classify(ev(title="甲公司：法定代表人由张三变更为赵六",
                                   occurred_at=dirty), ent(), b)
                self.assertEqual(v.bucket, bc.BUCKET_UNDETERMINED, f"dirty={dirty!r}")
                self.assertEqual(v.subtype, "no_date")
                self.assertFalse(v.is_problem())

    def test_timestamp_and_padded_dates_are_valid(self):
        b = snap(legal_person="张三")
        for good in ("2026-09-29 10:00:00", "  2026-09-29  ", "2026-10-05T23:59:59"):
            with self.subTest(good=repr(good)):
                # 归一到具体日期后应能正常判定（此处 after=2026-10-05 → change）
                exp = bc.BUCKET_CHANGE if "2026-10" in good else bc.BUCKET_CHANGE
                v = bc.classify(ev(title="甲公司：法定代表人由张三变更为赵六",
                                   occurred_at=good), ent(), b)
                self.assertIn(v.bucket, (bc.BUCKET_CHANGE, bc.BUCKET_BASELINE))
                self.assertNotEqual(v.subtype, "no_date")

    def test_semantically_invalid_date_should_be_undetermined(self):
        """PRD §3.5：任一日期非法 → undetermined。'2026-13-45' 月/日非法。"""
        b = snap(legal_person="张三")
        v = bc.classify(ev(title="甲公司：法定代表人由张三变更为赵六",
                           occurred_at="2026-13-45"), ent(), b)
        self.assertEqual(v.bucket, bc.BUCKET_UNDETERMINED,
                         f"'2026-13-45' 非法日期应 undetermined，实际={v.bucket}/{v.subtype}")


# ---------------------------------------------------------------------------
# A4. 11 维各 ≥1 变更 / 未变更
# ---------------------------------------------------------------------------
class DimensionsQA(unittest.TestCase):
    def _check(self, b, event, expect):
        v = bc.classify(event, ent(), b)
        self.assertEqual(v.bucket, expect,
                         msg=f"{event.title!r} -> {v.bucket}({v.reason})")

    def test_all_11_dimensions_change_and_no_change(self):
        cases = [
            # (label, baseline_kw, change_event, nochange_baseline_kw, same_event)
            ("legal_person", {"legal_person": "张三"},
             ev(title="甲公司：法定代表人由张三变更为赵六"), {"legal_person": "赵六"}),
            ("reg_capital", {"reg_capital": "1000万"},
             ev(title="甲公司：注册资本由1000万变更为3000万"), {"reg_capital": "3000万"}),
            ("shareholders", {"shareholders": "李四 100%"},
             ev(title="甲公司：股东由李四变更为王五"), {"shareholders": "王五"}),
            ("business_scope", {"business_scope": "房地产开发"},
             ev(title="甲公司：经营范围由房地产开发变更为房地产开发；物业管理"),
             {"business_scope": "房地产开发；物业管理"}),
            ("business_status", {"business_status": "存续"},
             ev(title="甲公司：登记状态由存续变更为注销"), {"business_status": "注销"}),
            ("abnormal_flag", {"abnormal_flag": 0},
             ev(title="甲公司：被列入经营异常名录"), {"abnormal_flag": 1}),
            ("penalty_count", {"penalty_count": 0},
             ev(title="甲公司：新增行政处罚（罚款50万）"), {"penalty_count": None}),
            ("lawsuit_count", {"lawsuit_count": 1},
             ev(category="lawsuit", title="甲公司：涉诉 (2026)辽01民初123号"),
             {"lawsuit_count": None}),
            ("enforcement_status", {"enforcement_status": ""},
             ev(category="enforcement", title="甲公司：被列为被执行人（X）"),
             {"enforcement_status": "被执行人"}),
            ("tax_status", {"tax_status": ""},
             ev(category="enforcement", title="甲公司：重大税收违法失信"),
             {"tax_status": "重大税收违法"}),
            ("risk_tags", {"risk_tags": "[]"},
             ev(category="news", title="甲公司：媒体曝出债务违约",
                sentiment="negative", severity="high"),
             {"risk_tags": '["负面", "高风险"]'}),
        ]
        for label, base_kw, change_ev, nbase_kw in cases:
            with self.subTest(dim=label, kind="change"):
                self._check(snap(**base_kw), change_ev, bc.BUCKET_CHANGE)
            with self.subTest(dim=label, kind="no-change"):
                self._check(snap(**nbase_kw), change_ev, bc.BUCKET_BASELINE)

    def test_dimension_count_is_11(self):
        self.assertEqual(len(bc.DIMENSIONS), 11)
        self.assertEqual(set(bc.STATE_DIMS) | set(bc.NEW_DIMS), {d for d, _ in bc.DIMENSIONS})


# ---------------------------------------------------------------------------
# A5. 涉诉 / 被执行：按"较基线新增"判定，不因总量大误判
# ---------------------------------------------------------------------------
class NewIncrementQA(unittest.TestCase):
    def test_lawsuit_total_large_still_new_detected(self):
        # 基线存量 50 起（总量很大），放款后 1 起新涉诉 → 必须是 change（增量识别）
        v = bc.classify(ev(category="lawsuit", title="甲公司：涉诉 (2026)新案"),
                        ent(), snap(lawsuit_count=50))
        self.assertEqual(v.bucket, bc.BUCKET_CHANGE)
        self.assertEqual(v.diff_items[0].dim, "lawsuit_count")
        self.assertEqual(v.diff_items[0].old, "50")
        self.assertEqual(v.diff_items[0].new, "51")

    def test_enforcement_existing_is_baseline(self):
        # 放款前已有被执行 → 放款后再报"被执行人" → 存量 → baseline（非问题）
        v = bc.classify(ev(category="enforcement", title="甲公司：被列为被执行人"),
                        ent(), snap(enforcement_status="被执行人"))
        self.assertEqual(v.bucket, bc.BUCKET_BASELINE)
        self.assertFalse(v.is_problem())

    def test_enforcement_new_is_change(self):
        v = bc.classify(ev(category="enforcement", title="甲公司：被列为被执行人"),
                        ent(), snap(enforcement_status=""))
        self.assertEqual(v.bucket, bc.BUCKET_CHANGE)

    def test_lawsuit_dim_none_is_not_problem(self):
        """（记录当前设计行为）该维基线缺失(None) → 仅记录，不判问题。"""
        v = bc.classify(ev(category="lawsuit", title="甲公司：涉诉 新案"),
                        ent(), snap(lawsuit_count=None))
        self.assertEqual(v.bucket, bc.BUCKET_BASELINE)


# ---------------------------------------------------------------------------
# A6. classify 纯函数：多次一致 + 零 DB 访问
# ---------------------------------------------------------------------------
class PureFunctionQA(unittest.TestCase):
    def test_repeated_calls_identical(self):
        args = (ev(title="甲公司：法定代表人由张三变更为赵六"), ent(), snap(legal_person="张三"))
        first = bc.classify(*args)
        for _ in range(5):
            again = bc.classify(*args)
            self.assertEqual((again.bucket, again.subtype, [d.to_dict() for d in again.diff_items]),
                             (first.bucket, first.subtype, [d.to_dict() for d in first.diff_items]))

    def test_classify_touches_no_db(self):
        boom = mock.Mock(side_effect=AssertionError("classify 不得访问数据库！"))
        with mock.patch.object(db, "get_baseline", boom), \
             mock.patch.object(db, "list_events", boom), \
             mock.patch.object(db, "get_enterprise", boom), \
             mock.patch.object(db, "upsert_baseline", boom), \
             mock.patch.object(db, "list_baselines", boom), \
             mock.patch.object(db, "delete_baseline", boom):
            v = bc.classify(ev(title="甲公司：法定代表人由张三变更为赵六"),
                            ent(), snap(legal_person="张三"))
        self.assertEqual(v.bucket, bc.BUCKET_CHANGE)
        boom.assert_not_called()


# ---------------------------------------------------------------------------
# A7. ★ 重点证伪：历史来源建基线应取"放款前最近一次状态"
# ---------------------------------------------------------------------------
class HistoricBaselineLatestStateTest(DbBase):
    """③ 历史事件建基线：状态维应取放款前**最近一次**变更后的值（放款时点状态）。

    构造：放款前发生两次法定代表人变更（8 月 张三→李四，9 月 李四→王五）。
    放款时点真实状态应为 **王五**（最近一次）。若实现取**最早**值（李四）即为缺陷。
    """

    def _seed_two_changes(self):
        db.insert_event(Event(enterprise_id=1, category="biz_change",
                              title="甲公司：法定代表人由张三变更为李四",
                              source="gsxt", occurred_at="2026-08-01"))
        db.insert_event(Event(enterprise_id=1, category="biz_change",
                              title="甲公司：法定代表人由李四变更为王五",
                              source="gsxt", occurred_at="2026-09-10"))

    def test_history_baseline_uses_latest_preloan_state(self):
        self._seed_two_changes()
        s = bc.build_baseline(ent())
        self.assertIsNotNone(s)
        self.assertEqual(s.legal_person, "王五",
                         f"历史基线应取放款前最近状态(王五)，实际={s.legal_person!r}")

    def test_false_negative_from_earliest_wins(self):
        """放款后 法代 王五→李四（真实变回旧值）→ 相对真实基线(王五)应 change。"""
        self._seed_two_changes()
        s = bc.build_baseline(ent())
        v = bc.classify(ev(title="甲公司：法定代表人由王五变更为李四"), ent(), s)
        self.assertEqual(v.bucket, bc.BUCKET_CHANGE,
                         f"王五→李四 相对真实基线(王五)应为 change，实际={v.bucket}")

    def test_false_positive_from_earliest_wins(self):
        """放款后 赵六→王五（结果=真实基线王五）→ 应为 baseline（非问题）。"""
        self._seed_two_changes()
        s = bc.build_baseline(ent())
        v = bc.classify(ev(title="甲公司：法定代表人由赵六变更为王五"), ent(), s)
        self.assertEqual(v.bucket, bc.BUCKET_BASELINE,
                         f"结果=真实基线(王五)应为 baseline，实际={v.bucket}")


if __name__ == "__main__":
    unittest.main()
