"""QA 独立验收 —— C. T01 原生地图（全新重写，独立验证"真的画出来了"）。

- offscreen 构造 ``MapView``，灌入 high/medium/low/none 四档光点，``resize(1200,800)``
  后 ``grab()`` 存 PNG 到 ``docs/qa/``；**独立统计非背景像素占比**（背景约 #0D1420）；
- 四级下钻（全国→省→市→区县）逐级切换不崩溃；
- ``has_children=False`` 的区域不下钻且有降级文案；
- 点击光点 emit ``spot_clicked``。
"""
import os
import unittest

from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QImage, QMouseEvent
from PySide6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])

from src.core.models import Event  # noqa: E402
from src.gui.map_engine import NO_CHILD_TEXT  # noqa: E402
from src.gui.map_view import MAP_BG, MapView  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
QA_DIR = os.path.join(ROOT, "docs", "qa")

_BG = (13, 20, 32)  # #0D1420


def _bg_rgb():
    r = int(MAP_BG[1:3], 16)
    g = int(MAP_BG[3:5], 16)
    b = int(MAP_BG[5:7], 16)
    return (r, g, b)


def _sample_spots():
    base = {"events": []}
    return [
        {"name": "高危企业", "province": "辽宁省", "city": "沈阳市", "lng": None, "lat": None,
         "event_count": 9, "severity": "high", "categories": ["lawsuit"], **base},
        {"name": "中危企业", "province": "浙江省", "city": "杭州市", "lng": None, "lat": None,
         "event_count": 5, "severity": "medium", "categories": ["biz_change"], **base},
        {"name": "低危企业", "province": "广东省", "city": "广州市", "lng": None, "lat": None,
         "event_count": 2, "severity": "low", "categories": ["news"], **base},
        {"name": "平稳企业", "province": "四川省", "city": "成都市", "lng": None, "lat": None,
         "event_count": 0, "severity": "none", "categories": [], **base},
    ]


class MapRenderQA(unittest.TestCase):
    def setUp(self):
        os.makedirs(QA_DIR, exist_ok=True)

    def test_renders_non_blank_png(self):
        mv = MapView()
        mv.resize(1200, 800)
        mv.show()
        mv.set_data(_sample_spots())
        for _ in range(5):
            _app.processEvents()
        pix = mv.grab()
        out = os.path.join(QA_DIR, "map_national_qa.png")
        self.assertTrue(pix.save(out), "PNG 保存失败")
        self.assertTrue(os.path.exists(out))

        # 独立像素统计（跳过采样，步长 3）
        img = pix.toImage()
        w, h = img.width(), img.height()
        self.assertEqual((w, h), (1200, 800))
        total = nonbg = 0
        for y in range(0, h, 3):
            for x in range(0, w, 3):
                c = img.pixelColor(x, y)
                total += 1
                if abs(c.red() - _BG[0]) + abs(c.green() - _BG[1]) + abs(c.blue() - _BG[2]) > 24:
                    nonbg += 1
        ratio = nonbg / max(1, total)
        with open(os.path.join(QA_DIR, "map_pixel_stats.txt"), "w", encoding="utf-8") as f:
            f.write(f"PNG={out}\n采样点数={total} 非背景={nonbg} 占比={ratio:.4%}\n")
        mv.close()
        mv.deleteLater()
        self.assertGreater(ratio, 0.02,
                           f"非背景像素占比仅 {ratio:.4%}（疑似空白/未绘制）PNG={out}")


class MapDrilldownQA(unittest.TestCase):
    def setUp(self):
        self.mv = MapView()
        self.mv.resize(1200, 800)
        self.mv.show()
        _app.processEvents()

    def tearDown(self):
        self.mv.close()
        self.mv.deleteLater()
        _app.processEvents()

    def _find(self, kw):
        return [f for f in self.mv._features if kw in f.name]

    def test_four_level_drilldown(self):
        self.assertEqual(self.mv._stack[-1]["level"], "national")
        ln = self._find("辽宁")
        self.assertTrue(ln, "全国层应含辽宁省")
        self.assertTrue(self.mv.drill(ln[0]), "全国→省 下钻失败")
        self.assertEqual(self.mv._stack[-1]["level"], "province")
        sy = self._find("沈阳")
        self.assertTrue(sy, "辽宁省层应含沈阳市")
        self.assertTrue(self.mv.drill(sy[0]), "省→市 下钻失败")
        self.assertEqual(self.mv._stack[-1]["level"], "district")
        self.assertGreater(len(self.mv._features), 0, "区县层应有边界数据")

    def test_no_children_region_degrades(self):
        zj = self._find("浙江")
        self.assertTrue(zj, "全国层应含浙江省")
        self.assertTrue(self.mv.drill(zj[0]))
        self.assertEqual(self.mv._stack[-1]["level"], "province")
        hz = self._find("杭州")
        self.assertTrue(hz, "浙江省层应含杭州市")
        # 杭州市无区县边界文件（has_children=False）→ 不下钻 + 降级文案
        self.assertFalse(self.mv._loader.has_children(hz[0].adcode))
        ok = self.mv.drill(hz[0])
        self.assertFalse(ok, "无下级数据的区域不得下钻")
        self.assertEqual(self.mv._stack[-1]["level"], "province", "层级不应变化")
        self.assertTrue(self.mv._msg.isVisible(), "应显示降级文案")
        self.assertEqual(self.mv._msg.text(), NO_CHILD_TEXT)


class MapSpotClickQA(unittest.TestCase):
    def setUp(self):
        self.mv = MapView()
        self.mv.resize(1200, 800)
        self.mv.show()
        _app.processEvents()

    def tearDown(self):
        self.mv.close()
        self.mv.deleteLater()
        _app.processEvents()

    def test_click_spot_emits_signal(self):
        self.mv.reset()
        self.mv.set_data(_sample_spots())
        _app.processEvents()
        captured = []
        self.mv.spot_clicked.connect(lambda n: captured.append(n))

        # 取第一个可见光点，计算其像素位置，合成鼠标按下+抬起
        sp = self.mv._visible_spots()[0]
        pt = self.mv._spot_px(sp, {})
        press = QMouseEvent(QEvent.Type.MouseButtonPress, pt, pt,
                            Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                            Qt.KeyboardModifier.NoModifier)
        release = QMouseEvent(QEvent.Type.MouseButtonRelease, pt, pt,
                              Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton,
                              Qt.KeyboardModifier.NoModifier)
        self.mv.mousePressEvent(press)
        self.mv.mouseReleaseEvent(release)
        self.assertIn(sp["name"], captured, f"点击光点应 emit spot_clicked，实际={captured}")


if __name__ == "__main__":
    unittest.main()
