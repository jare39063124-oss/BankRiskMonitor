"""设计 tokens 与全局 QSS：黑色科技风监测大屏主题。

- 黑底 #0A0E14 + 荧光绿 #00FF9C 主色，对标全球情报监测大屏
- 硬性要求：所有可点击控件常态有边框/底色、hover 提亮、pressed 反转，
  与背景明确区分；正文对比度 >= 4.5:1
- 微软雅黑 12px，统一 8px 栅格
"""

# ---------- 色彩 tokens ----------
PRIMARY = "#00FF9C"          # 荧光绿（主色/accent）
PRIMARY_DIM = "#17C47E"      # hover 提亮前的基础绿
PRIMARY_PRESSED = "#00E88B"
PRIMARY_BG = "#0F241B"       # 主按钮深绿底
PRIMARY_GLOW = "rgba(0,255,156,0.35)"

BG_WINDOW = "#0A0E14"        # 窗口近黑背景
BG_CARD = "#10161F"          # 卡片深灰蓝
BG_INPUT = "#161C26"         # 输入控件/按钮底色
BG_HOVER = "#1B2330"         # 悬停底色
BORDER = "#2A3441"           # 常态控件边框（与背景明确区分）
BORDER_BRIGHT = "#00FF9C"    # 悬停边框（荧光绿）

TEXT_MAIN = "#E6EDF3"        # 主文字（黑底对比度 ~14:1）
TEXT_SECOND = "#8B98A9"      # 次级文字
TEXT_DISABLE = "#5A6572"

SEVERITY_RED = "#FF4D4D"     # 高危（深底提亮红）
SEVERITY_ORANGE = "#FFA033"  # 中危
SEVERITY_YELLOW = "#FFD84D"  # 关注
SEVERITY_BLUE = "#4DA3FF"    # 低/正常
SEVERITY_GREEN = "#00FF9C"   # 平稳（与主色一致）

CATEGORY_COLORS = {
    "news": "#4DA3FF",        # 舆情
    "biz_change": "#FFA033",  # 工商变更
    "lawsuit": "#FFD84D",     # 涉诉
    "enforcement": "#FF4D4D", # 被执行/查封
    "social": "#C77DFF",      # 社媒
}

# 语义色背景（预警行）：深色底上的暗红/暗橙
BG_ALERT_HIGH = "#2A1215"
BG_ALERT_MEDIUM = "#2A1D0E"

FONT_FAMILY = "微软雅黑"
FONT_SIZE = 12              # 全局字号约 9pt
RADIUS = 4                  # 卡片圆角
SPACE = 8                   # 统一 8px 间距栅格


def apply_palette(app):
    """设置应用级深色调色板。

    QSS 的 color 属性对 item view 条目文本（走 QPalette.Text 角色）不生效，
    必须在调色板层显式指定，否则列表/表格未选中条目文字会落回系统默认灰暗色。
    """
    from PySide6.QtGui import QColor, QPalette

    def _role(group, color, *roles):
        for r in roles:
            pal.setColor(group, r, QColor(color))

    pal = app.palette()
    active = QPalette.ColorGroup.Active
    disabled = QPalette.ColorGroup.Disabled
    R = QPalette.ColorRole
    _role(active, TEXT_MAIN, R.WindowText, R.Text, R.ButtonText, R.BrightText)
    _role(active, BG_WINDOW, R.Window)
    _role(active, BG_CARD, R.Base)
    _role(active, "#0D131C", R.AlternateBase)
    _role(active, BG_INPUT, R.Button)
    _role(active, PRIMARY_BG, R.Highlight)
    _role(active, PRIMARY, R.HighlightedText)
    _role(active, BG_CARD, R.ToolTipBase)
    _role(active, PRIMARY, R.ToolTipText)
    _role(active, TEXT_DISABLE, R.PlaceholderText)
    _role(disabled, TEXT_DISABLE, R.WindowText, R.Text, R.ButtonText)
    app.setPalette(pal)


def get_app_qss() -> str:
    """返回全局样式表。黑色卡片式 QMainWindow/QGroupBox、三态按钮、表格、页签、输入控件、状态栏。"""
    return f"""
* {{
    font-family: "{FONT_FAMILY}";
    font-size: {FONT_SIZE}px;
    color: {TEXT_MAIN};
    outline: none;
}}
QMainWindow, QDialog {{
    background: {BG_WINDOW};
}}
QWidget {{
    background: transparent;
}}
QStatusBar {{
    background: {BG_CARD};
    border-top: 1px solid {BORDER};
    color: {TEXT_SECOND};
}}
QStatusBar QLabel {{
    color: {TEXT_SECOND};
    padding: 0 8px;
}}

/* ---------- 卡片容器 ---------- */
QGroupBox {{
    background: {BG_CARD};
    border: 1px solid {BORDER};
    border-radius: {RADIUS}px;
    margin-top: 12px;
    padding: 8px;
    font-weight: bold;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 12px;
    top: 0px;
    padding: 0 4px;
    color: {PRIMARY};
    background: {BG_WINDOW};
}}

/* ---------- 按钮三态（硬性要求：常态可辨/hover 提亮/pressed 反转） ---------- */
QPushButton {{
    background: {BG_INPUT};
    border: 1px solid {BORDER};
    border-radius: {RADIUS}px;
    padding: 5px 14px;
    color: {TEXT_MAIN};
}}
QPushButton:hover {{
    border-color: {BORDER_BRIGHT};
    color: {BORDER_BRIGHT};
    background: {BG_HOVER};
}}
QPushButton:pressed {{
    background: {PRIMARY};
    border-color: {PRIMARY};
    color: {BG_WINDOW};
}}
QPushButton:disabled {{
    color: {TEXT_DISABLE};
    border-color: {BORDER};
    background: {BG_CARD};
}}
QPushButton[primary="true"] {{
    background: {PRIMARY_BG};
    border: 1px solid {PRIMARY};
    color: {PRIMARY};
    font-weight: bold;
}}
QPushButton[primary="true"]:hover {{
    background: {BG_HOVER};
    border-color: {PRIMARY};
    color: #FFFFFF;
}}
QPushButton[primary="true"]:pressed {{
    background: {PRIMARY};
    border-color: {PRIMARY};
    color: {BG_WINDOW};
}}
QPushButton[primary="true"]:disabled {{
    background: {BG_CARD};
    border-color: {BORDER};
    color: {TEXT_DISABLE};
}}
QPushButton[danger="true"] {{
    color: {SEVERITY_RED};
    border-color: #6B2B2B;
    background: {BG_INPUT};
}}
QPushButton[danger="true"]:hover {{
    background: {BG_ALERT_HIGH};
    border-color: {SEVERITY_RED};
    color: {SEVERITY_RED};
}}
QPushButton[danger="true"]:pressed {{
    background: {SEVERITY_RED};
    border-color: {SEVERITY_RED};
    color: {BG_WINDOW};
}}

/* ---------- 输入控件 ---------- */
QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QDoubleSpinBox, QDateEdit, QTimeEdit {{
    background: {BG_INPUT};
    color: {TEXT_MAIN};
    border: 1px solid {BORDER};
    border-radius: {RADIUS}px;
    padding: 4px 6px;
    selection-background-color: {PRIMARY};
    selection-color: {BG_WINDOW};
}}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus,
QSpinBox:focus, QDoubleSpinBox:focus, QDateEdit:focus, QTimeEdit:focus {{
    border-color: {PRIMARY};
}}
QLineEdit:disabled, QPlainTextEdit:disabled {{
    background: {BG_CARD};
    color: {TEXT_DISABLE};
}}
QComboBox {{
    background: {BG_INPUT};
    border: 1px solid {BORDER};
    border-radius: {RADIUS}px;
    padding: 4px 24px 4px 8px;
}}
QComboBox:hover {{
    border-color: {BORDER_BRIGHT};
}}
QComboBox:pressed {{
    background: {BG_HOVER};
}}
QComboBox::drop-down {{
    border: none;
    width: 20px;
}}
QComboBox::down-arrow {{
    image: none;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 5px solid {TEXT_SECOND};
    margin-right: 6px;
}}
QComboBox QAbstractItemView {{
    background: {BG_CARD};
    border: 1px solid {BORDER};
    border-radius: {RADIUS}px;
    selection-background-color: {PRIMARY_BG};
    selection-color: {PRIMARY};
}}
QCheckBox, QRadioButton {{
    spacing: 6px;
    background: transparent;
}}
QCheckBox::indicator, QRadioButton::indicator, QTreeWidget::indicator, QListWidget::indicator {{
    width: 14px;
    height: 14px;
    border: 1px solid {BORDER};
    border-radius: 3px;
    background: {BG_INPUT};
}}
QRadioButton::indicator {{
    border-radius: 7px;
}}
QCheckBox::indicator:checked, QTreeWidget::indicator:checked, QListWidget::indicator:checked {{
    background: {PRIMARY};
    border-color: {PRIMARY};
    image: none;
}}
QCheckBox:hover, QRadioButton:hover {{
    color: {PRIMARY};
}}

/* ---------- 列表 / 表格 / 树 ---------- */
/* 注意：item 文本走 QPalette.Text 角色，全局 * 选择器不生效，必须在此显式声明亮色 */
QListWidget, QTreeWidget, QTableView, QListView {{
    background: {BG_CARD};
    color: {TEXT_MAIN};
    border: 1px solid {BORDER};
    border-radius: {RADIUS}px;
    alternate-background-color: #0D131C;
    selection-background-color: {PRIMARY_BG};
    selection-color: {PRIMARY};
    padding: 2px;
}}
QListWidget::item, QTreeWidget::item, QListView::item {{
    padding: 4px 6px;
    border-radius: 3px;
}}
QListWidget::item:hover, QTreeWidget::item:hover, QListView::item:hover {{
    background: {BG_HOVER};
}}
QTableView {{
    gridline-color: {BORDER};
}}
QTableView::item {{
    padding: 2px 6px;
}}
QHeaderView::section {{
    background: {BG_INPUT};
    color: {TEXT_SECOND};
    border: none;
    border-bottom: 1px solid {BORDER};
    border-right: 1px solid {BORDER};
    padding: 5px 8px;
    font-weight: bold;
}}
QTableCornerButton::section {{
    background: {BG_INPUT};
    border: none;
}}

/* ---------- 页签 ---------- */
QTabWidget::pane {{
    background: {BG_CARD};
    border: 1px solid {BORDER};
    border-radius: {RADIUS}px;
    top: -1px;
}}
QTabBar::tab {{
    background: {BG_CARD};
    color: {TEXT_SECOND};
    padding: 6px 16px;
    margin-right: 2px;
    border: 1px solid transparent;
    border-bottom: none;
    border-top-left-radius: {RADIUS}px;
    border-top-right-radius: {RADIUS}px;
}}
QTabBar::tab:selected {{
    background: {BG_CARD};
    border-color: {BORDER};
    border-top: 2px solid {PRIMARY};
    color: {PRIMARY};
    font-weight: bold;
}}
QTabBar::tab:hover:!selected {{
    color: {PRIMARY};
    background: {BG_HOVER};
}}

/* ---------- 滚动条 / 分割线 ---------- */
QScrollBar:vertical {{
    background: transparent;
    width: 8px;
    margin: 0;
}}
QScrollBar::handle:vertical {{
    background: #2E3A48;
    border-radius: 4px;
    min-height: 30px;
}}
QScrollBar::handle:vertical:hover {{
    background: {PRIMARY_DIM};
}}
QScrollBar:horizontal {{
    background: transparent;
    height: 8px;
    margin: 0;
}}
QScrollBar::handle:horizontal {{
    background: #2E3A48;
    border-radius: 4px;
    min-width: 30px;
}}
QScrollBar::handle:horizontal:hover {{
    background: {PRIMARY_DIM};
}}
QScrollBar::add-line, QScrollBar::sub-line {{
    width: 0;
    height: 0;
}}
QScrollBar::add-page, QScrollBar::sub-page {{
    background: transparent;
}}
QSplitter::handle {{
    background: {BG_WINDOW};
}}
QSplitter::handle:horizontal {{
    width: 3px;
}}
QSplitter::handle:vertical {{
    height: 3px;
}}
QFrame[lineWidth="1"] {{
    color: {BORDER};
}}

/* ---------- 菜单 / 工具栏 ---------- */
QMenuBar {{
    background: {BG_CARD};
    border-bottom: 1px solid {BORDER};
}}
QMenu {{
    background: {BG_CARD};
    border: 1px solid {BORDER};
    border-radius: {RADIUS}px;
    padding: 4px;
}}
QMenu::item {{
    padding: 5px 24px 5px 12px;
    border-radius: 3px;
}}
QMenu::item:selected {{
    background: {PRIMARY_BG};
    color: {PRIMARY};
}}
QToolBar {{
    background: {BG_CARD};
    border-bottom: 1px solid {BORDER};
    padding: 4px 8px;
    spacing: {SPACE}px;
}}
QToolBar::separator {{
    width: 1px;
    background: {BORDER};
    margin: 4px 2px;
}}
QToolButton {{
    background: {BG_INPUT};
    border: 1px solid {BORDER};
    border-radius: {RADIUS}px;
    padding: 4px 10px;
    color: {TEXT_MAIN};
}}
QToolButton:hover {{
    background: {BG_HOVER};
    border-color: {BORDER_BRIGHT};
    color: {BORDER_BRIGHT};
}}
QToolButton:pressed {{
    background: {PRIMARY};
    border-color: {PRIMARY};
    color: {BG_WINDOW};
}}

/* ---------- 进度条 / 提示 ---------- */
QProgressBar {{
    background: {BG_INPUT};
    border: 1px solid {BORDER};
    border-radius: {RADIUS}px;
    text-align: center;
    color: {TEXT_SECOND};
    height: 14px;
}}
QProgressBar::chunk {{
    background: {PRIMARY};
    border-radius: {RADIUS - 1}px;
}}
QToolTip {{
    background: {BG_CARD};
    color: {PRIMARY};
    border: 1px solid {BORDER_BRIGHT};
    padding: 4px 8px;
    border-radius: {RADIUS}px;
}}
QLabel[secondary="true"] {{
    color: {TEXT_SECOND};
}}
QLabel[h1="true"] {{
    font-size: 16px;
    font-weight: bold;
    color: {TEXT_MAIN};
}}
QLabel[h2="true"] {{
    font-size: 14px;
    font-weight: bold;
    color: {PRIMARY};
}}
"""
