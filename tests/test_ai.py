"""AI 层单元测试：全部 mock 网络调用，不真实访问。

运行：python -m unittest tests.test_ai -v
"""
import json
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core import db as db_mod
from src.core.models import Event

# 测试用临时数据库（db.connect 每次调用 db_path()，替换模块属性即生效，不污染真实数据）
_TMP_DIR = tempfile.mkdtemp(prefix="brm_ai_test_")
db_mod.db_path = lambda: os.path.join(_TMP_DIR, "test.db")
db_mod.init_db()

from src.ai import agents, llm_client, queue_worker, rule_engine  # noqa: E402

# 测试用模型策略：无重试退避等待、失败阈值 3
POLICY = {
    "banned_models": ["openrouter/auto", "openrouter/*"],
    "fallback_models": [{"id": "m1", "cost": 0}, {"id": "m2", "cost": 0}],
    "request": {"timeout_seconds": 1, "max_retries": 0,
                "backoff_base_seconds": 0.01, "failure_threshold": 3},
}


def _patch_policy():
    return mock.patch.object(llm_client.settings, "model_policy", return_value=POLICY)


def _mk_event(**kw) -> Event:
    ev = Event(enterprise_id=0, category="news", title="测试事件标题", summary="测试摘要",
               source="baidu_news")
    for k, v in kw.items():
        setattr(ev, k, v)
    return ev


def _insert_event(**kw) -> Event:
    """入库并返回带 id 的事件（供队列/落库断言使用）。"""
    db_mod.insert_event(_mk_event(**kw))
    return db_mod.list_events(limit=1)[0]


class AIBase(unittest.TestCase):
    """公共夹具：复位降级状态、固定 API Key（不依赖 .env 是否存在）。"""

    def setUp(self):
        llm_client._state.record_success()
        p = mock.patch.object(llm_client, "get_api_key", return_value="test-key")
        p.start()
        self.addCleanup(p.stop)


class TestRuleEngine(unittest.TestCase):
    """词库打分与来源权重断言。"""

    def test_high_word_enforcement(self):
        r = rule_engine.assess_by_rule("某公司资产被法院查封", "", "zxgk", "enforcement")
        self.assertEqual(r["severity"], "high")
        self.assertEqual(r["sentiment"], "negative")
        self.assertEqual(r["relevance"], "high")
        self.assertIn("查封", r["verdict"])
        self.assertIn("较高", r["verdict"])  # zxgk 官方源可信度高
        self.assertIn("执行标的金额", r["verdict"])

    def test_medium_lawsuit(self):
        r = rule_engine.assess_by_rule("公司与供应商产生合同纠纷", "", "baidu_news", "lawsuit")
        self.assertEqual(r["severity"], "medium")
        self.assertEqual(r["sentiment"], "negative")
        self.assertEqual(r["relevance"], "medium")

    def test_positive_news(self):
        r = rule_engine.assess_by_rule("某公司中标重点项目", "", "baidu_news", "news", ent_name="某公司")
        self.assertEqual(r["sentiment"], "positive")
        self.assertEqual(r["severity"], "low")
        self.assertEqual(r["relevance"], "high")  # 标题含企业全名

    def test_news_relevance_by_name(self):
        r = rule_engine.assess_by_rule("行业整体向好", "", "baidu_news", "news", ent_name="某公司")
        self.assertEqual(r["relevance"], "low")  # 未含企业全名

    def test_biz_change_equity(self):
        r = rule_engine.assess_by_rule("公司完成股权变更登记", "", "aiqicha", "biz_change")
        self.assertEqual(r["relevance"], "high")  # 股权类默认 high
        self.assertEqual(r["severity"], "medium")

    def test_source_weight(self):
        hi = rule_engine.assess_by_rule("公司被立案调查", "", "wenshu", "lawsuit")
        lo = rule_engine.assess_by_rule("公司被立案调查", "", "istero_pengpai", "news")
        self.assertIn("较高", hi["verdict"])
        self.assertIn("较低", lo["verdict"])


class TestLLMClient(AIBase):
    def test_banned_models_skipped(self):
        pol = dict(POLICY, fallback_models=[{"id": "openrouter/auto"},
                                            {"id": "openrouter/x:free"}, {"id": "m1"}])
        with mock.patch.object(llm_client.settings, "model_policy", return_value=pol):
            c = llm_client.LLMClient()
        self.assertEqual(c.models, ["m1"])  # openrouter/* 通配禁用生效

    def test_chat_success(self):
        with _patch_policy():
            c = llm_client.LLMClient()
        with mock.patch.object(llm_client.LLMClient, "_post_chat", return_value="hello"):
            self.assertEqual(c.chat([{"role": "user", "content": "hi"}]), ("hello", "m1"))

    def test_degrade_and_recover(self):
        with _patch_policy():
            c = llm_client.LLMClient()
        with mock.patch.object(llm_client.LLMClient, "_post_chat",
                               side_effect=RuntimeError("boom")):
            for _ in range(3):  # 连续失败达到阈值 3 → 触发全局降级
                with self.assertRaises(llm_client.AIUnavailableError):
                    c.chat([{"role": "user", "content": "hi"}])
        self.assertTrue(llm_client.is_degraded())
        with mock.patch.object(llm_client.LLMClient, "_post_chat", return_value="pong") as m:
            with self.assertRaises(llm_client.AIUnavailableError):
                c.chat([{"role": "user", "content": "hi"}])  # 降级后直接拒绝，不发请求
            self.assertEqual(m.call_count, 0)
            self.assertTrue(llm_client.health_check())  # 健康检查成功一次即复位
            self.assertFalse(llm_client.is_degraded())
            self.assertEqual(c.chat([{"role": "user", "content": "hi"}]), ("pong", "m1"))


class TestAgents(AIBase):
    AI_JSON = {"sentiment": "negative", "severity": "high", "relevance": "high",
               "verdict": "公司被执行查封，偿债风险显著上升，建议立即核实"}

    def test_assess_normal(self):
        ev = _mk_event(category="enforcement", title="公司被法院查封生产设备", source="zxgk")
        with mock.patch.object(llm_client, "is_available", return_value=True), \
             mock.patch.object(llm_client.LLMClient, "chat",
                               return_value=(json.dumps(self.AI_JSON, ensure_ascii=False), "mock-model")):
            res = agents.assess_event(ev)
        self.assertEqual(res["engine"], "ai")
        self.assertEqual(res["pending_review"], 0)
        self.assertEqual(res["sentiment"], "negative")
        self.assertEqual(res["severity"], "high")
        self.assertLessEqual(len(res["verdict"]), 60)

    def test_assess_json_with_fence(self):
        ev = _mk_event(category="news", title="公司新增诉讼", source="baidu_news")
        raw = "```json\n" + json.dumps(self.AI_JSON, ensure_ascii=False) + "\n```"
        with mock.patch.object(llm_client, "is_available", return_value=True), \
             mock.patch.object(llm_client.LLMClient, "chat", return_value=(raw, "mock-model")):
            res = agents.assess_event(ev)
        self.assertEqual(res["engine"], "ai")
        self.assertEqual(res["severity"], "high")

    def test_assess_json_broken_fallback(self):
        ev = _mk_event(category="enforcement", title="公司被法院查封生产设备", source="zxgk")
        with mock.patch.object(llm_client, "is_available", return_value=True), \
             mock.patch.object(llm_client.LLMClient, "chat",
                               return_value=("抱歉，这条事件我无法判断。", "mock-model")):
            res = agents.assess_event(ev)
        self.assertEqual(res["engine"], "ai")  # 仍是 AI 通道
        self.assertEqual(res["verdict"], "抱歉，这条事件我无法判断。")  # 整体作为 verdict
        self.assertEqual(res["severity"], "high")  # 字段按规则引擎兜底（命中“查封”）
        self.assertEqual(res["sentiment"], "negative")

    def test_assess_degraded_enqueues(self):
        ev = _insert_event(category="enforcement", title="公司新增被执行记录", source="zxgk")
        with mock.patch.object(llm_client, "is_available", return_value=False):
            res = agents.assess_event(ev)
        self.assertEqual(res["engine"], "rule")
        self.assertEqual(res["pending_review"], 1)
        self.assertTrue(res["verdict"].startswith(queue_worker.DEGRADED_PREFIX))
        tasks = db_mod.pop_pending_tasks(200)
        self.assertTrue(any(t == "assess" and p.get("event_id") == ev.id for _, t, p in tasks))
        row = [e for e in db_mod.list_events(limit=100) if e.id == ev.id][0]
        self.assertEqual(row.engine, "rule")  # 已落库
        self.assertEqual(row.pending_review, 1)

    def test_assess_or_enqueue_ai_path(self):
        ev = _insert_event(category="news", title="公司签署战略合作协议", source="baidu_news")
        with mock.patch.object(llm_client, "is_available", return_value=True), \
             mock.patch.object(llm_client.LLMClient, "chat",
                               return_value=(json.dumps(self.AI_JSON, ensure_ascii=False), "mock-model")):
            res = queue_worker.assess_or_enqueue(ev)
        self.assertEqual(res["engine"], "ai")
        row = [e for e in db_mod.list_events(limit=100) if e.id == ev.id][0]
        self.assertEqual(row.engine, "ai")
        self.assertEqual(row.pending_review, 0)

    def test_credit_impact_degraded(self):
        with mock.patch.object(llm_client, "is_available", return_value=False):
            res = agents.analyze_credit_impact("授信材料摘要", [_mk_event(title="公司被处罚")])
        self.assertEqual(res["engine"], "rule")
        self.assertEqual(res["pending_review"], 1)
        self.assertTrue(res["text"].startswith(queue_worker.DEGRADED_PREFIX))

    def test_credit_impact_ai_truncates_material(self):
        with mock.patch.object(llm_client, "is_available", return_value=True), \
             mock.patch.object(llm_client.LLMClient, "chat",
                               return_value=("一、分类迁徙风险：……", "m")) as m:
            res = agents.analyze_credit_impact("x" * 7000, [])
        self.assertEqual(res["engine"], "ai")
        self.assertEqual(res["pending_review"], 0)
        content = m.call_args[0][0][0]["content"]  # messages[0]["content"]
        self.assertIn("x" * 6000, content)   # 材料截断到 6000 字
        self.assertNotIn("x" * 6001, content)

    def test_report_fallback_and_ai(self):
        with mock.patch.object(llm_client, "is_available", return_value=False):
            res = agents.generate_report_text(
                "2026-09 全部监控企业", [{"name": "甲公司"}],
                [_mk_event(title="公司被冻结账户", severity="high")])
        self.assertEqual(res["engine"], "rule")
        self.assertEqual(res["pending_review"], 1)
        self.assertTrue(res["text"].startswith(queue_worker.DEGRADED_PREFIX))
        self.assertIn("甲公司", res["text"])
        with mock.patch.object(llm_client, "is_available", return_value=True), \
             mock.patch.object(llm_client.LLMClient, "chat",
                               return_value=("综述……风险提示……", "m")):
            res = agents.generate_report_text("2026-09", [], [])
        self.assertEqual(res["engine"], "ai")
        self.assertEqual(res["pending_review"], 0)

    def test_penetration_fallback(self):
        related = [{"name": "母公司A", "relation": "股东(60%)", "pen_level": 1, "direction": "up"},
                   {"name": "子公司B", "relation": "对外投资(100%)", "pen_level": 1, "direction": "down"}]
        with mock.patch.object(llm_client, "is_available", return_value=False):
            res = agents.analyze_penetration("目标企业", related)
        self.assertEqual(res["engine"], "rule")
        self.assertEqual(res["pending_review"], 1)
        self.assertTrue(res["text"].startswith(queue_worker.DEGRADED_PREFIX))
        self.assertIn("母公司A", res["text"])
        self.assertIn("互保", res["text"])

    def test_triage_rule_filter(self):
        evs = [_mk_event(title="a", severity="high", relevance="low"),
               _mk_event(title="b", severity="medium", relevance="high"),
               _mk_event(title="c", severity="medium", relevance="low"),
               _mk_event(title="d", severity="low", relevance="high")]
        with mock.patch.object(llm_client, "is_available", return_value=False):
            out = agents.triage_events(evs)
        self.assertEqual([e.title for e in out], ["a", "b"])

    def test_triage_ai_denoise(self):
        evs = [_mk_event(title="a", severity="high", relevance="low"),
               _mk_event(title="b", severity="high", relevance="low")]
        side = [('{ "relevant": false }', "m"), ('{"relevant": true}', "m")]
        with mock.patch.object(llm_client, "is_available", return_value=True), \
             mock.patch.object(llm_client.LLMClient, "chat", side_effect=side):
            out = agents.triage_events(evs)
        self.assertEqual([e.title for e in out], ["b"])  # 相关性降噪生效


class TestQueueWorker(AIBase):
    def test_handle_assess_task(self):
        ev = _insert_event(category="enforcement", title="公司被强制执行", source="zxgk")
        db_mod.enqueue("assess", {"event_id": ev.id})
        worker = queue_worker.QueueWorker()
        with mock.patch.object(llm_client, "is_available", return_value=True), \
             mock.patch.object(llm_client.LLMClient, "chat",
                               return_value=(json.dumps(TestAgents.AI_JSON, ensure_ascii=False), "m")):
            worker._handle("assess", {"event_id": ev.id})
        row = [e for e in db_mod.list_events(limit=100) if e.id == ev.id][0]
        self.assertEqual(row.engine, "ai")
        self.assertEqual(row.ai_verdict, TestAgents.AI_JSON["verdict"])

    def test_handle_credit_impact_task(self):
        ent_id = db_mod.upsert_enterprise("测试企业")
        db_mod.insert_event(_mk_event(enterprise_id=ent_id, title="公司被处罚"))
        worker = queue_worker.QueueWorker()
        with mock.patch.object(llm_client, "is_available", return_value=False):
            worker._handle("credit_impact", {"ent_id": ent_id})
        text = db_mod.get_setting(f"credit_impact_{ent_id}")
        self.assertTrue(text)
        self.assertTrue(text.startswith(queue_worker.DEGRADED_PREFIX))

    def test_worker_start_stop(self):
        """启停冒烟：mock 掉健康检查并强制降态，保证循环空转且不触网。"""
        worker = queue_worker.QueueWorker(poll_interval=0.05, health_interval=60)
        with mock.patch.object(llm_client, "is_degraded", return_value=True), \
             mock.patch.object(llm_client, "health_check", return_value=True):
            worker.start()
            time.sleep(0.15)
            worker.stop(timeout=2)
        self.assertFalse(worker._thread.is_alive())


if __name__ == "__main__":
    unittest.main()
