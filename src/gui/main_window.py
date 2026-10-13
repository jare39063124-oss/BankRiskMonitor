"""MainWindow：地图居中、企业清单居左、事件流/预警居右、AI 摘要与状态栏居底。

- 手动/定时刷新：QThread 后台调用 core.sync.run_sync（全部监控企业，含关联主体），
  刷新前按设置中心的 category_frequency 过滤本轮参与的信息域（禁用域跳过不查询，
  倍率以“每 N 次全局刷新执行一次”实现）；
- 联动：地图光斑点击 -> 事件流按企业过滤；事件筛选变化 -> 重新加载事件；
- 设置中心：工具栏与状态栏“设置”入口 -> SettingsDialog，保存后应用显示显隐
  （apply_visibility）与新刷新周期（倒计时联动）；
- 状态栏：生效模型/数据源、AI 在线或“自动化脚本降级”、下次刷新倒计时与免责声明。
"""
import json
from datetime import datetime

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QApplication,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSplitter,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

try:
    from ..ai import agents as _agents_mod
except Exception:
    _agents_mod = None
from ..core import db, settings
from ..core.models import CATEGORIES, CATEGORY_LABELS
from . import theme
from .dialogs import (
    EnterpriseDialog,
    ImportDialog,
    MaterialDialog,
    PenetrationDialog,
    SettingsDialog,
    _read_env,
)
from .map_view import MapView
from .panels import AISummaryPanel, AlertPanel, EnterpriseListPanel, EventFeedPanel
from .report_page import ReportDialog
from .workers import FnWorker

try:
    from ..core import sync
except Exception:
    sync = None

SEV_RANK = {"high": 3, "medium": 2, "low": 1}
SEV_LABELS = {"high": "高危", "medium": "中危", "low": "关注"}

DISCLAIMER = "仅采集公开渠道信息用于银行内部贷后管理，不向客户发送任何信息"

# 显示设置区域 key（app_settings.panel_visibility 的 JSON 键，无记录时全部显示）
VISIBILITY_KEYS = ("map", "enterprise_list", "event_feed", "alerts", "ai_summary", "countdown")


def run_refresh_cycle(ents, active_cats, progress_cb=None) -> dict:
    """执行一轮全局刷新（供 RefreshWorker 线程与单测复用），返回 {"ok", "fail"}。

    - active_cats：本轮允许查询的信息域集合；禁用/未到倍率轮次的域跳过不查询。
    - 实现方式：临时以过滤包装器替换 sync.run_with_fallback，被过滤域直接返回
      空结果，sync.run_sync 的入库/去重等其余逻辑保持不变；仅同一时间只允许
      一个刷新线程（MainWindow 已保证），结束时在 finally 中恢复原函数。
    - progress_cb(i, total, name)：进度回调（可在子线程中发射 Qt 信号）。
    """
    if sync is None or not ents:
        return {"ok": 0, "fail": 0}
    active = set(active_cats or ())
    ok = fail = 0
    total = len(ents)
    original = sync.run_with_fallback
    try:
        if active:
            def _filtered(cat, ent):
                if cat not in active:
                    return [], "disabled"
                return original(cat, ent)

            sync.run_with_fallback = _filtered
            for i, ent in enumerate(ents, 1):
                if progress_cb:
                    progress_cb(i, total, ent.name)
                try:
                    sync.run_sync(ent)
                    ok += 1
                except Exception:
                    fail += 1
    finally:
        sync.run_with_fallback = original
    return {"ok": ok, "fail": fail}


class RefreshWorker(FnWorker):
    """串行刷新所有监控企业：run_sync(ent) -> dict（仅查询本轮参与的信息域）。"""

    progress = Signal(int, int, str)

    def __init__(self, ents, categories=None, parent=None):
        super().__init__(self._run_all, parent=parent)
        self._ents = ents
        self._cats = set(categories or CATEGORIES)

    def _run_all(self):
        def cb(i, total, name):
            self.progress.emit(i, total, name)

        return run_refresh_cycle(self._ents, self._cats, cb)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        db.init_db()
        self.setWindowTitle("银行客户风险监控工作台")
        self.resize(1440, 880)

        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(theme.get_app_qss())

        self._worker = None
        self._next_refresh = None  # 下次自动刷新时间戳
        self._refresh_counter = 0  # 全局刷新计数器（信息域倍率：每 N 轮执行一次）
        self._vis = {k: True for k in VISIBILITY_KEYS}
        self._cat_freq = {}

        self._build_ui()
        self._setup_timer()
        self._update_status_bar()
        self._load_ui_settings()
        self.apply_visibility(self._vis)
        self.reload_data()

    # ================= 界面构建 =================
    def _build_ui(self):
        self._build_toolbar()

        # 左：企业清单
        self.ent_panel = EnterpriseListPanel(self)
        self.ent_panel.setMaximumWidth(340)
        self.ent_panel.edit_requested.connect(self._edit_enterprise)
        self.ent_panel.penetrate_requested.connect(self._penetrate)
        self.ent_panel.stop_requested.connect(self._stop_monitor)
        self.ent_panel.enterprise_selected.connect(self._on_ent_selected)

        # 中：地图
        self.map_view = MapView(self)
        self.map_view.spot_clicked.connect(self._on_spot_clicked)

        # 右：事件流 + 预警
        self.feed_panel = EventFeedPanel(self)
        self.feed_panel.event_filters_changed.connect(self._on_filters_changed)
        self.alert_panel = AlertPanel(self)
        self.alert_panel.spot_filter_requested.connect(
            self.feed_panel.set_enterprise_filter)

        right_split = QSplitter(Qt.Orientation.Vertical)
        right_split.addWidget(self.feed_panel)
        right_split.addWidget(self.alert_panel)
        right_split.setSizes([420, 260])

        main_split = QSplitter(Qt.Orientation.Horizontal)
        main_split.addWidget(self.ent_panel)
        main_split.addWidget(self.map_view)
        main_split.addWidget(right_split)
        main_split.setStretchFactor(0, 0)
        main_split.setStretchFactor(1, 1)
        main_split.setStretchFactor(2, 0)
        main_split.setSizes([280, 760, 440])

        # 底：AI 摘要
        self.ai_panel = AISummaryPanel(self)
        self.ai_panel.setMinimumHeight(140)

        central = QWidget(self)
        lay = QVBoxLayout(central)
        lay.setContentsMargins(theme.SPACE, theme.SPACE, theme.SPACE, theme.SPACE)
        lay.setSpacing(theme.SPACE)
        lay.addWidget(main_split, 4)
        lay.addWidget(self.ai_panel, 1)
        self.setCentralWidget(central)

        # 状态栏
        self.model_label = QLabel(self)
        self.source_label = QLabel(self)
        self.ai_label = QLabel(self)
        self.countdown_label = QLabel(self)
        self.settings_btn = QPushButton("设置", self)
        self.settings_btn.setFlat(True)
        self.settings_btn.setStyleSheet(
            "QPushButton { background: transparent; border: none; "
            f"color: {theme.TEXT_SECOND}; padding: 0 8px; }}"
            f"QPushButton:hover {{ color: {theme.PRIMARY}; }}")
        self.settings_btn.clicked.connect(self._open_settings)
        bar = self.statusBar()
        bar.addWidget(self.model_label)
        bar.addWidget(self.source_label)
        bar.addWidget(self.ai_label)
        bar.addWidget(self.countdown_label)
        bar.addPermanentWidget(QLabel(DISCLAIMER, self))
        bar.addPermanentWidget(self.settings_btn)

        self._tick_timer = QTimer(self)
        self._tick_timer.timeout.connect(self._on_tick)
        self._tick_timer.start(1000)

    def _build_toolbar(self):
        bar = QToolBar("主工具栏", self)
        bar.setMovable(False)
        self.addToolBar(bar)

        defs = [
            ("添加企业", self._add_enterprise),
            ("批量导入", self._import),
            ("刷新监控", self._manual_refresh),
            ("股权穿透", self._penetrate_selected),
            ("上传授信材料", self._upload_material),
            ("AI 影响研判", self._ai_impact),
            ("生成报告", self._gen_report),
            ("设置", self._open_settings),
        ]
        for text, slot in defs:
            act = QAction(text, self)
            act.triggered.connect(slot)
            bar.addAction(act)
        self._refresh_action = bar.actions()[2]

    # ================= 数据加载 =================
    def reload_data(self):
        """全量刷新：企业清单 / 事件流 / 预警 / 地图 / 摘要 / 状态栏。"""
        ents = db.list_enterprises(include_stopped=False)
        self._ent_names = {e.id: e.name for e in ents}
        self.ent_panel.load_data(ents)
        self._reload_events()
        self._reload_map(ents)
        self._update_status_bar()

    def _reload_events(self):
        category = self.feed_panel.cat_combo.currentData() or ""
        source = self.feed_panel.source_combo.currentData() or ""
        events = db.list_events(
            category=category or None, source=source or None,
            monitored_only=True, limit=500)
        self.feed_panel.set_events(events, self._ent_names)
        self.alert_panel.set_alerts(
            [e for e in events if e.severity in ("high", "medium")], self._ent_names)
        self._update_summary(events)

    def _reload_map(self, ents):
        """地图聚合：光点按企业注册城市定位（geo 回退省中心），附带最新事件供弹层。"""
        from ..core import geo
        events = db.list_events(monitored_only=True, limit=2000)
        agg = {}
        latest = {}  # enterprise_id -> [Event]（occurred_at 倒序，前 8 条进弹层）
        for e in events:
            a = agg.setdefault(e.enterprise_id, {"count": 0, "sev": "none", "cats": set()})
            a["count"] += 1
            if SEV_RANK.get(e.severity, 0) > SEV_RANK.get(a["sev"], 0):
                a["sev"] = e.severity
            if e.category:
                a["cats"].add(e.category)
            latest.setdefault(e.enterprise_id, []).append(e)
        spots = []
        for ent in ents:
            a = agg.get(ent.id, {"count": 0, "sev": "none", "cats": set()})
            center = geo.city_center(ent.province, ent.city)
            spots.append({
                "name": ent.name,
                "province": ent.province,
                "city": ent.city,
                "lng": center[0] if center else None,
                "lat": center[1] if center else None,
                "event_count": a["count"],
                "severity": a["sev"],
                "categories": sorted(a["cats"]),
                "events": [{
                    "title": ev.title,
                    "summary": (ev.summary or "")[:160],
                    "source": ev.source,
                    "occurred_at": ev.occurred_at,
                    "severity": ev.severity,
                    "category": ev.category,
                    "ai_verdict": (ev.ai_verdict or "")[:120],
                } for ev in latest.get(ent.id, [])[:8]],
            })
        self.map_view.set_data(spots)

    def _update_summary(self, events):
        """摘要：选中主体已有 AI 影响研判则展示，否则展示本地事件概览。"""
        ent = self.ent_panel.current()
        if ent and ent.id:
            saved = db.get_setting(f"credit_impact_{ent.id}")
            if saved:
                self.ai_panel.set_text(f"【{ent.name} 授信影响研判】\n{saved}")
                return
        by_sev = {"high": 0, "medium": 0, "low": 0}
        by_cat = {}
        for e in events:
            by_sev[e.severity] = by_sev.get(e.severity, 0) + 1
            by_cat[e.category] = by_cat.get(e.category, 0) + 1
        cat_txt = "；".join(
            f"{CATEGORY_LABELS.get(c, c)} {n} 条" for c, n in sorted(by_cat.items())) or "暂无"
        text = (
            f"当前监控主体 {len(self._ent_names)} 家，近 500 条事件："
            f"高危 {by_sev.get('high', 0)}、中危 {by_sev.get('medium', 0)}、"
            f"关注 {by_sev.get('low', 0)}。\n事件构成：{cat_txt}。\n"
            "提示：选中企业后可通过“上传授信材料→AI 影响研判”生成授信影响分析。")
        self.ai_panel.set_text(text)

    # ================= 状态栏 =================
    def _update_status_bar(self):
        models = settings.model_policy().get("fallback_models", [])
        model_id = models[0].get("id", "未配置") if models else "未配置"
        self.model_label.setText(f"当前模型：{model_id}")

        sources = settings.config().get("sources", {})
        total = sum(len(v) for v in sources.values())
        enabled = sum(1 for v in sources.values() for s in v if s.get("enabled"))
        self.source_label.setText(f"数据源：{enabled}/{total} 启用")

        if sync is None:
            self.ai_label.setText("模式：同步模块未就绪")
        elif _agents_mod is not None and _read_env().get("OPENROUTER_API_KEY"):
            self.ai_label.setText("模式：AI 在线")
        else:
            self.ai_label.setText("模式：自动化脚本降级")

        self._setup_timer()

    def _setup_timer(self):
        minutes = int(settings.config().get("app", {}).get("refresh_interval_minutes", 0) or 0)
        if minutes <= 0:
            self._next_refresh = None
            self.countdown_label.setText("自动刷新：关闭")
        else:
            if self._next_refresh is None or self._next_refresh < datetime.now().timestamp():
                self._next_refresh = datetime.now().timestamp() + minutes * 60
        self._update_countdown_visibility()

    def _on_tick(self):
        if self._next_refresh is None:
            return
        remain = int(self._next_refresh - datetime.now().timestamp())
        if remain <= 0:
            self._next_refresh = None
            if sync is None:
                self.statusBar().showMessage("同步模块未就绪，自动刷新已跳过", 5000)
                self._update_status_bar()
                return
            self._manual_refresh()
            return
        self.countdown_label.setText(f"下次刷新：{remain // 60:02d}:{remain % 60:02d}")

    # ================= 显示设置与信息域频率（设置中心） =================
    def _load_ui_settings(self):
        """从 app_settings 恢复显示设置与信息域频率配置。

        无记录（首次启动/未保存过）时保持默认：全部区域显示、全部信息域启用。
        """
        try:
            vis = json.loads(db.get_setting("panel_visibility", "{}") or "{}")
        except (ValueError, TypeError):
            vis = {}
        self._vis = {k: bool(vis.get(k, True)) for k in VISIBILITY_KEYS}
        try:
            self._cat_freq = json.loads(db.get_setting("category_frequency", "{}") or "{}")
        except (ValueError, TypeError):
            self._cat_freq = {}

    def apply_visibility(self, vis: dict):
        """按显示设置显隐各区域（QSplitter/QVBoxLayout 布局自动重排）。

        vis 键见 VISIBILITY_KEYS，缺省键按显示处理；倒计时还与刷新周期联动：
        周期为 0 时即使勾选也不显示。
        """
        self._vis = {k: bool(vis.get(k, True)) for k in VISIBILITY_KEYS}
        self.map_view.setVisible(self._vis["map"])
        self.ent_panel.setVisible(self._vis["enterprise_list"])
        self.feed_panel.setVisible(self._vis["event_feed"])
        self.alert_panel.setVisible(self._vis["alerts"])
        self.ai_panel.setVisible(self._vis["ai_summary"])
        self._update_countdown_visibility()

    def _update_countdown_visibility(self):
        """状态栏倒计时可见性 = 显示设置勾选 且 自动刷新周期大于 0。"""
        minutes = int(settings.config().get("app", {}).get("refresh_interval_minutes", 0) or 0)
        self.countdown_label.setVisible(bool(self._vis.get("countdown", True)) and minutes > 0)

    def due_categories(self) -> list:
        """按 category_frequency 计算本轮参与查询的信息域。

        - enabled=False 的域跳过不查询；
        - 倍率含义：该域每 N 次全局刷新执行一次，N = max(1, round(1/rate))，
          即 rate=1/2 每轮执行（2x 表示可更高频，最小实现与 1x 等价），
          rate=0.5 隔次执行（全局刷新计数 0、2、4…轮时执行）。
        """
        due = []
        for cat in CATEGORIES:
            conf = self._cat_freq.get(cat) or {}
            if not conf.get("enabled", True):
                continue
            try:
                rate = float(conf.get("rate", 1) or 1)
            except (TypeError, ValueError):
                rate = 1.0
            every = max(1, int(round(1.0 / rate))) if rate > 0 else 1
            if self._refresh_counter % every == 0:
                due.append(cat)
        return due

    # ================= 刷新 =================
    def _manual_refresh(self):
        if sync is None:
            QMessageBox.information(self, "提示", "同步模块未就绪，请稍后再试。")
            return
        if self._worker is not None and self._worker.isRunning():
            self._update_status_bar()  # 上一次刷新尚未结束，重排下次自动刷新
            return
        ents = db.list_enterprises(include_stopped=False)
        if not ents:
            QMessageBox.information(self, "提示", "暂无监控企业，请先添加。")
            return
        cats = self.due_categories()
        self._refresh_counter += 1
        if not cats:
            self.statusBar().showMessage(
                "本轮无参与刷新的信息域（已禁用或未到倍率轮次），已跳过", 5000)
            return
        self.statusBar().showMessage(
            f"刷新监控中：共 {len(ents)} 家主体（本轮参与域 {len(cats)}/{len(CATEGORIES)} 个）…")
        self._refresh_action.setEnabled(False)
        self._worker = RefreshWorker(ents, cats, parent=self)
        self._worker.progress.connect(
            lambda i, n, name: self.statusBar().showMessage(f"刷新监控中（{i}/{n}）：{name}"))
        self._worker.done.connect(self._on_refresh_done)
        self._worker.failed.connect(self._on_refresh_fail)
        self._worker.start()

    def _on_refresh_done(self, result):
        self._refresh_action.setEnabled(True)
        self._worker = None
        result = result or {}
        ok, fail = result.get("ok", 0), result.get("fail", 0)
        self.statusBar().showMessage(f"刷新完成：成功 {ok} 家，失败 {fail} 家", 8000)
        self._next_refresh = None
        self.reload_data()

    def _on_refresh_fail(self, msg):
        self._refresh_action.setEnabled(True)
        self._worker = None
        self.statusBar().showMessage("刷新失败", 8000)
        self._next_refresh = None
        self._update_status_bar()
        QMessageBox.warning(self, "刷新失败", msg)

    # ================= 联动 =================
    def _on_spot_clicked(self, name: str):
        self.feed_panel.set_enterprise_filter(name)
        self.statusBar().showMessage(f"已筛选企业事件：{name}", 5000)

    def _on_filters_changed(self, category, source):
        self._reload_events()

    def _on_ent_selected(self, name: str):
        self._reload_events()

    # ================= 工具栏动作 =================
    def _add_enterprise(self):
        dlg = EnterpriseDialog(self)
        if dlg.exec():
            self.reload_data()

    def _edit_enterprise(self, ent_id: int):
        ent = db.get_enterprise(ent_id)
        if ent and EnterpriseDialog(self, ent).exec():
            self.reload_data()

    def _import(self):
        dlg = ImportDialog(self)
        if dlg.exec() and dlg.changed:
            self.reload_data()

    def _penetrate_selected(self):
        ent = self.ent_panel.current()
        if ent is None:
            QMessageBox.information(self, "提示", "请先在左侧选择企业")
            return
        self._penetrate(ent.id)

    def _penetrate(self, ent_id: int):
        ent = db.get_enterprise(ent_id)
        if ent is None:
            return
        dlg = PenetrationDialog(self, ent)
        if dlg.exec():
            self.reload_data()

    def _stop_monitor(self, ent_id: int):
        ent = db.get_enterprise(ent_id)
        if ent is None:
            return
        ret = QMessageBox.question(
            self, "停止监控",
            f"确定停止监控“{ent.name}”吗？历史事件将保留可查。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if ret == QMessageBox.StandardButton.Yes:
            db.delete_enterprise(ent_id)
            self.reload_data()

    def _upload_material(self):
        ent = self.ent_panel.current()
        if ent is None:
            QMessageBox.information(self, "提示", "请先在左侧选择企业")
            return
        dlg = MaterialDialog(self, ent)
        dlg.ai_result.connect(self._show_ai_result)
        dlg.exec()

    def _ai_impact(self):
        """对选中企业最近一份材料直接执行 AI 影响研判。"""
        ent = self.ent_panel.current()
        if ent is None:
            QMessageBox.information(self, "提示", "请先在左侧选择企业")
            return
        try:
            from ..ai.agents import analyze_credit_impact  # 契约函数
        except Exception:
            QMessageBox.information(self, "提示", "AI 模块未就绪，当前为自动化脚本降级模式。")
            return
        materials = db.list_materials(ent.id)
        if not materials:
            QMessageBox.information(self, "提示", "该企业暂无授信材料，请先上传。")
            self._upload_material()
            return
        latest = materials[-1]
        events = db.list_events(enterprise_id=ent.id, monitored_only=True, limit=100)
        self.statusBar().showMessage(f"AI 影响研判中：{ent.name} …")
        self._ai_worker = FnWorker(analyze_credit_impact, latest.text, events, parent=self)
        self._ai_worker.done.connect(
            lambda r, eid=ent.id, name=ent.name: self._on_ai_impact_done(eid, name, r))
        self._ai_worker.failed.connect(self._on_ai_impact_fail)
        self._ai_worker.start()

    def _on_ai_impact_fail(self, msg: str):
        self.statusBar().showMessage("AI 研判失败", 5000)
        QMessageBox.warning(self, "AI 研判失败", msg)

    def _on_ai_impact_done(self, ent_id: int, name: str, result: dict):
        result = result or {}
        text = result.get("text", "")
        if text:
            db.set_setting(f"credit_impact_{ent_id}", text)
            self._show_ai_result(ent_id, text, result.get("engine", ""))
        self.statusBar().showMessage("AI 影响研判完成", 5000)

    def _show_ai_result(self, ent_id: int, text: str, engine: str):
        name = self._ent_names.get(ent_id, f"#{ent_id}")
        self.ai_panel.set_text(f"【{name} 授信影响研判】\n{text}", engine)

    def _gen_report(self):
        ReportDialog(self).exec()

    def _open_settings(self):
        if SettingsDialog(self).exec():
            settings.reload()
            self._load_ui_settings()
            self.apply_visibility(self._vis)
            self._next_refresh = None
            self._update_status_bar()
            self.reload_data()
