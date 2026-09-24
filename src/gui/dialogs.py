"""业务对话框：企业编辑、股权穿透、批量导入/导出、授信材料、全局设置。

并行开发中的模块（core.sync / core.penetration / ai.agents）一律 try/except 引入，
未就绪时在界面上明确提示，不阻断其余功能。
"""
import json
import os
from datetime import datetime, timedelta

import yaml
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QTextEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..core import db, settings
from ..core.models import CATEGORIES, CATEGORY_LABELS, Enterprise
from ..core.paths import app_root, reports_dir
from .workers import FnWorker

# ---------- 可选依赖（并行开发中，缺失时降级） ----------
try:
    from ..core import sync  # noqa: F401
except Exception:
    sync = None

try:
    from ..core import penetration as pen_mod
except Exception:
    pen_mod = None

try:
    from ..ai import agents  # noqa: F401
except Exception:
    agents = None

try:
    import docx as _docx
except Exception:
    _docx = None

try:
    from pypdf import PdfReader as _PdfReader
except Exception:
    _PdfReader = None

PROVINCES = [
    "北京市", "天津市", "河北省", "山西省", "内蒙古自治区", "辽宁省", "吉林省",
    "黑龙江省", "上海市", "江苏省", "浙江省", "安徽省", "福建省", "江西省",
    "山东省", "河南省", "湖北省", "湖南省", "广东省", "广西壮族自治区", "海南省",
    "重庆市", "四川省", "贵州省", "云南省", "西藏自治区", "陕西省", "甘肃省",
    "青海省", "宁夏回族自治区", "新疆维吾尔自治区", "香港特别行政区", "澳门特别行政区", "台湾省",
]

CHAPTERS = [
    ("overview", "对象概况"),
    ("events", "事件明细"),
    ("ai", "AI 影响研判"),
    ("penetration", "穿透图谱说明"),
    ("alerts", "预警汇总"),
]


# ---------- .env 读写（API Key 等敏感配置） ----------
def _env_path() -> str:
    return os.path.join(app_root(), ".env")


def _read_env() -> dict:
    env = {}
    p = _env_path()
    if os.path.exists(p):
        with open(p, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env


def _write_env(updates: dict):
    """合并写入 .env，保留既有键；值完整写入不截断。"""
    env = _read_env()
    env.update(updates)
    with open(_env_path(), "w", encoding="utf-8") as f:
        f.write("\n".join(f"{k}={v}" for k, v in env.items()) + "\n")


def _resolve(mod, names):
    for n in names:
        if hasattr(mod, n):
            return getattr(mod, n)
    return None


# =====================================================================
# 企业新增/编辑
# =====================================================================
class EnterpriseDialog(QDialog):
    """新增/编辑企业表单：名称/信用代码/省/市/授信备注。"""

    def __init__(self, parent=None, enterprise: Enterprise = None):
        super().__init__(parent)
        self.ent = enterprise
        self.setWindowTitle("编辑企业" if enterprise else "添加企业")
        self.setMinimumWidth(420)

        self.name_edit = QLineEdit(self)
        self.code_edit = QLineEdit(self)
        self.code_edit.setPlaceholderText("统一社会信用代码（选填）")
        self.province_combo = QComboBox(self)
        self.province_combo.addItem("", "")
        self.province_combo.addItems(PROVINCES)
        self.province_combo.setEditable(True)
        self.city_edit = QLineEdit(self)
        self.note_edit = QTextEdit(self)
        self.note_edit.setFixedHeight(72)
        self.note_edit.setPlaceholderText("授信情况备注（额度、期限、担保方式等）")

        form = QFormLayout()
        form.addRow("企业名称*", self.name_edit)
        form.addRow("信用代码", self.code_edit)
        form.addRow("省份", self.province_combo)
        form.addRow("城市", self.city_edit)
        form.addRow("授信备注", self.note_edit)

        if enterprise:
            self.name_edit.setText(enterprise.name)
            self.code_edit.setText(enterprise.credit_code)
            idx = self.province_combo.findText(enterprise.province)
            if idx >= 0:
                self.province_combo.setCurrentIndex(idx)
            else:
                self.province_combo.setEditText(enterprise.province)
            self.city_edit.setText(enterprise.city)
            self.note_edit.setPlainText(enterprise.credit_note)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.button(QDialogButtonBox.StandardButton.Ok).setText("保存")
        btns.button(QDialogButtonBox.StandardButton.Ok).setProperty("primary", True)
        btns.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        btns.accepted.connect(self._save)
        btns.rejected.connect(self.reject)

        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(btns)

    def _save(self):
        name = self.name_edit.text().strip()
        if not name:
            QMessageBox.warning(self, "提示", "请填写企业名称")
            return
        fields = dict(
            name=name,
            credit_code=self.code_edit.text().strip(),
            province=self.province_combo.currentText().strip(),
            city=self.city_edit.text().strip(),
            credit_note=self.note_edit.toPlainText().strip(),
        )
        if self.ent and self.ent.id:
            db.update_enterprise(self.ent.id, **fields)
        else:
            db.upsert_enterprise(
                fields["name"], fields["credit_code"], fields["province"],
                fields["city"], fields["credit_note"], monitored=1)
        self.accept()


# =====================================================================
# 股权穿透
# =====================================================================
class PenetrationDialog(QDialog):
    """输入/选择企业 -> penetration.penetrate -> 树展示（上穿/下穿）-> 勾选入库。"""

    def __init__(self, parent=None, enterprise: Enterprise = None):
        super().__init__(parent)
        self.base = enterprise
        self.setWindowTitle("股权穿透")
        self.setMinimumSize(560, 520)
        self._related = []

        self.ent_combo = QComboBox(self)
        self.ent_combo.setEditable(True)
        for e in db.list_enterprises():
            if not e.is_related:
                self.ent_combo.addItem(e.name, e.id)
        if enterprise:
            self.ent_combo.setEditText(enterprise.name)
        self.up_spin = QSpinBox(self)
        self.up_spin.setRange(1, 3)
        self.down_spin = QSpinBox(self)
        self.down_spin.setRange(1, 3)
        pcfg = settings.config().get("penetration", {})
        self.up_spin.setValue(int(pcfg.get("up_levels", 2)))
        self.down_spin.setValue(int(pcfg.get("down_levels", 2)))

        self.go_btn = QPushButton("开始穿透", self)
        self.go_btn.setProperty("primary", True)
        self.go_btn.clicked.connect(self._run)

        self.tree = QTreeWidget(self)
        self.tree.setHeaderLabels(["主体（勾选纳入监控）", "关系", "层级"])

        self.select_all_btn = QPushButton("全选", self)
        self.select_none_btn = QPushButton("全不选", self)
        self.select_all_btn.clicked.connect(lambda: self._set_all_checked(True))
        self.select_none_btn.clicked.connect(lambda: self._set_all_checked(False))

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.button(QDialogButtonBox.StandardButton.Ok).setText("保存所选并入库")
        btns.button(QDialogButtonBox.StandardButton.Ok).setProperty("primary", True)
        btns.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        btns.accepted.connect(self._save)
        btns.rejected.connect(self.reject)

        top = QHBoxLayout()
        top.addWidget(QLabel("企业", self))
        top.addWidget(self.ent_combo, 1)
        top.addWidget(QLabel("上穿层数", self))
        top.addWidget(self.up_spin)
        top.addWidget(QLabel("下穿层数", self))
        top.addWidget(self.down_spin)
        top.addWidget(self.go_btn)

        sel = QHBoxLayout()
        sel.addWidget(self.select_all_btn)
        sel.addWidget(self.select_none_btn)
        sel.addStretch(1)

        lay = QVBoxLayout(self)
        lay.addLayout(top)
        lay.addWidget(self.tree, 1)
        lay.addLayout(sel)
        lay.addWidget(btns)

        if pen_mod is None:
            self.go_btn.setEnabled(False)
            self.tree.addTopLevelItem(QTreeWidgetItem(["同步/穿透模块未就绪，请稍后再试"]))
        elif enterprise:
            self._run()

    def _run(self):
        name = self.ent_combo.currentText().strip()
        if not name:
            QMessageBox.warning(self, "提示", "请输入或选择企业名称")
            return
        ent_id = self.ent_combo.currentData()
        if ent_id:
            base = db.get_enterprise(int(ent_id))
        else:
            base = Enterprise(name=name)
        self.go_btn.setEnabled(False)
        self.tree.clear()
        self.tree.addTopLevelItem(QTreeWidgetItem(["穿透中，请稍候…"]))
        self._worker = FnWorker(
            pen_mod.penetrate, base, self.up_spin.value(), self.down_spin.value(), parent=self)
        self._worker.done.connect(self._on_done)
        self._worker.failed.connect(self._on_fail)
        self._worker.start()

    def _on_done(self, related):
        self.go_btn.setEnabled(True)
        self._related = list(related or [])
        self._render()

    def _on_fail(self, msg):
        self.go_btn.setEnabled(True)
        self.tree.clear()
        self.tree.addTopLevelItem(QTreeWidgetItem([f"穿透失败：{msg}"]))

    def _render(self):
        self.tree.clear()
        name = self.ent_combo.currentText().strip()
        root = QTreeWidgetItem([name, "本企业", "第 0 层"])
        root.setFlags(root.flags() & ~Qt.ItemFlag.ItemIsUserCheckable)
        self.tree.addTopLevelItem(root)
        groups = {}
        for r in self._related:
            direction = getattr(r, "direction", "") or "down"
            level = getattr(r, "pen_level", 1) or 1
            groups.setdefault((direction, level), []).append(r)
        for (direction, level) in sorted(groups):
            label = f"{'上穿（股东）' if direction == 'up' else '下穿（对外投资）'} 第 {level} 层"
            node = QTreeWidgetItem([label, "", f"第 {level} 层"])
            node.setFlags(node.flags() & ~Qt.ItemFlag.ItemIsUserCheckable)
            root.addChild(node)
            for r in groups[(direction, level)]:
                it = QTreeWidgetItem([r.name, getattr(r, "relation", "") or "—", f"第 {level} 层"])
                it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                it.setCheckState(0, Qt.CheckState.Checked)
                it.setData(0, Qt.ItemDataRole.UserRole, r)
                node.addChild(it)
        self.tree.expandAll()

    def _set_all_checked(self, checked: bool):
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked

        def walk(item):
            if item.childCount() == 0 and (item.flags() & Qt.ItemFlag.ItemIsUserCheckable):
                item.setCheckState(0, state)
            for i in range(item.childCount()):
                walk(item.child(i))

        for i in range(self.tree.topLevelItemCount()):
            walk(self.tree.topLevelItem(i))

    def _save(self):
        if pen_mod is None:
            self.reject()
            return
        base_id = self.ent_combo.currentData()
        if not base_id:
            # 手工输入的新企业：先入库取得主体 id
            base_id = db.upsert_enterprise(self.ent_combo.currentText().strip())
        selected = []
        self._collect(self.tree.invisibleRootItem(), selected)
        if not selected:
            QMessageBox.information(self, "提示", "未勾选任何主体")
            return
        try:
            pen_mod.save_related(int(base_id), self._related, selected)
        except Exception as e:
            QMessageBox.warning(self, "穿透入库失败", str(e))
            return
        self.accept()

    def _collect(self, node, out):
        for i in range(node.childCount()):
            child = node.child(i)
            if child.childCount() == 0:
                if child.checkState(0) == Qt.CheckState.Checked:
                    ent = child.data(0, Qt.ItemDataRole.UserRole)
                    if ent is not None:
                        out.append(getattr(ent, "name", str(ent)))
            else:
                self._collect(child, out)


# =====================================================================
# 批量导入 / 导出
# =====================================================================
class ImportDialog(QDialog):
    """文件选择 -> 调 core.sync 导入/导出 csv|xlsx；成功后由主窗口刷新。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("批量导入 / 导出")
        self.setMinimumWidth(480)
        self.changed = False

        self.path_edit = QLineEdit(self)
        browse = QPushButton("浏览…", self)
        browse.clicked.connect(self._browse)
        self.import_btn = QPushButton("导入企业", self)
        self.import_btn.setProperty("primary", True)
        self.import_btn.clicked.connect(self._do_import)
        self.export_ent_btn = QPushButton("导出企业清单", self)
        self.export_ent_btn.clicked.connect(lambda: self._do_export("enterprise"))
        self.export_evt_btn = QPushButton("导出事件明细", self)
        self.export_evt_btn.clicked.connect(lambda: self._do_export("event"))

        form = QFormLayout()
        path_row = QHBoxLayout()
        path_row.addWidget(self.path_edit, 1)
        path_row.addWidget(browse)
        form.addRow("文件（csv/xlsx）", path_row)

        imp_group = QGroupBox("导入")
        imp_lay = QVBoxLayout(imp_group)
        imp_lay.addLayout(form)
        imp_lay.addWidget(self.import_btn)

        exp_group = QGroupBox("导出")
        exp_lay = QHBoxLayout(exp_group)
        exp_lay.addWidget(self.export_ent_btn)
        exp_lay.addWidget(self.export_evt_btn)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        btns.button(QDialogButtonBox.StandardButton.Close).setText("关闭")
        btns.rejected.connect(self.reject)

        lay = QVBoxLayout(self)
        lay.addWidget(imp_group)
        lay.addWidget(exp_group)
        lay.addWidget(btns)

        if sync is None:
            tip = QLabel("同步模块未就绪，导入/导出暂不可用。", self)
            tip.setStyleSheet("color:#D93026;")
            lay.insertWidget(0, tip)
            for b in (self.import_btn, self.export_ent_btn, self.export_evt_btn):
                b.setEnabled(False)

    def _browse(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择文件", "", "表格文件 (*.csv *.xlsx)")
        if path:
            self.path_edit.setText(path)

    @staticmethod
    def _call(fn, *args):
        """兼容并行开发中 sync 函数的不同签名：先位置后关键字。"""
        try:
            return fn(*args)
        except TypeError:
            names = ("path", "file_path", "filepath")
            return fn(**dict(zip(names, args)))

    def _do_import(self):
        path = self.path_edit.text().strip()
        if not path or not os.path.exists(path):
            QMessageBox.warning(self, "提示", "请选择有效的 csv/xlsx 文件")
            return
        ext = os.path.splitext(path)[1].lower()
        if ext == ".xlsx":
            fn = _resolve(sync, ["import_enterprises_from_xlsx", "import_enterprises_xlsx",
                                 "import_enterprises", "import_csv", "import_file"])
        else:
            fn = _resolve(sync, ["import_enterprises_from_csv", "import_enterprises_csv",
                                 "import_enterprises", "import_csv", "import_file"])
        if fn is None:
            QMessageBox.warning(self, "同步模块未就绪", "未找到可用的导入函数。")
            return
        self.import_btn.setEnabled(False)
        self._worker = FnWorker(self._call, fn, path, parent=self)
        self._worker.done.connect(lambda _r: self._on_import_done(None))
        self._worker.failed.connect(self._on_error)
        self._worker.start()

    def _on_import_done(self, _msg):
        self.import_btn.setEnabled(True)
        self.changed = True
        QMessageBox.information(self, "导入完成", "数据导入成功，点击“关闭”后主界面将自动刷新。")

    def _do_export(self, kind: str):
        if kind == "enterprise":
            fn = _resolve(sync, ["export_enterprises_to_csv", "export_enterprises_to_xlsx",
                                 "export_enterprises", "export_enterprises_csv", "export_csv"])
        else:
            fn = _resolve(sync, ["export_events_to_csv", "export_events_to_xlsx",
                                 "export_events", "export_events_csv"])
        if fn is None:
            QMessageBox.information(
                self, "提示", "同步模块暂未提供事件明细导出函数，可稍后再试或导出企业清单。")
            return
        default = os.path.join(
            reports_dir(), f"{kind}_{datetime.now():%Y%m%d_%H%M%S}.csv")
        path, _f = QFileDialog.getSaveFileName(
            self, "导出到", default, "CSV (*.csv);;Excel (*.xlsx)")
        if not path:
            return
        self._worker = FnWorker(self._export_task, fn, path, parent=self)
        self._worker.done.connect(lambda _r: self._on_export_done(path))
        self._worker.failed.connect(self._on_error)
        self._worker.start()

    @staticmethod
    def _export_task(fn, path):
        try:
            return fn(path)
        except TypeError:
            return fn(path)

    def _on_export_done(self, path):
        QMessageBox.information(self, "导出完成", f"已导出至：\n{path}")

    def _on_error(self, msg):
        self.import_btn.setEnabled(True)
        QMessageBox.warning(self, "操作失败", msg)


# =====================================================================
# 授信材料上传 + AI 影响研判
# =====================================================================
class MaterialDialog(QDialog):
    """上传授信材料（docx/pdf/txt），列表展示，支持 AI 影响研判。

    研判结果经 ai_result 信号送回主窗口展示并入库 app_settings。
    """

    ai_result = Signal(int, str, str)  # ent_id, 研判文本, engine

    def __init__(self, parent=None, enterprise: Enterprise = None):
        super().__init__(parent)
        self.ent = enterprise
        self.setWindowTitle("授信材料")
        self.setMinimumSize(520, 460)

        self.path_edit = QLineEdit(self)
        browse = QPushButton("浏览…", self)
        browse.clicked.connect(self._browse)
        upload_btn = QPushButton("上传材料", self)
        upload_btn.setProperty("primary", True)
        upload_btn.clicked.connect(self._upload)

        self.list = QListWidget(self)

        self.ai_btn = QPushButton("AI 影响研判", self)
        self.ai_btn.clicked.connect(self._run_ai)
        self.tip_label = QLabel("", self)
        self.tip_label.setProperty("secondary", True)

        up_row = QHBoxLayout()
        up_row.addWidget(self.path_edit, 1)
        up_row.addWidget(browse)
        up_row.addWidget(upload_btn)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        btns.button(QDialogButtonBox.StandardButton.Close).setText("关闭")
        btns.rejected.connect(self.reject)

        lay = QVBoxLayout(self)
        if enterprise:
            cap = QLabel(f"企业：{enterprise.name}", self)
            cap.setProperty("h2", True)
            lay.addWidget(cap)
        lay.addLayout(up_row)
        lay.addWidget(QLabel("已上传材料", self))
        lay.addWidget(self.list, 1)
        row = QHBoxLayout()
        row.addWidget(self.ai_btn)
        row.addWidget(self.tip_label, 1)
        lay.addLayout(row)
        lay.addWidget(btns)

        if agents is None:
            self.ai_btn.setEnabled(False)
            self.tip_label.setText("AI 模块未就绪")
        elif _docx is None and _PdfReader is None:
            self.tip_label.setText("提示：缺少 python-docx / pypdf，docx/pdf 文本提取不可用")
        self._reload_list()

    def _reload_list(self):
        self.list.clear()
        if not self.ent or not self.ent.id:
            return
        for m in db.list_materials(self.ent.id):
            item = QListWidgetItem(f"{m.filename}　{m.uploaded_at}")
            item.setData(Qt.ItemDataRole.UserRole, m)
            self.list.addItem(item)

    def _browse(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择材料", "", "授信材料 (*.docx *.pdf *.txt)")
        if path:
            self.path_edit.setText(path)

    @staticmethod
    def _extract_text(path: str) -> str:
        ext = os.path.splitext(path)[1].lower()
        if ext == ".docx":
            if _docx is None:
                raise RuntimeError("缺少 python-docx，无法解析 docx")
            d = _docx.Document(path)
            return "\n".join(p.text for p in d.paragraphs if p.text.strip())
        if ext == ".pdf":
            if _PdfReader is None:
                raise RuntimeError("缺少 pypdf，无法解析 pdf")
            reader = _PdfReader(path)
            return "\n".join((page.extract_text() or "") for page in reader.pages)
        for enc in ("utf-8", "gbk"):
            try:
                with open(path, "r", encoding=enc) as f:
                    return f.read()
            except UnicodeDecodeError:
                continue
        raise RuntimeError("txt 编码无法识别（仅支持 UTF-8/GBK）")

    def _upload(self):
        path = self.path_edit.text().strip()
        if not path or not os.path.exists(path):
            QMessageBox.warning(self, "提示", "请选择有效的 docx/pdf/txt 文件")
            return
        try:
            text = self._extract_text(path)
        except Exception as e:
            QMessageBox.warning(self, "解析失败", str(e))
            return
        db.add_material(self.ent.id, os.path.basename(path), path, text)
        self.path_edit.clear()
        self._reload_list()

    def _run_ai(self):
        item = self.list.currentItem()
        if not item:
            QMessageBox.information(self, "提示", "请先选择一份材料")
            return
        material = item.data(Qt.ItemDataRole.UserRole)
        since = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
        events = db.list_events(enterprise_id=self.ent.id, since=since, monitored_only=True)
        self.ai_btn.setEnabled(False)
        self.tip_label.setText("AI 研判中，请稍候…")
        self._worker = FnWorker(
            agents.analyze_credit_impact, material.text, events, parent=self)
        self._worker.done.connect(self._on_ai_done)
        self._worker.failed.connect(self._on_ai_fail)
        self._worker.start()

    def _on_ai_done(self, result: dict):
        self.ai_btn.setEnabled(True)
        result = result or {}
        text = result.get("text", "")
        engine = result.get("engine", "")
        self.tip_label.setText("研判完成" + ("（引擎：%s）" % engine if engine else ""))
        if text:
            db.set_setting(f"credit_impact_{self.ent.id}", text)
            self.ai_result.emit(self.ent.id, text, engine)
        if result.get("pending_review"):
            QMessageBox.information(self, "AI 影响研判", "结果已生成，标记为待人工复核。")

    def _on_ai_fail(self, msg: str):
        self.ai_btn.setEnabled(True)
        self.tip_label.setText("")
        QMessageBox.warning(self, "AI 研判失败", msg)


# =====================================================================
# 全局设置
# =====================================================================
class SettingsDialog(QDialog):
    """六页签：显示设置 / 频率设置 / 监控设置 / 数据源设置 / AI 设置 / 报告设置。"""

    # 显示设置页的界面元素清单：key -> 标签
    VISIBILITY_DEFS = [
        ("map", "中央地图"),
        ("enterprise_list", "企业清单"),
        ("event_feed", "事件流"),
        ("alerts", "预警列表"),
        ("ai_summary", "AI 研判摘要"),
        ("countdown", "状态栏刷新倒计时"),
    ]

    # 信息域频率倍率选项：文案 -> 倍率值
    RATE_DEFS = [
        ("0.5x（隔次执行）", 0.5),
        ("1x（每轮执行）", 1.0),
        ("2x（每轮执行）", 2.0),
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("设置")
        self.setMinimumSize(560, 480)

        self.tabs = QTabWidget(self)
        self.tabs.addTab(self._display_tab(), "显示设置")
        self.tabs.addTab(self._frequency_tab(), "频率设置")
        self.tabs.addTab(self._monitor_tab(), "监控设置")
        self.tabs.addTab(self._sources_tab(), "数据源设置")
        self.tabs.addTab(self._ai_tab(), "AI 设置")
        self.tabs.addTab(self._report_tab(), "报告设置")

        # 两处“刷新周期”输入保持同步（频率设置页 / 监控设置页）
        self.refresh_spin.valueChanged.connect(self.freq_refresh_spin.setValue)
        self.freq_refresh_spin.valueChanged.connect(self.refresh_spin.setValue)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.button(QDialogButtonBox.StandardButton.Ok).setText("保存")
        btns.button(QDialogButtonBox.StandardButton.Ok).setProperty("primary", True)
        btns.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        btns.accepted.connect(self._save)
        btns.rejected.connect(self.reject)

        lay = QVBoxLayout(self)
        lay.addWidget(self.tabs)
        lay.addWidget(btns)

    # ---------- ① 显示设置 ----------
    def _display_tab(self):
        w = QWidget(self)
        saved = {}
        try:
            saved = json.loads(db.get_setting("panel_visibility", "{}"))
        except Exception:
            saved = {}

        self.vis_checks = {}
        box = QGroupBox("界面元素显示（保存后即时生效）")
        bl = QVBoxLayout(box)
        for key, label in self.VISIBILITY_DEFS:
            cb = QCheckBox(label, self)
            cb.setChecked(bool(saved.get(key, True)))
            bl.addWidget(cb)
            self.vis_checks[key] = cb

        lay = QVBoxLayout(w)
        lay.addWidget(box)
        lay.addStretch(1)
        return w

    # ---------- ② 频率设置 ----------
    def _frequency_tab(self):
        w = QWidget(self)
        cfg = settings.config()
        app_cfg = cfg.get("app", {})
        net_cfg = cfg.get("network", {})
        saved = {}
        try:
            saved = json.loads(db.get_setting("category_frequency", "{}"))
        except Exception:
            saved = {}

        self.freq_refresh_spin = QSpinBox(self)
        self.freq_refresh_spin.setRange(0, 720)
        self.freq_refresh_spin.setSuffix(" 分钟")
        self.freq_refresh_spin.setValue(
            int(app_cfg.get("refresh_interval_minutes", 30) or 0))
        self.freq_refresh_spin.setSpecialValueText("关闭自动刷新")

        self.cat_checks = {}
        self.rate_combos = {}
        form = QFormLayout()
        form.addRow("全局刷新周期", self.freq_refresh_spin)
        for cat in CATEGORIES:
            conf = saved.get(cat) or {}
            en = QCheckBox(CATEGORY_LABELS.get(cat, cat), self)
            en.setChecked(bool(conf.get("enabled", True)))
            combo = QComboBox(self)
            for label, rate in self.RATE_DEFS:
                combo.addItem(label, rate)
            try:
                cur = float(conf.get("rate", 1) or 1)
            except (TypeError, ValueError):
                cur = 1.0
            idx = combo.findData(cur)
            combo.setCurrentIndex(idx if idx >= 0 else 1)
            self.cat_checks[cat] = en
            self.rate_combos[cat] = combo
            form.addRow(en, combo)

        self.timeout_spin = QSpinBox(self)
        self.timeout_spin.setRange(1, 300)
        self.timeout_spin.setSuffix(" 秒")
        self.timeout_spin.setValue(int(net_cfg.get("timeout_seconds", 15) or 15))
        form.addRow("网络超时", self.timeout_spin)

        tip = QLabel(
            "倍率含义：0.5x=每两次全局刷新执行一次该域，1x/2x=每轮均执行；"
            "禁用的信息域刷新时跳过不查询。全局刷新周期为 0 时关闭自动刷新。", self)
        tip.setProperty("secondary", True)
        tip.setWordWrap(True)

        lay = QVBoxLayout(w)
        lay.addLayout(form)
        lay.addWidget(tip)
        lay.addStretch(1)
        return w

    # ---------- ③ 监控设置 ----------
    def _monitor_tab(self):
        w = QWidget(self)
        cfg = settings.config()
        app_cfg = cfg.get("app", {})
        pen_cfg = cfg.get("penetration", {})

        self.refresh_spin = QSpinBox(self)
        self.refresh_spin.setRange(0, 1440)
        self.refresh_spin.setSuffix(" 分钟")
        self.refresh_spin.setValue(int(app_cfg.get("refresh_interval_minutes", 30) or 0))
        self.refresh_spin.setSpecialValueText("关闭自动刷新")
        self.proxy_edit = QLineEdit(str(app_cfg.get("proxy", "") or ""), self)
        self.proxy_edit.setPlaceholderText("如 http://127.0.0.1:7890，留空不使用")
        self.up_spin = QSpinBox(self)
        self.up_spin.setRange(1, 3)
        self.up_spin.setValue(int(pen_cfg.get("up_levels", 2)))
        self.down_spin = QSpinBox(self)
        self.down_spin.setRange(1, 3)
        self.down_spin.setValue(int(pen_cfg.get("down_levels", 2)))

        form = QFormLayout()
        form.addRow("定时刷新周期", self.refresh_spin)
        form.addRow("HTTP 代理", self.proxy_edit)
        form.addRow("上穿层数", self.up_spin)
        form.addRow("下穿层数", self.down_spin)

        lay = QVBoxLayout(w)
        lay.addLayout(form)
        lay.addStretch(1)
        return w

    # ---------- ④ 数据源设置 ----------
    def _sources_tab(self):
        w = QWidget(self)
        self.src_tree = QTreeWidget(self)
        self.src_tree.setHeaderLabels(["数据源（勾选启用）", "类"])
        self.src_tree.header().resizeSection(0, 300)

        up_btn = QPushButton("上移", self)
        down_btn = QPushButton("下移", self)
        up_btn.clicked.connect(lambda: self._move_source(-1))
        down_btn.clicked.connect(lambda: self._move_source(1))

        cat_labels = dict(CATEGORY_LABELS)
        sources_cfg = settings.config().get("sources", {})
        for cat in ("news", "biz_change", "lawsuit", "enforcement"):
            root = QTreeWidgetItem([cat_labels.get(cat, cat)])
            root.setFlags(root.flags() & ~Qt.ItemFlag.ItemIsUserCheckable)
            self.src_tree.addTopLevelItem(root)
            for spec in sources_cfg.get(cat, []):
                it = QTreeWidgetItem([str(spec.get("name", "")), str(spec.get("class", ""))])
                it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                it.setCheckState(0, Qt.CheckState.Checked if spec.get("enabled") else Qt.CheckState.Unchecked)
                it.setData(0, Qt.ItemDataRole.UserRole, {"category": cat, "spec": dict(spec)})
                root.addChild(it)
            self.src_tree.expandAll()

        btns = QHBoxLayout()
        btns.addWidget(up_btn)
        btns.addWidget(down_btn)
        btns.addStretch(1)

        lay = QVBoxLayout(w)
        tip = QLabel("排在前面的数据源优先调用；取消勾选即停用。", self)
        tip.setProperty("secondary", True)
        lay.addWidget(tip)
        lay.addWidget(self.src_tree, 1)
        lay.addLayout(btns)
        return w

    def _move_source(self, delta: int):
        item = self.src_tree.currentItem()
        if not item or item.parent() is None:
            return
        parent = item.parent()
        idx = parent.indexOfChild(item)
        new_idx = idx + delta
        if new_idx < 0 or new_idx >= parent.childCount():
            return
        parent.takeChild(idx)
        parent.insertChild(new_idx, item)
        self.src_tree.setCurrentItem(item)

    # ---------- ⑤ AI 设置 ----------
    def _ai_tab(self):
        w = QWidget(self)
        env = _read_env()
        policy = settings.model_policy()

        self.model_list = QListWidget(self)
        for m in policy.get("fallback_models", []):
            self.model_list.addItem(m.get("id", "") if isinstance(m, dict) else str(m))
        self.model_edit = QLineEdit(self)
        self.model_edit.setPlaceholderText("模型 ID，如 deepseek/deepseek-chat-v3.1:free")
        add_btn = QPushButton("添加", self)
        del_btn = QPushButton("删除", self)
        add_btn.clicked.connect(self._add_model)
        del_btn.clicked.connect(self._del_model)

        self.api_base_edit = QLineEdit(env.get("OPENROUTER_API_BASE", "https://openrouter.ai/api/v1"), self)
        self.api_key_edit = QLineEdit(env.get("OPENROUTER_API_KEY", ""), self)
        self.api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key_edit.setPlaceholderText("密钥回显打码，保存时完整写入 .env")
        show_key = QCheckBox("显示密钥", self)
        show_key.toggled.connect(
            lambda on: self.api_key_edit.setEchoMode(
                QLineEdit.EchoMode.Normal if on else QLineEdit.EchoMode.Password))
        self.ai_proxy_edit = QLineEdit(env.get("OPENROUTER_HTTP_PROXY", ""), self)
        self.ai_proxy_edit.setPlaceholderText("AI 请求使用的 HTTP 代理，留空不使用")

        form = QFormLayout()
        form.addRow("API Base", self.api_base_edit)
        key_row = QHBoxLayout()
        key_row.addWidget(self.api_key_edit, 1)
        key_row.addWidget(show_key)
        form.addRow("API Key", key_row)
        form.addRow("HTTP 代理", self.ai_proxy_edit)

        model_box = QGroupBox("Fallback 模型列表（按序尝试）")
        ml = QVBoxLayout(model_box)
        mrow = QHBoxLayout()
        mrow.addWidget(self.model_edit, 1)
        mrow.addWidget(add_btn)
        mrow.addWidget(del_btn)
        ml.addLayout(mrow)
        ml.addWidget(self.model_list)

        lay = QVBoxLayout(w)
        lay.addLayout(form)
        lay.addWidget(model_box, 1)
        return w

    def _add_model(self):
        mid = self.model_edit.text().strip()
        if not mid:
            return
        if self.model_list.findItems(mid, Qt.MatchFlag.MatchExactly):
            return
        self.model_list.addItem(mid)
        self.model_edit.clear()

    def _del_model(self):
        for it in self.model_list.selectedItems():
            self.model_list.takeItem(self.model_list.row(it))

    # ---------- ⑥ 报告设置 ----------
    def _report_tab(self):
        w = QWidget(self)
        saved = {}
        try:
            saved = json.loads(db.get_setting("report_sections", "{}"))
        except Exception:
            saved = {}

        self.chapter_checks = {}
        box = QGroupBox("默认生成章节")
        bl = QVBoxLayout(box)
        for key, label in CHAPTERS:
            cb = QCheckBox(label, self)
            cb.setChecked(bool(saved.get(key, True)))
            bl.addWidget(cb)
            self.chapter_checks[key] = cb

        self.dir_edit = QLineEdit(db.get_setting("report_save_dir", reports_dir()), self)
        browse = QPushButton("浏览…", self)
        browse.clicked.connect(self._browse_dir)
        dir_row = QHBoxLayout()
        dir_row.addWidget(self.dir_edit, 1)
        dir_row.addWidget(browse)

        self.type_combo = QComboBox(self)
        self.type_combo.addItems(["日报", "周报", "事件专报"])
        cur = db.get_setting("report_type", "周报")
        idx = self.type_combo.findText(cur)
        if idx >= 0:
            self.type_combo.setCurrentIndex(idx)

        form = QFormLayout()
        form.addRow("默认保存目录", dir_row)
        form.addRow("默认报告类型", self.type_combo)

        lay = QVBoxLayout(w)
        lay.addWidget(box)
        lay.addLayout(form)
        lay.addStretch(1)
        return w

    def _browse_dir(self):
        d = QFileDialog.getExistingDirectory(self, "选择报告保存目录", self.dir_edit.text())
        if d:
            self.dir_edit.setText(d)

    # ---------- 保存 ----------
    def _save(self):
        # ① 监控设置 -> config.yaml
        cfg = settings.config()
        cfg.setdefault("app", {})
        cfg["app"]["refresh_interval_minutes"] = int(self.refresh_spin.value())
        cfg["app"]["proxy"] = self.proxy_edit.text().strip()
        cfg.setdefault("penetration", {})
        cfg["penetration"]["up_levels"] = int(self.up_spin.value())
        cfg["penetration"]["down_levels"] = int(self.down_spin.value())
        settings.save_config()

        # ② 频率设置：全局刷新周期（与监控设置页同步输入）与网络超时 -> config.yaml
        cfg["app"]["refresh_interval_minutes"] = int(self.freq_refresh_spin.value())
        cfg.setdefault("network", {})
        cfg["network"]["timeout_seconds"] = int(self.timeout_spin.value())
        settings.save_config()

        # ③ 数据源启停与排序 -> config.yaml（按树节点顺序与勾选状态重建）
        sources = {}
        for i in range(self.src_tree.topLevelItemCount()):
            root = self.src_tree.topLevelItem(i)
            for j in range(root.childCount()):
                child = root.child(j)
                data = child.data(0, Qt.ItemDataRole.UserRole) or {}
                cat = data.get("category")
                spec = dict(data.get("spec") or {})
                spec["enabled"] = child.checkState(0) == Qt.CheckState.Checked
                sources.setdefault(cat, []).append(spec)
        src_cfg = settings.config().get("sources", {})
        rebuilt = {}
        for cat in src_cfg:
            rebuilt[cat] = sources.get(cat, src_cfg[cat])
        for cat, lst in sources.items():
            rebuilt.setdefault(cat, lst)
        cfg["sources"] = rebuilt
        settings.save_config()
        settings.reload()

        # ④ AI 设置 -> model_policy.yaml + .env
        models = [{"id": self.model_list.item(i).text(), "cost": 0}
                  for i in range(self.model_list.count())]
        policy_path = os.path.join(app_root(), "model_policy.yaml")
        try:
            with open(policy_path, "r", encoding="utf-8") as f:
                policy = yaml.safe_load(f) or {}
        except Exception:
            policy = {}
        policy["fallback_models"] = models
        with open(policy_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(policy, f, allow_unicode=True, sort_keys=False)
        _write_env({
            "OPENROUTER_API_KEY": self.api_key_edit.text().strip(),
            "OPENROUTER_API_BASE": self.api_base_edit.text().strip(),
            "OPENROUTER_HTTP_PROXY": self.ai_proxy_edit.text().strip(),
        })

        # ⑤ 报告设置 -> app_settings
        db.set_setting("report_sections", json.dumps(
            {k: cb.isChecked() for k, cb in self.chapter_checks.items()}))
        db.set_setting("report_save_dir", self.dir_edit.text().strip())
        db.set_setting("report_type", self.type_combo.currentText())

        # ⑥ 显示设置 -> app_settings，并即时应用到主窗口（布局自动重排）
        vis = {k: cb.isChecked() for k, cb in self.vis_checks.items()}
        db.set_setting("panel_visibility", json.dumps(vis))
        mw = self.parent()
        if hasattr(mw, "apply_visibility"):
            mw.apply_visibility(vis)

        # ⑦ 频率设置：信息域启停与倍率 -> app_settings
        db.set_setting("category_frequency", json.dumps({
            cat: {"enabled": self.cat_checks[cat].isChecked(),
                  "rate": float(self.rate_combos[cat].currentData())}
            for cat in self.cat_checks}))
        self.accept()
