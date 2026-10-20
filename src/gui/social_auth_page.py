"""社媒登录态管理：启动登录浏览器、读取登录态、粘贴 Cookie、清除、连通性自测。

用户可用「小号」在程序拉起的专用浏览器里登录抖音/快手/小红书/微博，
登录态长期保留在本机（专用浏览器配置目录），程序按需读取并驱动浏览器抓取。

另含 **OpenCLI 后端**配置：复用已登录 Chrome 做跨平台采集，
提供安装自检、平台清单勾选与启停开关；
以及 **公开站点渲染后端**配置：法院公告网等无需登录的站点
默认走 Obscura 轻量无头引擎（自动拉起、无窗口），可切回 Chrome。

提供两种用法：
- ``SocialAuthWidget``：可嵌入设置中心页签；
- ``SocialAuthDialog``：独立弹窗（主窗口工具栏直接进入）。
"""
import os

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QGroupBox,
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

from ..core import cdp, settings, social_auth
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

# OpenCLI 可选平台（按信贷风控相关性排序）
OPENCLI_PLATFORMS = [
    ("weibo", "微博"), ("xiaohongshu", "小红书"), ("weixin", "微信公众号"),
    ("douyin", "抖音"), ("zhihu", "知乎"), ("tieba", "百度贴吧"),
    ("hupu", "虎扑"), ("bilibili", "哔哩哔哩"), ("jike", "即刻"),
    ("36kr", "36氪"), ("linux-do", "LinuxDo"), ("nowcoder", "牛客网"),
    ("sinablog", "新浪博客"), ("douban", "豆瓣"), ("dianping", "大众点评"),
    ("pixiv", "Pixiv"), ("mubu", "幕布"), ("weread", "微信读书"),
    ("gov-policy", "政府政策"), ("gov-law", "法律法规"),
    ("powerchina", "电建集团"), ("twitter", "Twitter"), ("reddit", "Reddit"),
    ("youtube", "YouTube"), ("facebook", "Facebook"),
    ("instagram", "Instagram"), ("linkedin", "LinkedIn"), ("tiktok", "TikTok"),
    ("hackernews", "HackerNews"), ("zlibrary", "Z-Library"),
]


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
        lay.addWidget(self._build_opencli_box())
        lay.addWidget(self._build_backend_box())
        self._reload()
        setup_dialog_hover(self)

    # ---------- 公开站点渲染后端 ----------
    def _build_backend_box(self) -> QWidget:
        """公开站点渲染后端：Obscura 轻量无头引擎 / Chrome，可切换并一键自检启动。"""
        box = QGroupBox("公开站点渲染后端（法院公告网等无需登录的站点）", self)
        self.backend_combo = QComboBox(box)
        for key in (cdp.BACKEND_AUTO, cdp.BACKEND_OBSCURA, cdp.BACKEND_CHROME):
            self.backend_combo.addItem(cdp.BACKEND_LABELS[key], key)
        self.backend_status = QLabel("", box)
        self.backend_status.setProperty("secondary", True)
        self.backend_status.setWordWrap(True)

        check_btn = QPushButton("自检并启动", box)
        check_btn.clicked.connect(self._backend_check)
        save_btn = QPushButton("保存配置", box)
        save_btn.setProperty("primary", True)
        save_btn.clicked.connect(self._backend_save)

        btn_row = QHBoxLayout()
        btn_row.addWidget(QLabel("后端：", box))
        btn_row.addWidget(self.backend_combo)
        btn_row.addWidget(check_btn)
        btn_row.addWidget(save_btn)
        btn_row.addStretch(1)

        tip = QLabel(
            "公开站点（法院公告网、政务源）默认用 Obscura 轻量无头引擎渲染："
            "内存约 30MB、无窗口、程序按需自动拉起，不再需要手动开 Chrome。\n"
            "需登录的社媒（抖音/快手/小红书/微博）仍走上面的 Chrome —— "
            "无头引擎无法扫码登录，登录态只存在于 Chrome 里。", box)
        tip.setWordWrap(True)
        tip.setProperty("secondary", True)

        lay = QVBoxLayout(box)
        lay.addWidget(tip)
        lay.addLayout(btn_row)
        lay.addWidget(self.backend_status)
        self._load_backend_config()
        return box

    def _load_backend_config(self):
        """按 config.yaml 的 browser.backend 设置下拉项，并展示当前可用性。"""
        current = cdp.configured_backend()
        idx = self.backend_combo.findData(current)
        if idx >= 0:
            self.backend_combo.setCurrentIndex(idx)
        self._refresh_backend_status()

    def _refresh_backend_status(self):
        st = cdp.backend_status()
        obscura = "已就绪" if st["obscura_available"] else "未找到可执行文件"
        self.backend_status.setText(
            f"Obscura：{obscura}（{'运行中' if st['obscura_running'] else '未运行'}，"
            f"端口 {st['obscura_port']}）；"
            f"Chrome：{'运行中' if st['chrome_running'] else '未运行'}，"
            f"端口 {st['chrome_port']}")

    def _backend_save(self):
        """把后端选择写回 config.yaml。"""
        cfg = settings.config()
        browser = cfg.setdefault("browser", {})
        browser["backend"] = self.backend_combo.currentData()
        try:
            settings.save_config()
        except Exception as e:
            QMessageBox.warning(self, "保存失败", str(e))
            return
        self._refresh_backend_status()
        QMessageBox.information(
            self, "已保存",
            f"渲染后端已设为「{cdp.BACKEND_LABELS[browser['backend']]}」。")

    def _backend_check(self):
        """自检：缺引擎先按需下载（约 70MB），再启动后端并报告端口。"""
        self.backend_status.setText("正在自检（首次可能需要下载渲染引擎）…")
        self.setEnabled(False)
        self._worker = FnWorker(self._prepare_backend, parent=self)
        self._worker.done.connect(self._on_backend_check_done)
        self._worker.failed.connect(self._on_backend_check_fail)
        self._worker.start()

    @staticmethod
    def _prepare_backend() -> int:
        """确保 Obscura 就绪（缺失则下载），再返回可用的调试端口。"""
        ok, msg = cdp.ensure_obscura_installed()
        if not ok:
            raise RuntimeError(msg)
        return cdp.ensure_backend()

    def _on_backend_check_done(self, port):
        self.setEnabled(True)
        self._refresh_backend_status()
        QMessageBox.information(
            self, "渲染后端自检",
            f"后端可用，调试端口 {port}。法院公告网等公开站点可直接采集。")

    def _on_backend_check_fail(self, msg):
        self.setEnabled(True)
        self._refresh_backend_status()
        QMessageBox.warning(
            self, "渲染后端自检",
            f"未找到可用的渲染后端：{msg}\n\n"
            "可把后端切为「自动」后重试（会自动拉起 Obscura），"
            "或点上方「启动登录浏览器」后改用 Chrome。")

    # ---------- OpenCLI 后端 ----------
    def _build_opencli_box(self) -> QWidget:
        """OpenCLI 后端配置区：启停开关 + 平台清单 + 安装自检。"""
        box = QGroupBox("OpenCLI 后端（复用已登录 Chrome 跨平台采集）", self)
        self.opencli_enabled = QCheckBox("启用 OpenCLI 后端", box)
        self.opencli_status = QLabel("", box)
        self.opencli_status.setProperty("secondary", True)
        self.opencli_status.setWordWrap(True)

        self.opencli_checks = {}
        plat_lay = QHBoxLayout()
        for key, label in OPENCLI_PLATFORMS:
            cb = QCheckBox(label, box)
            self.opencli_checks[key] = cb
            plat_lay.addWidget(cb)
        plat_lay.addStretch(1)
        plat_wrap = QWidget(box)
        plat_wrap.setLayout(plat_lay)

        check_btn = QPushButton("安装自检", box)
        check_btn.clicked.connect(self._opencli_check)
        open_dir_btn = QPushButton("打开扩展目录", box)
        open_dir_btn.clicked.connect(self._opencli_ext_dir)
        save_btn = QPushButton("保存配置", box)
        save_btn.setProperty("primary", True)
        save_btn.clicked.connect(self._opencli_save)

        btn_row = QHBoxLayout()
        btn_row.addWidget(check_btn)
        btn_row.addWidget(open_dir_btn)
        btn_row.addWidget(save_btn)
        btn_row.addStretch(1)

        tip = QLabel(
            "前置条件：① npm install -g @jackwener/opencli；"
            "② Chrome 装 Browser Bridge 扩展（点「打开扩展目录」，"
            "在 chrome://extensions 开启开发者模式后「加载已解压的扩展程序」）；"
            "③ 在该 Chrome 里用小号登录目标平台。\n"
            "满足后 OpenCLI 会复用该浏览器的登录态抓取，"
            "与上方自研方案并存互备；未满足时该源自动跳过，不影响其它社媒源。", box)
        tip.setWordWrap(True)
        tip.setProperty("secondary", True)

        lay = QVBoxLayout(box)
        lay.addWidget(self.opencli_enabled)
        lay.addWidget(tip)
        lay.addWidget(plat_wrap)
        lay.addWidget(self.opencli_status)
        lay.addLayout(btn_row)
        self._load_opencli_config()
        return box

    def _opencli_spec(self) -> dict:
        """取 config.yaml 中 opencli 源的配置。"""
        for spec in settings.config().get("sources", {}).get("social", []) or []:
            if spec.get("name") == "opencli":
                return spec
        return {}

    def _load_opencli_config(self):
        spec = self._opencli_spec()
        params = spec.get("params") or {}
        self.opencli_enabled.setChecked(bool(spec.get("enabled")))
        chosen = set(params.get("platforms") or [])
        for key, cb in self.opencli_checks.items():
            cb.setChecked(key in chosen)

    def _opencli_save(self):
        """把启停与平台清单写回 config.yaml。"""
        cfg = settings.config()
        sources = cfg.setdefault("sources", {})
        social = sources.setdefault("social", [])
        spec = None
        for s in social:
            if s.get("name") == "opencli":
                spec = s
                break
        if spec is None:
            spec = {"name": "opencli", "enabled": False, "tier": 2,
                    "class": "providers.opencli_base.OpenCliProvider",
                    "params": {}}
            social.append(spec)
        spec["enabled"] = self.opencli_enabled.isChecked()
        params = spec.setdefault("params", {})
        params["platforms"] = [k for k, cb in self.opencli_checks.items() if cb.isChecked()]
        try:
            settings.save_config()
        except Exception as e:
            QMessageBox.warning(self, "保存失败", str(e))
            return
        self.opencli_status.setText(
            f"已保存：{'启用' if spec['enabled'] else '停用'}，"
            f"覆盖 {len(params['platforms'])} 个平台。")
        QMessageBox.information(self, "已保存", "OpenCLI 后端配置已写入 config.yaml。")

    def _opencli_check(self):
        """安装自检：跑 opencli doctor 并展示结果。"""
        self.opencli_status.setText("正在自检…")
        self.setEnabled(False)
        self._worker = FnWorker(self._opencli_doctor, parent=self)
        self._worker.done.connect(self._on_opencli_check_done)
        self._worker.failed.connect(self._on_opencli_check_fail)
        self._worker.start()

    @staticmethod
    def _opencli_doctor() -> dict:
        from ..providers.opencli_base import OpenCliProvider
        p = OpenCliProvider({})
        return p.doctor()

    def _on_opencli_check_done(self, result):
        self.setEnabled(True)
        result = result or {}
        installed = result.get("installed")
        connected = result.get("extension_connected")
        issues = result.get("issues") or []
        if not installed:
            text = "未安装 opencli CLI。请先执行：npm install -g @jackwener/opencli"
        elif not connected:
            text = ("opencli 已安装，但 Chrome 扩展未连接。"
                    "请点「打开扩展目录」加载 Browser Bridge 扩展后重试。")
        elif issues:
            text = "自检发现问题：\n" + "\n".join(str(i) for i in issues)
        else:
            text = "自检通过：opencli 已安装且浏览器扩展已连接，可以启用该后端。"
        self.opencli_status.setText(text)
        QMessageBox.information(self, "OpenCLI 安装自检", text)

    def _on_opencli_check_fail(self, msg):
        self.setEnabled(True)
        self.opencli_status.setText(f"自检失败：{msg}")
        QMessageBox.warning(self, "OpenCLI 安装自检", f"自检失败：{msg}")

    def _opencli_ext_dir(self):
        """打开扩展目录；不存在则先自动下载（第三方二进制包不入库）。"""
        from ..providers import opencli_base as ob
        path = ob.extension_dir()
        if not ob.extension_installed():
            self.opencli_status.setText("正在下载 OpenCLI 浏览器扩展…")
            self.setEnabled(False)
            self._worker = FnWorker(ob.download_extension, parent=self)
            self._worker.done.connect(self._on_ext_download_done)
            self._worker.failed.connect(self._on_ext_download_fail)
            self._worker.start()
            return
        self._reveal_ext_dir(path)

    def _reveal_ext_dir(self, path: str):
        try:
            os.startfile(path)  # noqa: S606 - Windows 打开资源管理器
        except OSError as e:
            QMessageBox.information(self, "扩展目录", f"{path}\n\n（{e}）")
            return
        QMessageBox.information(
            self, "加载扩展",
            f"已打开：{path}\n\n"
            "在 Chrome 地址栏打开 chrome://extensions → 开启右上角「开发者模式」"
            "→ 点「加载已解压的扩展程序」→ 选择上面的目录。")

    def _on_ext_download_done(self, result):
        self.setEnabled(True)
        ok, msg = result if isinstance(result, (tuple, list)) else (False, str(result))
        self.opencli_status.setText("扩展已下载" if ok else "扩展下载失败")
        if not ok:
            QMessageBox.warning(self, "扩展下载失败", msg)
            return
        from ..providers import opencli_base as ob
        self._reveal_ext_dir(ob.extension_dir())

    def _on_ext_download_fail(self, msg):
        self.setEnabled(True)
        self.opencli_status.setText("扩展下载失败")
        QMessageBox.warning(self, "扩展下载失败", msg)

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
