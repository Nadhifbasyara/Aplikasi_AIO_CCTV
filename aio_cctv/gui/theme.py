"""Tema abu-abu gelap seluruh aplikasi desktop (sidebar, halaman, dialog, grafik).

Semua warna antarmuka diambil dari token di sini, supaya tampilan tetap seragam.
"""
from pathlib import Path

from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtWidgets import QApplication

# Abu-abu gelap netral, diambil dari tampilan Qt bawaan di Ubuntu Yaru-dark (masukan pengguna)
BG = "#2A2A2A"            # latar halaman, sidebar, status bar
SURFACE = "#303030"       # panel (group box)
SURFACE_2 = "#3A3A3A"     # tombol, kepala tabel, tab, hover
INPUT = "#232323"         # kolom isian, tabel, daftar
BORDER = "#1E1E1E"
BORDER_STRONG = "#555555"
TEXT = "#F2F2F2"
STRONG = "#FFFFFF"        # teks di atas pilihan / item aktif
MUTED = "#A8A8A8"
DISABLED = "#6E6E6E"
ACCENT = "#BDBDBD"        # abu-abu terang: garis item aktif, fokus
ACCENT_SOFT = "#474747"   # latar item terpilih
CHECKED = "#707070"       # latar kotak centang / radio yang dipilih
SIDEBAR = "#2A2A2A"
SIDEBAR_TEXT = "#E0E0E0"
SIDEBAR_HOVER = "#353535"
SIDEBAR_ACTIVE = "#404040"
INDICATOR = "#8A8A8A"     # tepi kotak centang / radio
OK = "#66BB6A"
WARN = "#FFA726"
BAD = "#EF5350"
PRIMARY = "#1E88E5"       # tombol aksi utama (mis. "+ Tambah Kamera") & sakelar aktif
# label berwarna (pill): (teks, latar transparan)
PILLS = {"blue": ("#64B5F6", "rgba(30,136,229,0.18)"), "orange": ("#FFB74D", "rgba(251,140,0,0.18)"),
         "teal": ("#4DB6AC", "rgba(38,166,154,0.18)"), "purple": ("#BA68C8", "rgba(142,36,170,0.20)"),
         "gray": ("#C8C8C8", "rgba(255,255,255,0.08)"), "red": ("#EF9A9A", "rgba(229,57,53,0.18)"),
         "green": ("#81C784", "rgba(67,160,71,0.20)")}
ASSETS = (Path(__file__).parent / "assets").as_posix()   # QSS butuh garis miring maju

STYLE = f"""
QWidget {{ color:{TEXT}; }}
QMainWindow, QDialog, QWizard, QStackedWidget, QScrollArea > QWidget > QWidget {{ background:{BG}; }}
QToolTip {{ background:{SURFACE_2}; color:{TEXT}; border:1px solid {BORDER}; padding:4px; }}

QFrame#sidebar {{ background:{SIDEBAR}; border-right:1px solid {BORDER}; }}
QFrame#sidebar QPushButton {{
    color:{SIDEBAR_TEXT}; background:transparent; border:none; border-left:3px solid transparent;
    text-align:left; padding:9px 14px 9px 13px; font-size:13px; }}
QFrame#sidebar QPushButton:hover {{ background:{SIDEBAR_HOVER}; color:{STRONG}; }}
QFrame#sidebar QPushButton:checked {{
    background:{SIDEBAR_ACTIVE}; color:{STRONG}; border-left:3px solid {ACCENT}; font-weight:bold; }}

QPushButton, QToolButton {{
    background:{SURFACE_2}; border:1px solid {BORDER}; border-radius:4px; padding:5px 12px; }}
QPushButton:hover, QToolButton:hover {{ border-color:{ACCENT}; }}
QPushButton:pressed, QToolButton:pressed {{ background:{ACCENT_SOFT}; }}
QPushButton:checked {{ background:{ACCENT_SOFT}; border-color:{ACCENT}; }}
QPushButton:disabled, QToolButton:disabled {{ color:{DISABLED}; border-color:{SURFACE_2}; }}
QPushButton:default {{ border-color:{ACCENT}; }}
QToolButton[autoRaise="true"] {{ background:transparent; border:none; }}

QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QDateEdit, QPlainTextEdit, QTextEdit,
QTextBrowser, QListWidget, QTableWidget, QTreeView {{
    background:{INPUT}; border:1px solid {BORDER}; border-radius:4px;
    selection-background-color:{ACCENT_SOFT}; selection-color:{STRONG}; }}
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QDateEdit {{ padding:4px 6px; }}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus, QDateEdit:focus,
QPlainTextEdit:focus {{ border-color:{ACCENT}; }}
QLineEdit:disabled, QComboBox:disabled {{ color:{DISABLED}; }}
QComboBox QAbstractItemView {{ background:{INPUT}; border:1px solid {BORDER};
    selection-background-color:{ACCENT_SOFT}; }}

QGroupBox {{ background:{SURFACE}; border:1px solid {BORDER}; border-radius:6px;
    margin-top:18px; padding:10px 8px 8px 8px; }}
QGroupBox::title {{ subcontrol-origin:margin; left:10px; padding:0 4px; color:{TEXT};
    font-weight:bold; }}

QHeaderView::section {{ background:{SURFACE_2}; color:{MUTED}; border:none;
    border-right:1px solid {BORDER}; border-bottom:1px solid {BORDER}; padding:5px 6px;
    font-weight:bold; }}
QTableWidget {{ gridline-color:{BORDER}; alternate-background-color:{SURFACE_2}; }}
QTableCornerButton::section {{ background:{SURFACE_2}; border:none; }}

QTabWidget::pane {{ border:1px solid {BORDER}; border-radius:4px; top:-1px; }}
QTabBar::tab {{ background:{SURFACE}; color:{MUTED}; border:1px solid {BORDER};
    padding:6px 14px; border-top-left-radius:4px; border-top-right-radius:4px; }}
QTabBar::tab:selected {{ color:{STRONG}; border-bottom:2px solid {ACCENT}; background:{SURFACE_2}; }}

QCheckBox::indicator, QAbstractItemView::indicator, QRadioButton::indicator {{
    width:14px; height:14px; border:1px solid {INDICATOR}; background:{INPUT}; }}
QCheckBox::indicator, QAbstractItemView::indicator {{ border-radius:3px; }}
QRadioButton::indicator {{ border-radius:8px; }}
QCheckBox::indicator:hover, QRadioButton::indicator:hover {{ border-color:{ACCENT}; }}
QCheckBox::indicator:checked, QAbstractItemView::indicator:checked {{
    background:{CHECKED}; border-color:{CHECKED}; image:url({ASSETS}/check.svg); }}
QRadioButton::indicator:checked {{ background:{CHECKED}; border-color:{CHECKED};
    image:url({ASSETS}/dot.svg); }}
QCheckBox::indicator:disabled, QRadioButton::indicator:disabled {{ border-color:{BORDER}; }}
QScrollBar:vertical {{ background:{BG}; width:10px; margin:0; }}
QScrollBar:horizontal {{ background:{BG}; height:10px; margin:0; }}
QScrollBar::handle {{ background:{BORDER}; border-radius:5px; min-height:24px; min-width:24px; }}
QScrollBar::handle:hover {{ background:{BORDER_STRONG}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width:0; height:0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background:none; }}
QSplitter::handle {{ background:{BORDER}; }}
QStatusBar {{ background:{SIDEBAR}; color:{MUTED}; border-top:1px solid {BORDER}; }}
QStatusBar QLabel {{ color:{MUTED}; }}
QMenu {{ background:{SURFACE}; border:1px solid {BORDER}; }}
QMenu::item:selected {{ background:{ACCENT_SOFT}; }}
QGraphicsView {{ border:1px solid {BORDER}; }}

QLabel#pageTitle {{ font-size:20px; font-weight:bold; }}
QLabel#colHeader {{ color:{MUTED}; font-weight:bold; }}
QPushButton#primary {{ background:{PRIMARY}; border-color:{PRIMARY}; color:#FFFFFF; font-weight:bold; }}
QPushButton#primary:hover {{ background:#1976D2; }}
QPushButton#danger {{ color:{BAD}; border-color:#6B2C2C; }}
QPushButton#danger:hover {{ background:#3A2626; border-color:{BAD}; }}
QPushButton#danger:disabled {{ color:{DISABLED}; border-color:{SURFACE_2}; }}
QPushButton#small {{ padding:3px 10px; }}
QLineEdit#search {{ padding:5px 8px; min-width:220px; }}
QFrame#card {{ background:{SURFACE}; border:1px solid {SURFACE_2}; border-radius:8px; }}
QFrame#card:hover {{ border-color:{BORDER_STRONG}; }}
QFrame#card[selected="true"] {{ border:1px solid {PRIMARY}; }}
QLabel#cardTitle {{ font-size:16px; font-weight:bold; }}
QFrame#addr {{ background:{INPUT}; border:1px solid {BORDER}; border-radius:4px; }}
QFrame#addr QLineEdit {{ background:transparent; border:none; padding:3px 6px; }}
QCheckBox#switch::indicator {{ width:36px; height:20px; border:none; background:transparent;
    image:url({ASSETS}/switch-off.svg); }}
QCheckBox#switch::indicator:checked {{ image:url({ASSETS}/switch-on.svg); background:transparent; }}
QPushButton#success {{ background:#43A047; border-color:#43A047; color:#FFFFFF; font-weight:bold; }}
QPushButton#success:hover {{ background:#388E3C; }}
QPushButton#success:disabled {{ background:{SURFACE_2}; border-color:{SURFACE_2}; color:{DISABLED}; }}
QLabel#sectionTitle {{ font-size:15px; font-weight:bold; }}
QFrame#floatbar {{ background:rgba(28,28,28,0.90); border:1px solid {BORDER_STRONG}; border-radius:8px; }}
QFrame#floatbar QToolButton {{ background:transparent; border:none; border-radius:6px; padding:4px 9px; }}
QFrame#floatbar QToolButton:hover {{ background:{SURFACE_2}; }}
QFrame#floatbar QToolButton:checked {{ background:{ACCENT_SOFT}; border:1px solid {BORDER_STRONG}; }}
QFrame#floatSep {{ background:{BORDER_STRONG}; max-width:1px; margin:4px 4px; }}
QLabel#chip {{ background:rgba(28,28,28,0.88); border:1px solid {BORDER_STRONG}; border-radius:10px;
    padding:3px 12px; }}
QListWidget#zoneList {{ background:transparent; border:none; }}
QListWidget#zoneList::item {{ border-radius:6px; margin:2px 0; }}
QListWidget#zoneList::item:hover {{ background:{SURFACE_2}; }}
QListWidget#zoneList::item:selected {{ background:{ACCENT_SOFT}; border:1px solid {BORDER_STRONG}; }}
QToolButton#rowBtn {{ background:transparent; border:none; padding:3px; border-radius:4px; }}
QToolButton#rowBtn:hover {{ background:{SURFACE_2}; }}
QPushButton#warn {{ color:{WARN}; border-color:#7A5420; }}
QPushButton#warn:hover {{ background:#3A3020; border-color:{WARN}; }}
QFrame#addCard {{ background:transparent; border:2px dashed {BORDER_STRONG}; border-radius:8px; }}
QFrame#addCard:hover {{ border-color:{ACCENT}; background:{SURFACE}; }}
QToolButton#section {{ background:transparent; border:none; font-weight:bold; text-align:left;
    padding:4px 0; }}
QPlainTextEdit#mono {{ font-family:monospace; }}
QLabel#thumb {{ background:#111111; border:1px solid {BORDER}; border-radius:6px; }}
QPushButton#red {{ background:#E53935; border-color:#E53935; color:#FFFFFF; font-weight:bold; }}
QPushButton#red:hover {{ background:#C62828; }}
QPushButton#primary:disabled, QPushButton#red:disabled {{ background:{SURFACE_2}; border-color:{SURFACE_2};
    color:{DISABLED}; }}
QFrame#sheet {{ background:#262626; border:1px solid {BORDER}; border-radius:8px; }}
QFrame#tile {{ background:{SURFACE_2}; border:1px solid {BORDER}; border-radius:6px; }}
QLabel#tileValue {{ font-size:18px; font-weight:bold; }}
QLabel#reportTitle {{ font-size:24px; font-weight:bold; }}
QLabel#camTitle {{ font-size:20px; font-weight:bold; }}
QTableWidget#plain {{ background:transparent; border:none; }}
QAbstractSpinBox {{ padding-right:20px; }}
QAbstractSpinBox::up-button, QAbstractSpinBox::down-button {{ subcontrol-origin:border; width:18px;
    background:transparent; border:none; border-left:1px solid {BORDER}; }}
QAbstractSpinBox::up-button {{ subcontrol-position:top right; }}
QAbstractSpinBox::down-button {{ subcontrol-position:bottom right; }}
QAbstractSpinBox::up-button:hover, QAbstractSpinBox::down-button:hover {{ background:{SURFACE_2}; }}
QAbstractSpinBox::up-arrow {{ image:url({ASSETS}/arrow-up.svg); width:9px; height:6px; }}
QAbstractSpinBox::down-arrow {{ image:url({ASSETS}/arrow-down.svg); width:9px; height:6px; }}
QAbstractSpinBox::up-arrow:disabled, QAbstractSpinBox::down-arrow:disabled {{ image:none; }}
QLabel#fieldLabel {{ color:{TEXT}; }}
QLabel#hint {{ color:{MUTED}; font-size:12px; }}
QListWidget#ruleList {{ background:transparent; border:none; outline:none; }}
QListWidget#ruleList::item {{ background:{SURFACE}; border:1px solid {SURFACE_2}; border-radius:6px;
    margin:3px 2px; }}
QListWidget#ruleList::item:hover {{ border-color:{BORDER_STRONG}; }}
QListWidget#ruleList::item:selected {{ background:rgba(30,136,229,0.16); border:1px solid {PRIMARY}; }}
QLabel#ruleTitle {{ font-size:14px; }}
QToolButton#kebab {{ background:transparent; border:none; padding:2px; }}
QToolButton#kebab:hover {{ background:{SURFACE_2}; border-radius:4px; }}
QToolButton#kebab::menu-indicator {{ image:none; }}
""" + "".join(
    f"QLabel[pill=\"{k}\"], QPushButton[pill=\"{k}\"] {{ color:{fg}; background:{bg}; border:none;"
    f" border-radius:9px; padding:2px 9px; }}\n" for k, (fg, bg) in PILLS.items())


def palette() -> QPalette:
    """Palet dasar (dipakai widget yang tidak terjangkau stylesheet, mis. pyqtgraph & dialog)."""
    p = QPalette()
    roles = {
        QPalette.ColorRole.Window: BG, QPalette.ColorRole.WindowText: TEXT,
        QPalette.ColorRole.Base: INPUT, QPalette.ColorRole.AlternateBase: SURFACE_2,
        QPalette.ColorRole.Text: TEXT, QPalette.ColorRole.Button: SURFACE_2,
        QPalette.ColorRole.ButtonText: TEXT, QPalette.ColorRole.ToolTipBase: SURFACE_2,
        QPalette.ColorRole.ToolTipText: TEXT, QPalette.ColorRole.Highlight: ACCENT_SOFT,
        QPalette.ColorRole.HighlightedText: STRONG, QPalette.ColorRole.Link: "#90CAF9",
        QPalette.ColorRole.PlaceholderText: "{INDICATOR}", QPalette.ColorRole.BrightText: BAD,
    }
    for role, color in roles.items():
        p.setColor(role, QColor(color))
    for role in (QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText,
                 QPalette.ColorRole.WindowText):
        p.setColor(QPalette.ColorGroup.Disabled, role, QColor("{DISABLED}"))
    return p


def apply_theme(app: QApplication) -> None:
    import pyqtgraph as pg

    app.setStyle("Fusion")                  # dasar netral yang konsisten di Linux & Windows
    app.setPalette(palette())
    app.setStyleSheet(STYLE)
    pg.setConfigOptions(foreground=MUTED, antialias=True)   # sumbu & legenda grafik
