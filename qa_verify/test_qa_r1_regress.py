"""QA 独立回归验收 —— R1 缺陷修复（commit cea848d）证伪式复核。

本文件由 QA（严过关）**独立自造数据 + 自定断言**，不复用工程师既有用例，专门针对
本轮 3 处源码改动做"证伪"：

* FIX-1 / BUG-1：``_apply_history`` / ``_apply_auto_source`` 改为按日期**降序**取
  "放款前最近一次"变更值 —— 验证①取最近非最早；②降序改动**未打穿跨来源优先级**。
* FIX-3：``classify`` 将 ``d < loan -> baseline`` 前移到无基线判断之前 —— 核查
  "放款前事件 + 无基线"语义变化是否让既有断言退化为恒真/恒假（断言须仍有区分度）。
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
BEFORE = "2026-09-20"


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
        self._db_file = os.path.join(self._tmp.name, "qa_regress.db")
        p = mock.patch.object(db, "db_path", return_value=self._db_file)
        p.start()
        self._p = p
        db.init_db()

    def tearDown(self):
        self._p.stop()
        gc.collect()
        self._tmp.cleanup()


def _seed_legal(rows):
    """rows: [(date, before, after)] → 逐条写入历史工商变更事件。"""
    for date, before, after in rows:
        db.insert_event(Event(enterprise_id=1, category="biz_change",
                              title=f"甲公司：法定代表人由{before}变更为{after}",
                              source="gsxt", occurred_at=date))


# ---------------------------------------------------------------------------
# FIX-1(a)：放款前同维 3 次变更 + 放款后 1 次变更 → 基线取"放款前最近一次"
# ---------------------------------------------------------------------------
class HistoryThreeChangesLatestTest(DbBase):
    """放款前三次法代变更（8/01 张三→李四，9/01 李四→王五，9/15 王五→赵六）。

    真实放款时点状态 = **赵六**（最近一次）。断言基线**恰为**赵六，且**排除**李四/王五
    （即实现确实取了"最近"而非"最早"或"中间"）。
    """

    def _seed(self):
        _seed_legal([("2026-08-01", "张三", "李四"),
                     ("2026-09-01", "李四", "王五"),
                     ("2026-09-15", "王五", "赵六")])

    def test_baseline_is_exactly_latest_preloan(self):
        self._seed()
        s = bc.build_baseline(ent())
        self.assertIsNotNone(s)
        self.assertEqual(s.legal_person, "赵六",
                         f"基线应取放款前最近状态(赵六)，实际={s.legal_person!r}")
        # 明确排除"最早/中间"两种错误取值
        self.assertNotIn(s.legal_person, ("李四", "王五"))

    def test_postloan_change_is_change(self):
        # 放款后 赵六 → 孙七：相对真实基线(赵六)必为 change
        self._seed()
        s = bc.build_baseline(ent())
        v = bc.classify(ev(title="甲公司：法定代表人由赵六变更为孙七"), ent(), s)
        self.assertEqual(v.bucket, bc.BUCKET_CHANGE,
                         f"孙七 vs 基线赵六 应 change，实际={v.bucket}/{v.subtype}")
        self.assertEqual(v.diff_items[0].old, "赵六")
        self.assertEqual(v.diff_items[0].new, "孙七")

    def test_postloan_result_equals_baseline_is_baseline(self):
        # 放款后 钱八 → 赵六（结果恰=真实基线赵六）→ baseline（非问题）
        self._seed()
        s = bc.build_baseline(ent())
        v = bc.classify(ev(title="甲公司：法定代表人由钱八变更为赵六"), ent(), s)
        self.assertEqual(v.bucket, bc.BUCKET_BASELINE,
                         f"结果=基线(赵六) 应为 baseline，实际={v.bucket}/{v.subtype}")

    def test_postloan_regress_to_intermediate_is_change(self):
        """放款后变回"中间旧值"王五 → 相对真实基线(赵六)仍是 change（防漏报）。"""
        self._seed()
        s = bc.build_baseline(ent())
        v = bc.classify(ev(title="甲公司：法定代表人由赵六变更为王五"), ent(), s)
        self.assertEqual(v.bucket, bc.BUCKET_CHANGE)

    def test_baseline_source_is_history(self):
        self._seed()
        s = bc.build_baseline(ent())
        self.assertEqual(s.source, "history")


# ---------------------------------------------------------------------------
# FIX-1(b)：_apply_auto_source 的 changes 同样取"放款前最近一次"
# ---------------------------------------------------------------------------
class AutoSourceThreeChangesLatestTest(DbBase):
    def test_changes_take_latest_preloan_only(self):
        sources = [{
            "source": "tyc_mcp",
            "changes": [
                {"change_item": "法定代表人", "after": "李四", "change_date": "2026-08-01"},
                {"change_item": "法定代表人", "after": "王五", "change_date": "2026-09-01"},
                {"change_item": "法定代表人", "after": "赵六", "change_date": "2026-09-15"},
                {"change_item": "法定代表人", "after": "孙七", "change_date": "2026-10-05"},  # 放款后→忽略
            ],
        }]
        s = bc.build_baseline(ent(), sources)
        self.assertIsNotNone(s)
        self.assertEqual(s.legal_person, "赵六",
                         f"自动源 changes 应取放款前最近(赵六)，实际={s.legal_person!r}")


# ---------------------------------------------------------------------------
# FIX-1(c) 关键：降序改动**不得打穿跨来源优先级**（①自动源 profile 仍优先）
# ---------------------------------------------------------------------------
class CrossSourcePriorityNotRegressedTest(DbBase):
    def test_auto_profile_outranks_history_same_dim(self):
        """① 自动源 profile 与 ③ 历史源同维冲突 → ① 仍优先。"""
        _seed_legal([("2026-08-01", "张三", "李四"),
                     ("2026-09-15", "李四", "王五")])   # 历史最新=王五
        sources = [{"source": "tyc_mcp", "profile": {"legal_person": "档案法代"}}]
        s = bc.build_baseline(ent(), sources)
        self.assertIsNotNone(s)
        self.assertEqual(s.legal_person, "档案法代",
                         f"①自动源应优先于③历史，实际={s.legal_person!r}")

    def test_auto_source_changes_outrank_history(self):
        """① 自动源带日期变更记录 vs ③ 历史事件同维 → ① 仍优先。"""
        _seed_legal([("2026-09-15", "李四", "王五")])
        sources = [{
            "source": "tyc_mcp",
            "changes": [{"change_item": "法定代表人", "after": "自动源值",
                         "change_date": "2026-09-10"}],
        }]
        s = bc.build_baseline(ent(), sources)
        self.assertEqual(s.legal_person, "自动源值",
                         f"①自动源 changes 应优先于③历史，实际={s.legal_person!r}")

    def test_manual_outranks_history_same_dim(self):
        """② 手动基线 vs ③ 历史同维 → ② 优先。"""
        _seed_legal([("2026-09-15", "李四", "王五")])
        db.upsert_baseline(1, loan_date=LOAN, legal_person="手动值", source="manual")
        s = bc.build_baseline(ent())
        self.assertEqual(s.legal_person, "手动值",
                         f"②手动应优先于③历史，实际={s.legal_person!r}")

    def test_history_still_fills_dim_left_empty_by_auto(self):
        """对照：①未提供的维，③历史仍应补齐（降序改动的副作用不得致漏填）。"""
        _seed_legal([("2026-09-15", "李四", "王五")])
        sources = [{"source": "tyc_mcp", "profile": {"reg_capital": "1000万"}}]
        s = bc.build_baseline(ent(), sources)
        self.assertEqual(s.reg_capital, "1000万")
        self.assertEqual(s.legal_person, "王五",
                         f"①未覆盖的法代维应由③历史补为王五，实际={s.legal_person!r}")

    def test_mixed_source_label_kept(self):
        _seed_legal([("2026-09-15", "李四", "王五")])
        sources = [{"source": "tyc_mcp", "profile": {"reg_capital": "1000万"}}]
        s = bc.build_baseline(ent(), sources)
        self.assertEqual(s.source, "mixed")


# ---------------------------------------------------------------------------
# FIX-3：判定顺序核查 —— 断言必须仍有区分度（防止恒真/恒假退化）
# ---------------------------------------------------------------------------
class ClassifyOrderDiscriminationTest(unittest.TestCase):
    """FIX-3 后：放款前事件优先判 baseline；放款后无基线仍 undetermined。

    本组用例刻意同时覆盖"应 baseline"与"应 undetermined"两类输入，确保断言**有区分度**。
    """

    def test_preloan_no_baseline_is_baseline(self):
        v = bc.classify(ev(title="甲公司：法定代表人由张三变更为赵六", occurred_at=BEFORE),
                        ent(), None)
        self.assertEqual(v.bucket, bc.BUCKET_BASELINE)
        self.assertEqual(v.subtype, "baseline")
        self.assertFalse(v.is_problem())

    def test_postloan_no_baseline_is_undetermined(self):
        v = bc.classify(ev(title="甲公司：法定代表人由张三变更为赵六", occurred_at=AFTER),
                        ent(), None)
        self.assertEqual(v.bucket, bc.BUCKET_UNDETERMINED)
        self.assertEqual(v.subtype, "no_baseline")
        self.assertFalse(v.is_problem())

    def test_discrimination_same_input_differs_by_date(self):
        """同一事件内容、同一 None 基线，仅日期不同 → bucket 必然不同（区分度证据）。"""
        a = bc.classify(ev(title="甲公司：法定代表人由张三变更为赵六", occurred_at=BEFORE),
                        ent(), None)
        b = bc.classify(ev(title="甲公司：法定代表人由张三变更为赵六", occurred_at=AFTER),
                        ent(), None)
        self.assertNotEqual(a.bucket, b.bucket,
                            "放款前/后 + 无基线 必须落到不同 bucket，否则断言无区分度")

    def test_same_day_is_not_preloan(self):
        """同日（d == loan）不得被误判为放款前 baseline —— 应为 undetermined(no_baseline)。"""
        v = bc.classify(ev(title="甲公司：法定代表人由张三变更为赵六", occurred_at=LOAN),
                        ent(), None)
        self.assertEqual(v.bucket, bc.BUCKET_UNDETERMINED)
        self.assertEqual(v.subtype, "no_baseline")

    def test_empty_date_still_no_date_not_baseline(self):
        """空/非法日期即便在放款前语境也须 no_date，不得被 d<loan 分支吞掉。"""
        for dirty in ("", None, "abc", "2026-13-45", "2026-02-30"):
            with self.subTest(dirty=repr(dirty)):
                v = bc.classify(ev(title="甲公司：法定代表人由张三变更为赵六",
                                   occurred_at=dirty), ent(), None)
                self.assertEqual(v.subtype, "no_date", f"dirty={dirty!r}")
                self.assertEqual(v.bucket, bc.BUCKET_UNDETERMINED)

    def test_no_loan_date_outranks_everything(self):
        """无放款日优先级最高：即便有基线/放款前日期也须 no_loan。"""
        v = bc.classify(ev(title="甲公司：法定代表人由张三变更为赵六", occurred_at=BEFORE),
                        ent(loan_date=""), snap(legal_person="张三"))
        self.assertEqual(v.subtype, "no_loan")


if __name__ == "__main__":
    unittest.main()
