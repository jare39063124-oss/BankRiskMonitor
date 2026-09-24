"""设计 tokens 与全局 QSS：银行蓝浅色主题，微软雅黑 12px，统一 8px 栅格。"""

# ---------- 色彩 tokens ----------
PRIMARY = "#1B5EBA"          # 银行蓝（主色）
PRIMARY_DARK = "#134A94"
PRIMARY_HOVER = "#2E6FCE"
BG_WINDOW = "#F5F6F8"        # 窗口浅灰背景
BG_CARD = "#FFFFFF"          # 卡片白
BORDER = "#E0E3E7"           # 卡片/控件边框
TEXT_MAIN = "#202124"        # 主文字
TEXT_SECOND = "#5F6368"      # 次级文字
TEXT_DISABLE = "#9AA0A6"

SEVERITY_RED = "#D93026"     # 高危
SEVERITY_ORANGE = "#E8710A"  # 中危
SEVERITY_YELLOW = "#F9AB00"  # 关注
SEVERITY_BLUE = "#1A73E8"    # 低/正常

CATEGORY_COLORS = {
    "news": "#1A73E8",        # 舆情
    "biz_change": "#E8710A",  # 工商变更
    "lawsuit": "#F9AB00",     # 涉诉
    "enforcement": "#D93026", # 被执行/查封
    "social": "#8E44AD",      # 社媒
}

# 语义色背景（预警行）
BG_ALERT_HIGH = "#FCE8E6"
BG_ALERT_MEDIUM = "#FEF3E6"

FONT_FAMILY = "微软雅黑"
FONT_SIZE = 12              # 全局字号约 9pt
RADIUS = 4                  # 卡片圆角
SPACE = 8                   # 统一 8px 间距栅格


def get_app_qss() -> str:
    """返回全局样式表。卡片式 QMainWindow/QGroupBox、主/次按钮、表格、页签、输入控件、状态栏。"""
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
    color: {TEXT_MAIN};
    background: {BG_WINDOW};
}}

/* ---------- 按钮 ---------- */
QPushButton {{
    background: {BG_CARD};
    border: 1px solid {BORDER};
    border-radius: {RADIUS}px;
    padding: 5px 14px;
    color: {TEXT_MAIN};
}}
QPushButton:hover {{
    border-color: {PRIMARY};
    color: {PRIMARY};
}}
QPushButton:pressed {{
    background: #F0F4FA;
}}
QPushButton:disabled {{
    color: {TEXT_DISABLE};
    border-color: {BORDER};
    background: {BG_CARD};
}}
QPushButton[primary="true"] {{
    background: {PRIMARY};
    border: 1px solid {PRIMARY};
    color: #FFFFFF;
    font-weight: bold;
}}
QPushButton[primary="true"]:hover {{
    background: {PRIMARY_HOVER};
    border-color: {PRIMARY_HOVER};
    color: #FFFFFF;
}}
QPushButton[primary="true"]:pressed {{
    background: {PRIMARY_DARK};
}}
QPushButton[primary="true"]:disabled {{
    background: #A9C2E8;
    border-color: #A9C2E8;
    color: #FFFFFF;
}}
QPushButton[danger="true"] {{
    color: {SEVERITY_RED};
    border-color: #F2B8B5;
}}
QPushButton[danger="true"]:hover {{
    background: {BG_ALERT_HIGH};
    border-color: {SEVERITY_RED};
    color: {SEVERITY_RED};
}}

/* ---------- 输入控件 ---------- */
QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QDoubleSpinBox, QDateEdit, QTimeEdit {{
    background: {BG_CARD};
    border: 1px solid {BORDER};
    border-radius: {RADIUS}px;
    padding: 4px 6px;
    selection-background-color: {PRIMARY};
    selection-color: #FFFFFF;
}}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus,
QSpinBox:focus, QDoubleSpinBox:focus, QDateEdit:focus, QTimeEdit:focus {{
    border-color: {PRIMARY};
}}
QLineEdit:disabled, QPlainTextEdit:disabled {{
    background: {BG_WINDOW};
    color: {TEXT_DISABLE};
}}
QComboBox {{
    background: {BG_CARD};
    border: 1px solid {BORDER};
    border-radius: {RADIUS}px;
    padding: 4px 24px 4px 8px;
}}
QComboBox:hover {{
    border-color: {PRIMARY};
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
    selection-background-color: #EAF1FB;
    selection-color: {TEXT_MAIN};
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
    background: {BG_CARD};
}}
QRadioButton::indicator {{
    border-radius: 7px;
}}
QCheckBox::indicator:checked, QTreeWidget::indicator:checked, QListWidget::indicator:checked {{
    background: {PRIMARY};
    border-color: {PRIMARY};
    image: none;
}}

/* ---------- 列表 / 表格 / 树 ---------- */
QListWidget, QTreeWidget, QTableView, QListView {{
    background: {BG_CARD};
    border: 1px solid {BORDER};
    border-radius: {RADIUS}px;
    alternate-background-color: #FAFBFC;
    selection-background-color: #EAF1FB;
    selection-color: {TEXT_MAIN};
    padding: 2px;
}}
QListWidget::item, QTreeWidget::item, QListView::item {{
    padding: 4px 6px;
    border-radius: 3px;
}}
QTableView {{
    gridline-color: {BORDER};
}}
QTableView::item {{
    padding: 2px 6px;
}}
QHeaderView::section {{
    background: {BG_WINDOW};
    color: {TEXT_SECOND};
    border: none;
    border-bottom: 1px solid {BORDER};
    border-right: 1px solid {BORDER};
    padding: 5px 8px;
    font-weight: bold;
}}
QTableCornerButton::section {{
    background: {BG_WINDOW};
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
    background: transparent;
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
    color: {PRIMARY};
    font-weight: bold;
}}
QTabBar::tab:hover:!selected {{
    color: {PRIMARY};
}}

/* ---------- 滚动条 / 分割线 ---------- */
QScrollBar:vertical {{
    background: transparent;
    width: 8px;
    margin: 0;
}}
QScrollBar::handle:vertical {{
    background: #C7CBD1;
    border-radius: 4px;
    min-height: 30px;
}}
QScrollBar::handle:vertical:hover {{
    background: {TEXT_DISABLE};
}}
QScrollBar:horizontal {{
    background: transparent;
    height: 8px;
    margin: 0;
}}
QScrollBar::handle:horizontal {{
    background: #C7CBD1;
    border-radius: 4px;
    min-width: 30px;
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
    background: #EAF1FB;
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
    background: transparent;
    border: 1px solid transparent;
    border-radius: {RADIUS}px;
    padding: 4px 10px;
    color: {TEXT_MAIN};
}}
QToolButton:hover {{
    background: #EAF1FB;
    border-color: {BORDER};
    color: {PRIMARY};
}}

/* ---------- 进度条 / 提示 ---------- */
QProgressBar {{
    background: {BG_WINDOW};
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
    background: {TEXT_MAIN};
    color: #FFFFFF;
    border: none;
    padding: 4px 8px;
    border-radius: {RADIUS}px;
}}
QLabel[secondary="true"] {{
    color: {TEXT_SECOND};
}}
QLabel[h1="true"] {{
    font-size: 16px;
    font-weight: bold;
}}
QLabel[h2="true"] {{
    font-size: 14px;
    font-weight: bold;
}}
"""
