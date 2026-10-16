"""单实例守卫（v2 增量 R5）：``QLocalServer`` / ``QLocalSocket``。

选型理由（ARCH-v2-increment §1.5）：Windows 下 ``QLocalServer`` 走**命名管道**，
生命周期由 OS 管理，进程崩溃后自动释放、无残留；且**原生支持**"第二实例激活
已有窗口"（连上管道 → 写 ``SHOW`` → 主实例 ``activated`` 信号抬窗），这是
``QSharedMemory`` 做不到的。

依赖 ``PySide6.QtNetwork``（打包 spec **未** exclude，无需改 spec）。
"""
from __future__ import annotations

import logging
import time

from PySide6.QtCore import QCoreApplication, QObject, QThread, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

log = logging.getLogger(__name__)

MSG_SHOW = b"SHOW"
CONNECT_TIMEOUT_MS = 300
NOTIFY_GRACE_S = 0.4
DEFAULT_KEY = "BankRiskMonitor"


class SingleInstanceGuard(QObject):
    """单实例守卫：探测/创建命名管道，收到 ``SHOW`` 时发射 ``activated``。

    典型用法（``main.py``）::

        guard = SingleInstanceGuard("BankRiskMonitor")
        if not guard.is_primary():
            guard.notify_existing()   # 激活已有窗口
            sys.exit(0)
        ...
        guard.activated.connect(win._on_instance_activated)
    """

    activated = Signal()

    def __init__(self, key: str = DEFAULT_KEY, parent=None):
        super().__init__(parent)
        self.key = key or DEFAULT_KEY
        self._server = None
        self._conns = set()

    # ---------- 对外接口 ----------
    def is_primary(self) -> bool:
        """尝试探测已有实例：连上 → ``False``（非主）；否则成为主实例（启动监听）。"""
        if self._probe_existing():
            return False
        return self._start_server()

    def notify_existing(self) -> bool:
        """连接已有实例并发送 ``SHOW`` 激活其窗口；成功返回 ``True``。

        发送后**短暂泵送事件循环**再断开：命名管道为流式，套接字握手尚未完成
        时立即 ``disconnectFromServer`` 会丢弃缓冲数据，导致主实例收不到 ``SHOW``。
        """
        socket = QLocalSocket()
        socket.connectToServer(self.key)
        if not socket.waitForConnected(CONNECT_TIMEOUT_MS):
            return False
        socket.write(MSG_SHOW)
        socket.flush()
        socket.waitForBytesWritten(CONNECT_TIMEOUT_MS)
        app = QCoreApplication.instance()
        if app is not None:
            deadline = time.monotonic() + NOTIFY_GRACE_S
            while time.monotonic() < deadline:
                app.processEvents()
                if (socket.state() == QLocalSocket.LocalSocketState.ConnectedState
                        and socket.bytesToWrite() == 0):
                    break
                QThread.msleep(5)
        socket.disconnectFromServer()
        return True

    def close(self):
        """释放命名管道监听与残留连接（进程退出/测试清理）。"""
        for conn in list(self._conns):
            self._drop(conn)
        if self._server is not None:
            try:
                self._server.close()
            finally:
                self._server = None
        QLocalServer.removeServer(self.key)

    # ---------- 内部实现 ----------
    def _probe_existing(self) -> bool:
        """仅探测是否存在主实例，不发送激活信号。"""
        socket = QLocalSocket()
        socket.connectToServer(self.key)
        ok = socket.waitForConnected(CONNECT_TIMEOUT_MS)
        socket.disconnectFromServer()
        return ok

    def _start_server(self) -> bool:
        server = QLocalServer(self)
        if not server.listen(self.key):
            # 极少数情况下残留命名管道导致 listen 失败：清理后重试一次
            QLocalServer.removeServer(self.key)
            if not server.listen(self.key):
                # 仍失败：不阻塞应用启动（宁可多开也不让用户打不开）
                log.warning("单实例监听失败（key=%s）：%s", self.key, server.errorString())
                return True
        server.newConnection.connect(self._on_new_connection)
        self._server = server
        return True

    def _on_new_connection(self):
        conn = self._server.nextPendingConnection() if self._server else None
        if conn is None:
            return
        self._conns.add(conn)
        conn.readyRead.connect(lambda c=conn: self._handle(c))
        conn.disconnected.connect(lambda c=conn: self._drop(c))
        if conn.bytesAvailable():
            self._handle(conn)

    def _handle(self, conn):
        """读取 ``SHOW`` 并发射 ``activated``（重复读取空数据不重复发射）。"""
        try:
            data = bytes(conn.readAll())
        except Exception:  # noqa: BLE001 - 连接已断开
            return
        if data:
            self.activated.emit()

    def _drop(self, conn):
        """关闭并从跟踪集合移除一个连接（避免悬空信号回调）。"""
        self._conns.discard(conn)
        try:
            conn.disconnectFromServer()
        except Exception:  # noqa: BLE001
            pass
        conn.deleteLater()
