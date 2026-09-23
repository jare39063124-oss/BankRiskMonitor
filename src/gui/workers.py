"""通用后台线程：在线程中执行任意函数，结果/异常经信号回传 UI。"""
from PySide6.QtCore import QThread, Signal


class FnWorker(QThread):
    done = Signal(object)      # 函数返回值
    failed = Signal(str)       # 异常信息

    def __init__(self, fn, *args, parent=None):
        super().__init__(parent)
        self._fn = fn
        self._args = args

    def run(self):
        try:
            self.done.emit(self._fn(*self._args))
        except Exception as e:  # 后台线程异常统一回传，不在子线程弹窗
            self.failed.emit(str(e))
