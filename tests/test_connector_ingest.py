"""T03 单元测试：连接器代查入库（``tools/connector_ingest.py``）。

覆盖（ARCH-v2-increment §1.6.3 / T03 四项硬验收）：
1. 样例 JSON **幂等可重复执行**（第二次 ``inserted=0``）；
2. ``occurred_at`` 映射硬约束：有变更日 → 真实日期；无变更日 → 空串；
   **等于抓取日（generated_at）亦留空**；
3. 未授权连接器不抛异常、优雅跳过（退出码 0）；
4. title 稳定去重；``source`` 命名限定；未知连接器 → ``connector_unknown`` 不阻断。

全部离线；SQLite 重定向到临时文件，``data_dir`` 指向临时目录，不污染项目库。

运行：python -m pytest tests/test_connector_ingest.py -q
"""
import gc
import importlib.util
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (ROOT, os.path.join(ROOT, "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

from src.core import db  # noqa: E402
from src.core import paths  # noqa: E402

FIXTURE = os.path.join(ROOT, "tests", "fixtures", "connector_sample.json")


def _load_ingest():
    """按文件路径加载（tools 非包，避免命名空间包歧义）。"""
    path = os.path.join(ROOT, "tools", "connector_ingest.py")
    spec = importlib.util.spec_from_file_location("connector_ingest", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["connector_ingest"] = mod  # dataclass 解析需要模块已注册
    spec.loader.exec_module(mod)
    return mod


ci = _load_ingest()


class IngestBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self._db_file = os.path.join(self._tmp.name, "test.db")
        patchers = [
            mock.patch.object(db, "db_path", return_value=self._db_file),
            mock.patch.object(paths, "data_dir", return_value=self._tmp.name),
        ]
        for p in patchers:
            p.start()
        self._patchers = patchers
        db.init_db()
        db.upsert_enterprise("样例测试公司", credit_code="91211500SAMPLE001")
        self._eid = db.list_enterprises()[0].id

    def tearDown(self):
        for p in self._patchers:
            p.stop()
        gc.collect()
        self._tmp.cleanup()

    def _write_json(self, data) -> str:
        path = os.path.join(self._tmp.name, "batch.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        return path


class OccurredAtUnitTest(unittest.TestCase):
    """_norm_date / _occurred_at 纯函数（硬约束：绝不回退抓取日）。"""

    def test_norm_date(self):
        self.assertEqual(ci._norm_date("2026-10-05"), "2026-10-05")
        self.assertEqual(ci._norm_date("2026-10-05 12:00:00"), "2026-10-05")
        self.assertEqual(ci._norm_date(""), "")
        self.assertEqual(ci._norm_date(None), "")
        self.assertEqual(ci._norm_date("2026/10/5"), "")

    def test_occurred_at_with_date(self):
        occ, note = ci._occurred_at("2026-10-05", generated_at="2026-10-16T14:30:00")
        self.assertEqual(occ, "2026-10-05")
        self.assertEqual(note, "")

    def test_occurred_at_missing(self):
        occ, note = ci._occurred_at("", generated_at="2026-10-16T14:30:00")
        self.assertEqual(occ, "")
        self.assertIn("变更日期缺失", note)

    def test_occurred_at_rejects_generated_at(self):
        # 即便 change_date == 抓取日，也留空（防上游把抓取日当变更日）
        occ, note = ci._occurred_at("2026-10-16", generated_at="2026-10-16T14:30:00")
        self.assertEqual(occ, "")
        self.assertIn("变更日期缺失", note)


class ConnectorIngestTest(IngestBase):
    def test_idempotent(self):
        res1 = ci.run(FIXTURE)
        self.assertEqual(res1["connector"], "tyc_mcp")
        self.assertEqual(res1["source"], "tyc_mcp")
        self.assertEqual(res1["companies"], 1)
        self.assertEqual(res1["inserted"], 5)   # 3 changes + 1 lawsuit + 1 enforcement
        res2 = ci.run(FIXTURE)                  # 第二次：全部去重
        self.assertEqual(res2["inserted"], 0)
        self.assertGreaterEqual(res2["skipped"], 5)

    def test_occurred_at_mapping_in_db(self):
        ci.run(FIXTURE)
        evs = {e.title: e for e in db.list_events(enterprise_id=self._eid)}
        # 有变更日 → 真实日期
        e1 = evs["样例测试公司：法定代表人由张三变更为赵六"]
        self.assertEqual(e1.occurred_at, "2026-10-05")
        # 无变更日 → 空串 + 摘要标注
        e2 = evs["样例测试公司：注册资本由500万变更为1000万"]
        self.assertEqual(e2.occurred_at, "")
        self.assertIn("变更日期缺失", e2.summary)
        # change_date == generated_at → 亦留空
        e3 = evs["样例测试公司：股东由李四 100%变更为李四 60%/王五 40%"]
        self.assertEqual(e3.occurred_at, "")

    def test_title_stable_dedup(self):
        prof = ci.load(FIXTURE)
        comp = prof.companies[0]
        ev1 = ci.build_events(comp, "tyc_mcp", prof.generated_at, self._eid, "样例测试公司")
        ev2 = ci.build_events(comp, "tyc_mcp", prof.generated_at, self._eid, "样例测试公司")
        self.assertEqual([e.title for e in ev1], [e.title for e in ev2])  # title 稳定
        self.assertTrue(all("2026-" not in e.title for e in ev1))         # title 不含日期
        # 稳定 title 保证 insert_event 幂等去重
        self.assertTrue(db.insert_event(ev1[0]))
        self.assertFalse(db.insert_event(ev2[0]))

    def test_unauthorized_graceful(self):
        path = self._write_json({"schema_version": "1.0", "connector": "tyc_mcp",
                                 "error": "unauthorized", "companies": []})
        res = ci.run(path)  # 绝不抛异常
        self.assertEqual(res["inserted"], 0)
        self.assertTrue(any(r["reason"] == "unauthorized" for r in res["reasons"]))
        self.assertEqual(ci.main(["--file", path]), 0)  # 退出码 0

    def test_empty_companies(self):
        path = self._write_json({"schema_version": "1.0", "connector": "qcc_company",
                                 "companies": []})
        res = ci.run(path)
        self.assertEqual(res["inserted"], 0)
        self.assertTrue(any(r["reason"] == "empty" for r in res["reasons"]))

    def test_unknown_connector_not_blocking(self):
        path = self._write_json({
            "schema_version": "1.0", "generated_at": "2026-10-16T14:30:00",
            "connector": "foo_mcp",
            "companies": [{"name": "样例测试公司", "credit_code": "91211500SAMPLE001",
                           "changes": [{"change_item": "法定代表人", "before": "张三",
                                        "after": "赵六", "change_date": "2026-10-05"}]}],
        })
        res = ci.run(path)
        self.assertEqual(res["source"], "connector_unknown")
        self.assertEqual(res["inserted"], 1)  # 未知连接器仍入库，仅 warning 不阻断

    def test_no_match_skipped_no_create(self):
        path = self._write_json({
            "schema_version": "1.0", "connector": "qcc_company",
            "companies": [{"name": "不存在的企业XYZ", "credit_code": "NOPE",
                           "changes": [{"change_item": "法定代表人", "before": "a",
                                        "after": "b", "change_date": "2026-10-05"}]}],
        })
        before = len(db.list_enterprises(include_stopped=True))
        res = ci.run(path)
        self.assertEqual(res["inserted"], 0)
        self.assertEqual(res["skipped"], 1)
        self.assertTrue(any(r["reason"] == "no_enterprise" for r in res["reasons"]))
        # 不得新建企业
        self.assertEqual(len(db.list_enterprises(include_stopped=True)), before)

    def test_main_missing_file(self):
        self.assertEqual(ci.main(["--file", os.path.join(self._tmp.name, "nope.json")]), 2)

    def test_dry_run_no_write(self):
        res = ci.run(FIXTURE, dry_run=True)
        self.assertEqual(res["inserted"], 5)
        self.assertEqual(len(db.list_events(enterprise_id=self._eid)), 0)  # 未落库


if __name__ == "__main__":
    unittest.main()
