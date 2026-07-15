"""Global Qt stylesheet and platform-aware font selection."""

from __future__ import annotations

import platform
import sys
from pathlib import Path

from PySide6.QtCore import QSettings
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QApplication

from astraweft.presentation.design_system.tokens import Colors


def _preferred_font() -> str:
    families = set(QFontDatabase.families())
    candidates = (
        ("SF Pro Display", "SF Pro Text", "Helvetica Neue")
        if sys.platform == "darwin"
        else ("Segoe UI Variable", "Segoe UI", "Noto Sans")
    )
    return next(
        (family for family in candidates if family in families), QApplication.font().family()
    )


def fixed_width_font() -> QFont:
    """Return an installed code font without Qt's missing Monospace alias."""
    families = set(QFontDatabase.families())
    candidates = (
        ("SFMono-Regular", "Menlo", "Monaco")
        if sys.platform == "darwin"
        else ("Cascadia Mono", "Consolas", "Noto Sans Mono", "DejaVu Sans Mono")
    )
    family = next(
        (candidate for candidate in candidates if candidate in families),
        QApplication.font().family(),
    )
    return QFont(family)


def apply_theme(
    application: QApplication,
    *,
    theme: str = "dark",
    reduce_motion: bool = False,
) -> None:
    """Apply typography and the Dark Cyber AI component theme."""
    application.setProperty("astraweftTheme", theme)
    application.setProperty("astraweftReduceMotion", reduce_motion or system_reduce_motion())
    font = QFont(_preferred_font(), 13)
    font.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
    application.setFont(font)
    application.setStyle("Fusion")
    application.setStyleSheet(_stylesheet())


def system_reduce_motion() -> bool:
    """Read the host accessibility preference where a stable native key exists."""
    system = platform.system()
    if system == "Darwin":
        preferences = Path.home() / "Library/Preferences/com.apple.universalaccess.plist"
        value = QSettings(str(preferences), QSettings.Format.NativeFormat).value(
            "reduceMotion", False
        )
        return _as_bool(value)
    if system == "Windows":
        settings = QSettings(
            "HKEY_CURRENT_USER\\Software\\Microsoft\\Windows\\CurrentVersion\\Explorer\\Advanced",
            QSettings.Format.NativeFormat,
        )
        return not _as_bool(settings.value("TaskbarAnimations", True))
    return False


def _as_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return False


def _stylesheet() -> str:
    return f"""
    * {{
        color: {Colors.TEXT};
        outline: none;
    }}
    QMainWindow, QWidget#AppRoot {{
        background-color: {Colors.CANVAS};
    }}
    QWidget {{
        selection-background-color: {Colors.PRIMARY};
        selection-color: white;
    }}
    QFrame#Sidebar {{
        background-color: {Colors.SIDEBAR};
        border-right: 1px solid {Colors.BORDER};
    }}
    QLabel#LogoMark {{
        background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
            stop:0 {Colors.PRIMARY_BRIGHT}, stop:0.55 {Colors.PRIMARY}, stop:1 {Colors.CYAN});
        border-radius: 12px;
        color: white;
        font-size: 17px;
        font-weight: 800;
    }}
    QLabel#BrandName {{
        color: {Colors.TEXT};
        font-size: 16px;
        font-weight: 750;
        letter-spacing: 1px;
    }}
    QLabel#BrandMeta, QLabel#NavSection {{
        color: {Colors.TEXT_DIM};
        font-size: 10px;
        font-weight: 650;
        letter-spacing: 1.3px;
    }}
    QPushButton#NavButton {{
        background: transparent;
        border: 1px solid transparent;
        border-radius: 10px;
        color: {Colors.TEXT_MUTED};
        font-size: 13px;
        font-weight: 540;
        padding: 0 13px;
        text-align: left;
    }}
    QPushButton#NavButton:hover {{
        background-color: {Colors.SURFACE_ALT};
        color: {Colors.TEXT};
    }}
    QPushButton#NavButton:checked {{
        background-color: rgba(124, 108, 255, 0.16);
        border: 1px solid rgba(139, 124, 255, 0.30);
        color: #DED9FF;
        font-weight: 650;
    }}
    QFrame#Topbar {{
        background-color: rgba(9, 11, 16, 0.96);
        border-bottom: 1px solid {Colors.BORDER};
    }}
    QLabel#PageTitle {{
        color: {Colors.TEXT};
        font-size: 19px;
        font-weight: 720;
    }}
    QLabel#Breadcrumb {{
        color: {Colors.TEXT_DIM};
        font-size: 11px;
    }}
    QLineEdit#GlobalSearch {{
        min-height: 36px;
        max-height: 36px;
        min-width: 310px;
        background-color: {Colors.SURFACE};
        border: 1px solid {Colors.BORDER};
        border-radius: 10px;
        color: {Colors.TEXT};
        padding: 0 14px;
        font-size: 12px;
    }}
    QLineEdit#GlobalSearch:focus {{
        border: 1px solid {Colors.PRIMARY};
        background-color: {Colors.SURFACE_ALT};
    }}
    QLineEdit#GlobalSearch::placeholder {{
        color: {Colors.TEXT_DIM};
    }}
    QPushButton#GhostButton {{
        min-height: 34px;
        background-color: {Colors.SURFACE};
        border: 1px solid {Colors.BORDER};
        border-radius: 9px;
        color: {Colors.TEXT_MUTED};
        padding: 0 12px;
        font-size: 12px;
    }}
    QPushButton#GhostButton:hover {{
        border-color: {Colors.BORDER_STRONG};
        color: {Colors.TEXT};
        background-color: {Colors.SURFACE_ALT};
    }}
    QPushButton#DangerButton {{
        min-height: 36px;
        background-color: rgba(240, 108, 124, 0.14);
        border: 1px solid rgba(240, 108, 124, 0.55);
        border-radius: 9px;
        color: #FFABB6;
        padding: 0 16px;
        font-size: 12px;
        font-weight: 650;
    }}
    QPushButton#DangerButton:hover {{
        background-color: rgba(240, 108, 124, 0.22);
        border-color: {Colors.DANGER};
    }}
    QPushButton#IconButton {{
        background: transparent;
        border: 1px solid transparent;
        border-radius: 8px;
        color: {Colors.TEXT_MUTED};
        font-size: 16px;
    }}
    QPushButton#IconButton:hover {{
        background-color: {Colors.SURFACE_ALT};
        border-color: {Colors.BORDER};
        color: {Colors.TEXT};
    }}
    QPushButton#PrimaryButton:focus, QPushButton#GhostButton:focus,
    QPushButton#DangerButton:focus, QPushButton#IconButton:focus,
    QPushButton#NavButton:focus {{
        border: 1px solid {Colors.CYAN};
    }}
    QLineEdit#TextInput, QComboBox#SelectInput,
    QSpinBox#NumberInput, QDoubleSpinBox#NumberInput, QPlainTextEdit#TextArea {{
        min-height: 36px;
        background-color: {Colors.SURFACE};
        border: 1px solid {Colors.BORDER};
        border-radius: 9px;
        color: {Colors.TEXT};
        padding: 0 12px;
    }}
    QLineEdit#TextInput:focus, QComboBox#SelectInput:focus,
    QSpinBox#NumberInput:focus, QDoubleSpinBox#NumberInput:focus,
    QPlainTextEdit#TextArea:focus {{
        border-color: {Colors.CYAN};
        background-color: {Colors.SURFACE_ALT};
    }}
    QComboBox#SelectInput::drop-down {{
        border: none;
        width: 28px;
    }}
    QLabel#Badge {{
        min-height: 22px;
        border-radius: 8px;
        padding: 0 8px;
        color: {Colors.TEXT_MUTED};
        background-color: {Colors.SURFACE_ALT};
        border: 1px solid {Colors.BORDER};
        font-size: 10px;
        font-weight: 650;
    }}
    QLabel#Badge[tone="success"] {{ color: {Colors.SUCCESS}; border-color: #286A56; }}
    QLabel#Badge[tone="warning"] {{ color: {Colors.WARNING}; border-color: #6D5830; }}
    QLabel#Badge[tone="danger"] {{ color: {Colors.DANGER}; border-color: #713844; }}
    QLabel#Badge[tone="info"] {{ color: {Colors.CYAN}; border-color: #285C70; }}
    QPushButton#PrimaryButton {{
        min-height: 36px;
        background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
            stop:0 {Colors.PRIMARY}, stop:1 #6254DF);
        border: 1px solid #9185FF;
        border-radius: 9px;
        color: white;
        padding: 0 16px;
        font-size: 12px;
        font-weight: 650;
    }}
    QPushButton#PrimaryButton:hover {{
        background-color: {Colors.PRIMARY_BRIGHT};
        border-color: #B4ABFF;
    }}
    QPushButton#PrimaryButton:disabled {{
        background-color: {Colors.SURFACE_ALT};
        border-color: {Colors.BORDER};
        color: {Colors.TEXT_DIM};
    }}
    QLabel#Avatar {{
        background-color: {Colors.ELEVATED};
        border: 1px solid {Colors.BORDER_STRONG};
        border-radius: 17px;
        color: #D7D1FF;
        font-size: 10px;
        font-weight: 750;
    }}
    QScrollArea, QScrollArea > QWidget > QWidget {{
        background: transparent;
        border: none;
    }}
    QFrame#HeroBanner {{
        background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
            stop:0 #17152B, stop:0.52 #121928, stop:1 #0F1D27);
        border: 1px solid #2E3450;
        border-radius: 16px;
    }}
    QLabel#HeroEyebrow {{
        color: {Colors.CYAN};
        font-size: 10px;
        font-weight: 700;
        letter-spacing: 1.4px;
    }}
    QLabel#HeroTitle {{
        color: {Colors.TEXT};
        font-size: 24px;
        font-weight: 760;
    }}
    QLabel#HeroBody {{
        color: {Colors.TEXT_MUTED};
        font-size: 12px;
    }}
    QFrame#MetricCard, QFrame#SectionCard {{
        background-color: {Colors.SURFACE};
        border: 1px solid {Colors.BORDER};
        border-radius: 14px;
    }}
    QFrame#MetricCard:hover, QFrame#SectionCard:hover {{
        border-color: {Colors.BORDER_STRONG};
        background-color: #141925;
    }}
    QLabel#MetricLabel, QLabel#SectionMeta {{
        color: {Colors.TEXT_MUTED};
        font-size: 11px;
        font-weight: 570;
    }}
    QLabel#MetricValue {{
        color: {Colors.TEXT};
        font-size: 25px;
        font-weight: 760;
    }}
    QLabel#MetricFoot {{
        color: {Colors.TEXT_DIM};
        font-size: 10px;
    }}
    QLabel#SectionTitle {{
        color: {Colors.TEXT};
        font-size: 14px;
        font-weight: 680;
    }}
    QLabel#BodyText {{
        color: {Colors.TEXT_MUTED};
        font-size: 12px;
    }}
    QLabel#MutedText {{
        color: {Colors.TEXT_DIM};
        font-size: 11px;
    }}
    QFrame#HealthRow {{
        background-color: {Colors.SURFACE_ALT};
        border: 1px solid #202736;
        border-radius: 10px;
    }}
    QLabel#HealthName {{
        color: {Colors.TEXT_MUTED};
        font-size: 11px;
    }}
    QLabel#HealthValue {{
        color: {Colors.TEXT};
        font-size: 11px;
        font-weight: 620;
    }}
    QLabel#EmptyIcon {{
        background-color: rgba(124, 108, 255, 0.12);
        border: 1px solid rgba(124, 108, 255, 0.28);
        border-radius: 22px;
        color: {Colors.PRIMARY_BRIGHT};
        font-size: 19px;
    }}
    QLabel#EmptyTitle {{
        color: {Colors.TEXT};
        font-size: 13px;
        font-weight: 650;
    }}
    QLabel#ErrorIcon {{
        background-color: rgba(240, 108, 124, 0.12);
        border: 1px solid rgba(240, 108, 124, 0.35);
        border-radius: 22px;
        color: {Colors.DANGER};
        font-size: 20px;
        font-weight: 750;
    }}
    QLabel#TraceText {{
        color: {Colors.TEXT_DIM};
        background-color: {Colors.CANVAS};
        border: 1px solid {Colors.BORDER};
        border-radius: 7px;
        padding: 7px 10px;
        font-size: 10px;
    }}
    QFrame#SkeletonBlock {{
        background-color: {Colors.SURFACE_ALT};
        border: 1px solid {Colors.BORDER};
        border-radius: 7px;
    }}
    QFrame#Toast {{
        background-color: {Colors.ELEVATED};
        border: 1px solid {Colors.BORDER_STRONG};
        border-radius: 12px;
    }}
    QFrame#Toast[tone="danger"] {{ border-color: rgba(240, 108, 124, 0.65); }}
    QFrame#Toast[tone="warning"] {{ border-color: rgba(244, 189, 94, 0.60); }}
    QFrame#Toast[tone="success"] {{ border-color: rgba(54, 212, 154, 0.55); }}
    QLabel#ToastText {{ color: {Colors.TEXT}; font-size: 11px; }}
    QLabel#ToastMarker {{ color: {Colors.CYAN}; font-size: 8px; }}
    QDialog#ConfirmDialog {{
        background-color: {Colors.ELEVATED};
        border: 1px solid {Colors.BORDER_STRONG};
    }}
    QLabel#DialogTitle, QLabel#DrawerTitle {{
        color: {Colors.TEXT};
        font-size: 15px;
        font-weight: 700;
    }}
    QFrame#Drawer {{
        background-color: {Colors.SIDEBAR};
        border-left: 1px solid {Colors.BORDER_STRONG};
    }}
    QFrame#DrawerDivider {{ background-color: {Colors.BORDER}; border: none; }}
    QTableView#DataTable {{
        background-color: {Colors.SURFACE};
        alternate-background-color: {Colors.SURFACE_ALT};
        border: 1px solid {Colors.BORDER};
        border-radius: 10px;
        selection-background-color: rgba(124, 108, 255, 0.20);
        selection-color: {Colors.TEXT};
    }}
    QTableView#DataTable::item {{ padding: 8px; border-bottom: 1px solid {Colors.BORDER}; }}
    QGraphicsView#WorkflowCanvas {{
        background-color: {Colors.CANVAS};
        border: 1px solid {Colors.BORDER};
        border-radius: 12px;
    }}
    QListWidget#ProblemList {{
        background-color: {Colors.CANVAS};
        border: 1px solid {Colors.BORDER};
        border-radius: 9px;
        color: {Colors.TEXT_MUTED};
        padding: 5px;
    }}
    QListWidget#ProblemList::item {{
        padding: 8px;
        border-bottom: 1px solid {Colors.BORDER};
    }}
    QHeaderView {{ background-color: {Colors.SURFACE_ALT}; }}
    QHeaderView::section {{
        background-color: {Colors.SURFACE_ALT};
        color: {Colors.TEXT_MUTED};
        border: none;
        border-bottom: 1px solid {Colors.BORDER_STRONG};
        padding: 9px;
        font-size: 10px;
        font-weight: 650;
    }}
    QTabWidget#TabView::pane {{
        background-color: {Colors.SURFACE};
        border: 1px solid {Colors.BORDER};
        border-radius: 9px;
        top: -1px;
    }}
    QTabBar::tab {{
        background: transparent;
        color: {Colors.TEXT_MUTED};
        border-bottom: 2px solid transparent;
        padding: 9px 14px;
    }}
    QTabBar::tab:selected {{ color: {Colors.TEXT}; border-bottom-color: {Colors.PRIMARY}; }}
    QFrame#StatusBar {{
        background-color: {Colors.SIDEBAR};
        border-top: 1px solid {Colors.BORDER};
    }}
    QLabel#StatusText {{
        color: {Colors.TEXT_DIM};
        font-size: 10px;
    }}
    QFrame#StatusPill {{
        background-color: {Colors.SURFACE};
        border: 1px solid {Colors.BORDER};
        border-radius: 9px;
    }}
    QLabel#PillText {{
        color: {Colors.TEXT_MUTED};
        font-size: 9px;
        font-weight: 680;
        letter-spacing: 0.5px;
    }}
    QFrame#FoundationCard {{
        background-color: {Colors.SURFACE};
        border: 1px solid {Colors.BORDER};
        border-radius: 18px;
    }}
    QDialog#ProviderDialog {{
        background-color: {Colors.CANVAS};
    }}
    QFrame#ProviderCard, QFrame#FormSection {{
        background-color: {Colors.SURFACE};
        border: 1px solid {Colors.BORDER};
        border-radius: 14px;
    }}
    QFrame#ProviderCard:hover {{
        border-color: {Colors.BORDER_STRONG};
        background-color: #141925;
    }}
    QFrame#PluginSummary {{
        background-color: rgba(67, 200, 255, 0.05);
        border: 1px solid #243443;
        border-radius: 11px;
    }}
    QLabel#ContentTitle {{
        color: {Colors.TEXT};
        font-size: 22px;
        font-weight: 740;
    }}
    QLabel#CardTitle {{
        color: {Colors.TEXT};
        font-size: 15px;
        font-weight: 680;
    }}
    QLabel#ProviderMark {{
        background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
            stop:0 rgba(124,108,255,0.28), stop:1 rgba(67,200,255,0.16));
        border: 1px solid rgba(124,108,255,0.42);
        border-radius: 11px;
        color: #DAD5FF;
        font-size: 12px;
        font-weight: 760;
    }}
    QLabel#FormLabel {{
        color: {Colors.TEXT_MUTED};
        font-size: 11px;
        font-weight: 620;
    }}
    QLabel#FormHint {{
        color: {Colors.TEXT_DIM};
        font-size: 10px;
    }}
    QLabel#FormError {{
        color: #FFABB6;
        background-color: rgba(240, 108, 124, 0.10);
        border: 1px solid rgba(240, 108, 124, 0.35);
        border-radius: 8px;
        padding: 9px 11px;
    }}
    QLabel#CatalogSummary {{
        color: {Colors.CYAN};
        background-color: rgba(67, 200, 255, 0.06);
        border: 1px solid #243443;
        border-radius: 9px;
        padding: 9px 12px;
        font-size: 11px;
    }}
    QFrame#ModelDetail {{
        background-color: {Colors.SURFACE};
        border: 1px solid {Colors.BORDER};
        border-radius: 12px;
    }}
    QPlainTextEdit#SchemaViewer {{
        background-color: {Colors.CANVAS};
        border: none;
        border-radius: 8px;
        color: #B9C5D9;
        padding: 10px;
        font-size: 10px;
    }}
    QSplitter#ModelCatalogSplitter::handle {{
        background-color: transparent;
        width: 10px;
    }}
    QCheckBox#SchemaCheckBox {{
        color: {Colors.TEXT_MUTED};
        spacing: 8px;
    }}
    QCheckBox#SchemaCheckBox::indicator {{
        width: 17px;
        height: 17px;
        border: 1px solid {Colors.BORDER_STRONG};
        border-radius: 5px;
        background-color: {Colors.SURFACE_ALT};
    }}
    QCheckBox#SchemaCheckBox::indicator:checked {{
        background-color: {Colors.PRIMARY};
        border-color: {Colors.PRIMARY_BRIGHT};
    }}
    QDialogButtonBox QPushButton {{
        min-height: 34px;
        min-width: 82px;
        background-color: {Colors.SURFACE_ALT};
        border: 1px solid {Colors.BORDER_STRONG};
        border-radius: 9px;
        padding: 0 14px;
    }}
    QDialogButtonBox QPushButton:hover {{
        border-color: {Colors.CYAN};
        background-color: {Colors.ELEVATED};
    }}
    QLabel#FoundationGlyph {{
        background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
            stop:0 rgba(124,108,255,0.25), stop:1 rgba(67,200,255,0.12));
        border: 1px solid rgba(124,108,255,0.35);
        border-radius: 30px;
        color: {Colors.PRIMARY_BRIGHT};
        font-size: 25px;
    }}
    QLabel#FoundationTitle {{
        color: {Colors.TEXT};
        font-size: 21px;
        font-weight: 730;
    }}
    QScrollBar:vertical {{
        background: transparent;
        width: 8px;
        margin: 4px 1px;
    }}
    QScrollBar::handle:vertical {{
        background: {Colors.BORDER_STRONG};
        min-height: 32px;
        border-radius: 3px;
    }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
        height: 0;
    }}
    QToolTip {{
        background-color: {Colors.ELEVATED};
        color: {Colors.TEXT};
        border: 1px solid {Colors.BORDER_STRONG};
        padding: 6px 8px;
    }}
    """
