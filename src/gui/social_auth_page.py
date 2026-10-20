"""社媒登录态管理：启动登录浏览器、读取登录态、粘贴 Cookie、清除、连通性自测。

用户可用「小号」在程序拉起的专用浏览器里登录抖音/快手/小红书/微博，
登录态长期保留在本机（专用浏览器配置目录），程序按需读取并驱动浏览器抓取。

提供两种用法：
- ``SocialAuthWidget``：可嵌入设置中心页签；
- ``SocialAuthDialog``：独立弹窗（主窗口工具栏直接进入）。
"""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..core import social_auth
from .hover_effect import setup_dialog_hover
from .workers import FnWorker

# 站点顺序
SITE_ORDER = ["weibo", "douyin", "kuaishou", "xiaohongshu", "wechat_sogou"]

SITE_HINTS = {
    "weibo": "s.weibo.com 搜索页",
    "douyin": "抖音搜索页",
    "kuaishou": "快手搜索页",
    "xiaohongshu": "小红书搜索页",
    "wechat_sogou": "搜狗微信搜索（无需登录）",
}


class SocialAuthWidget(QWidget):
    """社媒登录态管理面板。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._worker = None

        self.tree = QTreeWidget(self)
        self.tree.setHeaderLabels(["站点", "状态", "登录态摘要", "更新时间"])
        self.tree.setRootIsDecorated(False)
        self.tree.setColumnWidth(0, 110)
        self.tree.setColumnWidth(1, 90)
        self.tree.setColumnWidth(2, 300)
        self.tree.itemDoubleClicked.connect(self._on_double_click)

        self.status_label = QLabel(self)
        self.status_label.setProperty("secondary", True)
        self.status_label.setWordWrap(True)

        launch_btn = QPushButton("启动登录浏览器", self)
        launch_btn.setProperty("primary", True)
        launch_btn.clicked.connect(self._launch)

        read_btn = QPushButton("读取登录态", self)
        read_btn.clicked.connect(self._read_all)

        paste_btn = QPushButton("粘贴 Cookie…", self)
        paste_btn.clicked.connect(self._paste)

        clear_btn = QPushButton("清除所选", self)
        clear_btn.clicked.connect(self._clear)

        test_btn = QPushButton("连通性自测", self)
        test_btn.clicked.connect(self._test)

        btns = QHBoxLayout()
        btns.addWidget(launch_btn)
        btns.addWidget(read_btn)
        btns.addWidget(paste_btn)
        btns.addWidget(clear_btn)
        btns.addWidget(test_btn)
        btns.addStretch(1)

        guide = QLabel(
            "使用步骤：\n"
            "1) 点「启动登录浏览器」，程序会拉起一个独立 Chrome 窗口"
            "（配置目录 data/browser_profile，登录态长期保留）；\n"
            "2) 在该窗口里用小号登录抖音/快手/小红书/微博"
            "（登录哪个就登哪个，可只登用得到的）；\n"
            "3) 回到本窗口点「读取登录态」，程序自动把各站点的 Cookie 取回并保存；\n"
            "4) 之后刷新监控时，社媒源会**驱动这个已登录的浏览器**去抓搜索结果"
            "（仅在需要时访问）。\n"
            "若不方便开浏览器，也可点「粘贴 Cookie…」手动填入。", self)
        guide.setWordWrap(True)
        guide.setProperty("secondary", True)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(guide)
        lay.addWidget(self.tree, 1)
        lay.addWidget(self.status_label)
        lay.addLayout(btns)
        self._reload()
        setup_dialog_hover(self)

    # ---------- 刷新列表 ----------
    def _reload(self):
        self.tree.clear()
        for site in SITE_ORDER:
            st = social_auth.status(site)
            item = QTreeWidgetItem([
                st["label"],
                "已配置" if st["configured"] else "未配置",
                st["masked"] or "—",
                st["updated_at"] or "—",
            ])
            item.setData(0, Qt.ItemDataRole.UserRole, site)
            self.tree.addTopLevelItem(item)
        summary = social_auth.auth_summary()
        browser = "运行中" if summary["browser"] else "未启动"
        self.status_label.setText(
            f"调试浏览器：{browser}（端口 {summary['port']}）　"
            f"已配置登录态 {summary['configured']}/{summary['total']} 个站点")

    def _current_site(self) -> str:
        item = self.tree.currentItem()
        if not item:
            return ""
        return str(item.data(0, Qt.ItemDataRole.UserRole) or "")

    def _on_double_click(self, item, col):
        site = str(item.data(0, Qt.ItemDataRole.UserRole) or "")
        if site:
            self._paste(site)

    # ---------- 动作 ----------
    def _launch(self):
        port = social_auth.get_cdp_port()
        ok, msg = social_auth.launch_login_browser(port)
        if not ok:
            QMessageBox.warning(self, "启动失败", msg)
            return
        self._reload()
        QMessageBox.information(
            self, "已启动登录浏览器",
            msg + "\n\n请在该浏览器窗口登录小号，登录完成后回到本窗口点「读取登录态」。")

    def _read_all(self):
        if not social_auth.browser_running():
            QMessageBox.warning(
                self, "调试浏览器未运行",
                "请先点「启动登录浏览器」，在弹出的窗口登录小号后再点「读取登录态」。")
            return
        self.status_label.setText("正在读取登录态…")
        self.setEnabled(False)
        self._worker = FnWorker(social_auth.import_all_from_chrome, parent=self)
        self._worker.done.connect(self._on_read_done)
        self._worker.failed.connect(self._on_read_fail)
        self._worker.start()

    def _on_read_done(self, result):
        self.setEnabled(True)
        result = result or {}
        ok_list = [f"{social_auth.SITE_LABELS.get(s, s)}：已读取"
                   for s, (ok, _) in result.items() if ok]
        fail_list = [f"{social_auth.SITE_LABELS.get(s, s)}：{msg}"
                     for s, (ok, msg) in result.items() if not ok]
        self._reload()
        text = "读取完成。\n\n" + ("成功：\n" + "\n".join(ok_list) if ok_list else "")
        if fail_list:
            text += "\n\n未获取：\n" + "\n".join(fail_list)
        QMessageBox.information(self, "读取登录态", text)

    def _on_read_fail(self, msg):
        self.setEnabled(True)
        QMessageBox.warning(self, "读取失败", msg)

    def _paste(self, site=None):
        site = site or self._current_site()
        if not site:
            QMessageBox.information(self, "提示", "请先选择一个站点")
            return
        label = social_auth.SITE_LABELS.get(site, site)
        dlg = QDialog(self)
        dlg.setWindowTitle(f"粘贴 {label} 登录态")
        dlg.setMinimumSize(560, 320)
        edit = QPlainTextEdit(dlg)
        edit.setPlaceholderText(
            "在浏览器 F12 → Network → 任意请求 → Request Headers → Cookie，整行复制粘贴到这里。\n"
            "也支持 a=1; b=2 或 JSON 格式。")
        cur = social_auth.get_cookie(site)
        if cur:
            edit.setPlainText(cur)
        btn_ok = QPushButton("保存", dlg)
        btn_ok.setProperty("primary", True)
        btn_ok.clicked.connect(dlg.accept)
        btn_cancel = QPushButton("取消", dlg)
        btn_cancel.clicked.connect(dlg.reject)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(btn_cancel)
        row.addWidget(btn_ok)
        lay = QVBoxLayout(dlg)
        lay.addWidget(QLabel(f"{label}（{SITE_HINTS.get(site, '')}）", dlg))
        lay.addWidget(edit, 1)
        lay.addLayout(row)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        raw = edit.toPlainText().strip()
        if not raw:
            return
        try:
            social_auth.set_cookie(site, raw)
        except RuntimeError as e:
            QMessageBox.warning(self, "保存失败", str(e))
            return
        self._reload()

    def _clear(self):
        site = self._current_site()
        if not site:
            QMessageBox.information(self, "提示", "请先选择一个站点")
            return
        label = social_auth.SITE_LABELS.get(site, site)
        if QMessageBox.question(
                self, "确认清除", f"确定要清除 {label} 的登录态吗？") != \
                QMessageBox.StandardButton.Yes:
            return
        social_auth.clear(site)
        self._reload()

    def _test(self):
        """连通性自测：对该站点做一次真实抓取探测。"""
        site = self._current_site()
        if not social_auth.browser_running():
            QMessageBox.information(
                self, "调试浏览器未运行",
                "未检测到调试浏览器。社媒抓取需要在已登录的浏览器中进行，"
                "请先点「启动登录浏览器」。")
            return
        if not site:
            QMessageBox.information(self, "提示", "请先选择一个站点")
            return
        if not social_auth.get_cookie(site):
            QMessageBox.information(
                self, "未配置登录态",
                f"{social_auth.SITE_LABELS.get(site, site)} 尚未配置登录态，"
                "请先点「读取登录态」或手动粘贴。")
            return
        self.status_label.setText(f"正在自测 {site} …")
        self.setEnabled(False)
        self._worker = FnWorker(self._probe, site, parent=self)
        self._worker.done.connect(self._on_test_done)
        self._worker.failed.connect(self._on_test_fail)
        self._worker.start()

    @staticmethod
    def _probe(site):
        """对单个站点做一次真实抓取，返回 (条数, 说明)。"""
        from ..core.models import Enterprise
        from ..providers import base as pb
        spec = None
        for s in pb.settings.config().get("sources", {}).get("social", []):
            if s.get("name") == site:
                spec = s
                break
        if not spec:
            return -1, f"配置中未找到源 {site}"
        params = dict(spec.get("params") or {})
        if spec.get("requires_auth"):
            params["requires_auth"] = True
        mod_path, cls_name = str(spec.get("class", "")).rsplit(".", 1)
        import importlib
        try:
            mod = importlib.import_module(mod_path)
        except ImportError:
            mod = importlib.import_module("src." + mod_path)
        provider = getattr(mod, cls_name)(params)
        try:
            events = provider.fetch(Enterprise(name="测试企业"))
        except Exception as e:
            return -1, f"抓取失败：{e}"
        return len(events), f"抓取成功，取到 {len(events)} 条"

    def _on_test_done(self, result):
        self.setEnabled(True)
        count, msg = result if isinstance(result, (tuple, list)) else (-1, str(result))
        self._reload()
        if count > 0:
            QMessageBox.information(self, "连通性自测", msg)
        elif count == 0:
            QMessageBox.information(
                self, "连通性自测", msg + "（该测试企业名通常搜不到内容，属正常）")
        else:
            QMessageBox.warning(self, "连通性自测", msg)

    def _on_test_fail(self, msg):
        self.setEnabled(True)
        QMessageBox.warning(self, "自测异常", msg)


class SocialAuthDialog(QDialog):
    """社媒登录态独立弹窗。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("社媒登录态")
        self.setMinimumSize(720, 520)
        self.view = SocialAuthWidget(self)
        lay = QVBoxLayout(self)
        lay.addWidget(self.view)
        setup_dialog_hover(self)
