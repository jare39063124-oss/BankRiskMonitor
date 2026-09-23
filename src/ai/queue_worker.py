"""AI 任务队列后台线程：AI 降级期间任务入队积累，恢复后自动补跑。

纯 threading 实现（与 GUI 框架解耦），start()/stop() 供 GUI 启停；
内部用哨兵 None 通知工作线程退出。降级标志未触发时才消费队列，
降级期间任务在 ai_queue 表中积累，健康检查恢复后自动继续补跑。
"""
import logging
import queue
import threading
import time

from ..core import db
from . import llm_client, rule_engine

log = logging.getLogger(__name__)

DEGRADED_PREFIX = rule_engine.DEGRADED_PREFIX


def _ent_name(event) -> str:
    try:
        ent = db.get_enterprise(event.enterprise_id) if getattr(event, "enterprise_id", 0) else None
        return ent.name if ent else ""
    except Exception:
        return ""


def rule_assess(event) -> dict:
    """规则研判（纯计算，不落库）：engine=rule、pending_review=1、verdict 加降级前缀。"""
    res = rule_engine.assess_by_rule(event.title, event.summary, event.source,
                                     event.category, _ent_name(event))
    res["verdict"] = DEGRADED_PREFIX + res["verdict"]
    res["engine"] = "rule"
    res["pending_review"] = 1
    return res


def _persist_assess(event, res: dict):
    if getattr(event, "id", None):
        db.update_event_verdict(event.id, res["sentiment"], res["severity"], res["relevance"],
                                res["verdict"], res["engine"], res["pending_review"])


def degraded_assess(event) -> dict:
    """降级研判（assess_or_enqueue 的降级分支，同款逻辑）：规则出结果并落库、入队待 AI 恢复补跑。"""
    res = rule_assess(event)
    if getattr(event, "id", None):
        _persist_assess(event, res)
        db.enqueue("assess", {"event_id": event.id})
    return res


def assess_or_enqueue(event) -> dict:
    """AI 可用→立即 AI 研判并落库；不可用→规则结果落库并入队。返回研判结果 dict。"""
    from . import agents  # 延迟导入，避免循环依赖
    if llm_client.is_available():
        try:
            res = agents.assess_event(event)
            _persist_assess(event, res)
            return res
        except llm_client.AIUnavailableError:
            pass  # 请求过程中恰好降级，转规则分支
    return degraded_assess(event)


def _find_event(event_id: int):
    for ev in db.list_events(limit=2000):
        if ev.id == event_id:
            return ev
    return None


class QueueWorker:
    """后台队列线程：哨兵退出 + 定期健康检查，恢复后自动补跑积累任务。"""

    _SENTINEL = None

    def __init__(self, poll_interval: float = 1.0, health_interval: float = 60.0):
        self.poll_interval = poll_interval
        self.health_interval = health_interval
        self._wq = queue.Queue()
        self._thread: threading.Thread = None
        self._last_health = 0.0

    # ---- 生命周期（供 GUI 调用）----
    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._last_health = 0.0
        self._thread = threading.Thread(target=self._run, name="ai-queue-worker", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0):
        self._wq.put(self._SENTINEL)
        t = self._thread
        if t and t.is_alive():
            t.join(timeout=timeout)

    # ---- 主循环 ----
    def _run(self):
        while True:
            try:
                item = self._wq.get(timeout=self.poll_interval)
            except queue.Empty:
                item = "tick"
            if item is self._SENTINEL:
                break
            try:
                self._tick()
            except Exception as e:
                log.warning("队列循环异常: %s", e)

    def _tick(self):
        # 定期健康检查：降级期间也要探测，成功一次即复位降级标志，随后继续补跑队列
        now = time.time()
        if now - self._last_health >= self.health_interval:
            self._last_health = now
            try:
                llm_client.health_check()
            except Exception as e:
                log.warning("AI 健康检查异常: %s", e)
        if llm_client.is_degraded():
            return  # 降级期间不消费队列，任务继续积累
        try:
            tasks = db.pop_pending_tasks()
        except Exception as e:
            log.warning("读取任务队列失败: %s", e)
            return
        for task_id, task_type, payload in tasks:
            try:
                self._handle(task_type, payload or {})
                db.finish_task(task_id, True)
            except Exception as e:
                log.warning("任务 %s(%s) 处理失败: %s", task_id, task_type, e)
                try:
                    db.finish_task(task_id, False)
                except Exception:
                    pass

    # ---- 任务分发 ----
    def _handle(self, task_type: str, payload: dict):
        if task_type == "assess":
            self._handle_assess(payload)
        elif task_type == "credit_impact":
            self._handle_credit_impact(payload)
        else:
            raise ValueError(f"未知任务类型: {task_type}")

    def _handle_assess(self, payload: dict):
        from . import agents
        event_id = payload.get("event_id")
        ev = _find_event(int(event_id)) if event_id is not None else None
        if not ev:
            raise ValueError(f"事件不存在，payload={payload}")
        res = agents.assess_event(ev)
        _persist_assess(ev, res)

    def _handle_credit_impact(self, payload: dict):
        from . import agents
        ent_id = payload.get("ent_id") or payload.get("enterprise_id")
        if not ent_id:
            raise ValueError(f"缺少 ent_id，payload={payload}")
        ent_id = int(ent_id)
        material_text = payload.get("material_text") or ""
        if not material_text:
            mats = db.list_materials(ent_id)
            material_text = "\n".join(m.text for m in mats if m.text)
        events = db.list_events(enterprise_id=ent_id, limit=200)
        res = agents.analyze_credit_impact(material_text, events)
        db.set_setting(f"credit_impact_{ent_id}", res["text"])


# 模块级默认实例：GUI 可直接 worker.start() / worker.stop()
worker = QueueWorker()
