"""QA 独立验收 —— B. R2 数据源扩充（重点验"不崩不卡"）。

- 对 credit_china / gsxt 注入 超时 / HTTP 500 / 反爬 HTML / 非 JSON 四种故障，
  断言 ``run_with_fallback`` **不抛未捕获异常** 且 **不阻塞**（耗时上限断言）；
- 断言 ``config.yaml`` 付费源默认关闭、jisuapi 未被当免费工商源启用；
- ``tools/connector_ingest.py``：临时副本库跑两遍幂等；**任何 occurred_at 不得等于 generated_at**；
  未授权(401/403)不抛异常。
"""
import gc
import importlib.util
import json
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

import yaml

from src.core import db, settings
from src.core.models import Enterprise, Event

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURE = os.path.join(ROOT, "tests", "fixtures", "connector_sample.json")

# 预热真实配置到缓存（只读）
with open(os.path.join(ROOT, "config.yaml"), "r", encoding="utf-8") as _f:
    settings._config = yaml.safe_load(_f) or {}


class FakeResp:
    def __init__(self, text="", status_code=200, json_data=None):
        self.text = text
        self.status_code = status_code
        self._json = json_data

    def json(self):
        if self._json is None:
            raise ValueError("no json")
        return self._json


class FaultInjectionQA(unittest.TestCase):
    """逐源四种故障：run_with_fallback 既不抛异常也不阻塞。"""

    TIME_BUDGET = 5.0

    def _run(self, provider, category):
        from src.providers import base as pbase
        with mock.patch.object(pbase, "load_providers", return_value=[provider]):
            t0 = time.monotonic()
            events, err = pbase.run_with_fallback(category, Enterprise(id=1, name="测试公司"))
            dt = time.monotonic() - t0
        return events, err, dt

    def test_credit_china_four_faults(self):
        import requests
        from src.providers import credit_china as m

        faults = {
            "timeout": mock.Mock(side_effect=requests.Timeout("timed out")),
            "http500": mock.Mock(return_value=FakeResp("server error", status_code=500)),
            "anticrawl_html": mock.Mock(return_value=FakeResp(
                "<html><body>请开启JavaScript验证，安全验证</body></html>")),
            "non_json": mock.Mock(return_value=FakeResp(
                "this-is-plain-text-not-json-payload-" + "x" * 80)),
        }
        for label, fake in faults.items():
            with self.subTest(fault=label):
                p = m.CreditChinaProvider({})
                with mock.patch.object(p, "http_get", fake):
                    events, err, dt = self._run(p, "enforcement")
                self.assertEqual(events, [], f"{label}: 不得返回事件")
                self.assertIn("credit_china", err, f"{label}: 错误串应含源名")
                self.assertLess(dt, self.TIME_BUDGET, f"{label}: 耗时 {dt:.2f}s 超上限")

    def test_gsxt_three_faults(self):
        import requests
        from src.providers import biz_gsxt as m

        faults = {
            "timeout": mock.Mock(side_effect=requests.Timeout("timed out")),
            "http500_home": mock.Mock(return_value=FakeResp("err", status_code=500)),
            "anticrawl_short": mock.Mock(return_value=FakeResp("<html>验证</html>")),
        }
        for label, fake in faults.items():
            with self.subTest(fault=label):
                p = m.GsxtProvider({})
                with mock.patch.object(p, "http_get", fake):
                    events, err, dt = self._run(p, "biz_change")
                self.assertEqual(events, [], f"{label}: 不得返回事件")
                self.assertIn("gsxt", err, f"{label}: 错误串应含源名")
                self.assertLess(dt, self.TIME_BUDGET, f"{label}: 耗时 {dt:.2f}s 超上限")

    def test_connected_refresh_cycle_does_not_raise(self):
        """模拟一次真实刷新链：源全挂 → run_sync 不抛异常，仅 0 入库。"""
        from src.core import sync as sync_mod
        import requests

        def boom(cat, ent):
            raise requests.Timeout("net down")

        ent = Enterprise(id=1, name="测试公司")
        with mock.patch.object(sync_mod, "run_with_fallback", side_effect=boom):
            # 注意：sync 内部对每个源单独兜底；此处验证整体不被异常打断
            try:
                sync_mod.run_sync(ent)
                raised = False
            except Exception:
                raised = True
        # run_sync 若未自行兜底，则视为缺陷（刷新会被打断）
        self.assertFalse(raised, "全部数据源超时不应打断 run_sync 刷新链")


class ConfigPolicyQA(unittest.TestCase):
    def _specs(self):
        cfg = settings.config()
        out = []
        for cat, specs in cfg["sources"].items():
            for s in specs or []:
                out.append((cat, s))
        return out

    def test_paid_sources_disabled_by_default(self):
        for cat, s in self._specs():
            if s.get("paid"):
                with self.subTest(name=s["name"]):
                    self.assertFalse(s.get("enabled"),
                                     f"付费源 {s['name']} 不得默认启用（{cat}）")

    def test_requires_key_sources_disabled_strict(self):
        """团队验收口径：requires_key:true 的源默认必须 enabled:false。"""
        violators = [s["name"] for _cat, s in self._specs()
                     if s.get("requires_key") and s.get("enabled")]
        self.assertEqual(violators, [],
                         f"以下 requires_key 源被默认启用：{violators}")

    def test_jisuapi_not_enabled_as_free_biz_source(self):
        names = {s["name"]: s for _c, s in self._specs()}
        self.assertIn("jisuapi", names)
        self.assertFalse(names["jisuapi"]["enabled"], "jisuapi 不得作为免费工商源启用")
        self.assertTrue(names["jisuapi"].get("paid"), "jisuapi 应标为付费")

    def test_p0_free_sources_enabled(self):
        names = {s["name"]: s for _c, s in self._specs()}
        for n in ("gsxt", "credit_china", "wenshu", "zxgk", "aiqicha"):
            with self.subTest(name=n):
                self.assertTrue(names[n]["enabled"], f"P0 免费源 {n} 应启用")


# ---------------------------------------------------------------------------
# tools/connector_ingest.py
# ---------------------------------------------------------------------------
def _load_ingest():
    path = os.path.join(ROOT, "tools", "connector_ingest.py")
    spec = importlib.util.spec_from_file_location("qa_connector_ingest", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["qa_connector_ingest"] = mod
    spec.loader.exec_module(mod)
    return mod


ci = _load_ingest()
GEN_DAY = "2026-10-16"  # connector_sample.json 的 generated_at 归一


class ConnectorIngestQA(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        patchers = [
            mock.patch.object(db, "db_path", return_value=os.path.join(self._tmp.name, "qa.db")),
            mock.patch.object(__import__("src.core.paths", fromlist=["x"]), "data_dir",
                              return_value=self._tmp.name),
        ]
        for p in patchers:
            p.start()
        self._p = patchers
        db.init_db()
        db.upsert_enterprise("样例测试公司", credit_code="91211500SAMPLE001")
        self._eid = db.list_enterprises()[0].id

    def tearDown(self):
        for p in self._p:
            p.stop()
        gc.collect()
        self._tmp.cleanup()

    def test_idempotent_second_run_zero(self):
        r1 = ci.run(FIXTURE)
        r2 = ci.run(FIXTURE)
        self.assertEqual(r1["inserted"], 5)
        self.assertEqual(r2["inserted"], 0, "第二遍必须全部去重 inserted=0")

    def test_no_occurred_at_equals_generated_at(self):
        ci.run(FIXTURE)
        evs = db.list_events(enterprise_id=self._eid)
        self.assertGreater(len(evs), 0)
        bad = [e.title for e in evs if e.occurred_at == GEN_DAY]
        self.assertEqual(bad, [], f"存在 occurred_at==generated_at 的事件：{bad}")

    def test_empty_occurred_at_for_missing_change_date(self):
        ci.run(FIXTURE)
        evs = {e.title: e for e in db.list_events(enterprise_id=self._eid)}
        e = evs["样例测试公司：注册资本由500万变更为1000万"]  # 无 change_date
        self.assertEqual(e.occurred_at, "")
        self.assertIn("变更日期缺失", e.summary)

    def test_unauthorized_no_exception(self):
        path = os.path.join(self._tmp.name, "unauth.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"schema_version": "1.0", "connector": "qcc_company",
                       "error": "403 Forbidden unauthorized"}, f, ensure_ascii=False)
        res = ci.run(path)   # 不得抛异常
        self.assertEqual(res["inserted"], 0)
        self.assertTrue(any(r["reason"] == "unauthorized" for r in res["reasons"]))
        # 401 亦可
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"schema_version": "1.0", "connector": "shuidi_credit",
                       "status": "401"}, f, ensure_ascii=False)
        self.assertEqual(ci.run(path)["inserted"], 0)

    def test_unknown_connector_does_not_block(self):
        path = os.path.join(self._tmp.name, "u.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"schema_version": "1.0", "connector": "foo_mcp",
                       "companies": [{"name": "样例测试公司",
                                      "credit_code": "91211500SAMPLE001",
                                      "changes": [{"change_item": "法定代表人",
                                                   "before": "a", "after": "b",
                                                   "change_date": "2026-10-05"}]}]},
                      f, ensure_ascii=False)
        res = ci.run(path)
        self.assertEqual(res["source"], "connector_unknown")
        self.assertEqual(res["inserted"], 1)


if __name__ == "__main__":
    unittest.main()
