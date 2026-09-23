"""MapView：内嵌 QWebEngineView 的中国地图，ECharts 光斑展示监控主体风险分布。

- 全部本地资源（file://），并拦截 http/https 请求，保证不产生网络访问。
- 通过 QWebChannel 将页面点击回传 Qt：spot_clicked(str enterprise_name)。
- echarts.min.js / china.json 缺失时降级加载 map_fallback.html 文本列表。
"""
import json
import os

from PySide6.QtCore import QObject, QUrl, Signal, Slot
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import (
    QWebEnginePage,
    QWebEngineProfile,
    QWebEngineSettings,
    QWebEngineUrlRequestInterceptor,
)
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QWidget, QVBoxLayout

from ..core import settings
from ..core.paths import asset_path

_BLOCKED_SCHEMES = ("http", "https", "ftp")


class _LocalOnlyInterceptor(QWebEngineUrlRequestInterceptor):
    """禁止页面发起任何远程网络请求。"""

    def interceptRequest(self, info):
        scheme = info.requestUrl().scheme().lower()
        if scheme in _BLOCKED_SCHEMES:
            info.block(True)


class _SpotBridge(QObject):
    """QWebChannel 桥：页面点击光斑 -> Qt 信号。"""

    spot_clicked = Signal(str)

    @Slot(str)
    def onSpotClick(self, name: str):
        self.spot_clicked.emit(name)


class MapView(QWidget):
    """中央地图面板。set_data(spots) 渲染；spots 见主窗口聚合逻辑。"""

    spot_clicked = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._loaded = False
        self._pending = None  # (spots, mode) 页面就绪后待渲染

        self.view = QWebEngineView(self)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.view)

        # 本地资源可用性：echarts + geojson 缺一即降级
        echarts_js = asset_path("echarts.min.js")
        china_json = asset_path("china.json")
        self._ok = bool(echarts_js and os.path.exists(echarts_js)
                        and china_json and os.path.exists(china_json))
        page_file = asset_path("map.html") if self._ok else asset_path("map_fallback.html")

        page = self.view.page()
        page.settings().setAttribute(
            QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)
        page.settings().setAttribute(
            QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, False)
        page.settings().setAttribute(
            QWebEngineSettings.WebAttribute.JavascriptEnabled, True)

        profile = page.profile()
        self._interceptor = _LocalOnlyInterceptor(profile)
        profile.setUrlRequestInterceptor(self._interceptor)

        self._bridge = _SpotBridge()
        self._bridge.spot_clicked.connect(self.spot_clicked)
        self._channel = QWebChannel(self)
        self._channel.registerObject("bridge", self._bridge)
        page.setWebChannel(self._channel)

        self.view.loadFinished.connect(self._on_load_finished)
        self.view.load(QUrl.fromLocalFile(page_file))

    def _on_load_finished(self, ok: bool):
        self._loaded = True
        if self._pending is not None:
            spots, mode = self._pending
            self._pending = None
            self._push(spots, mode)

    def _push(self, spots: list, mode: str):
        payload = json.dumps(spots, ensure_ascii=False).replace("</", "<\\/")
        js = "renderSpots({0}, {1});".format(
            json.dumps(payload, ensure_ascii=False), json.dumps(mode))
        self.view.page().runJavaScript(js)

    def set_data(self, spots: list, mode: str = None):
        """spots: [{name, province, event_count, severity, categories}]"""
        if not spots:
            spots = []
        if mode is None:
            mode = settings.config().get("ui", {}).get("map_mode", "severity")
            if mode not in ("severity", "category"):
                mode = "severity"
        if not self._loaded:
            self._pending = (spots, mode)
            return
        self._push(spots, mode)
