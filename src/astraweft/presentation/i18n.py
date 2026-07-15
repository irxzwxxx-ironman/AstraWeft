"""Small presentation localization boundary for the initial Chinese/English catalog."""

from __future__ import annotations

from PySide6.QtCore import QLocale


class Translator:
    """Select translated copy and locale-aware numeric formatting."""

    def __init__(self, language: str = "zh_CN") -> None:
        self.language = "en_US" if language == "en_US" else "zh_CN"
        self.locale = QLocale(self.language)

    @property
    def english(self) -> bool:
        return self.language == "en_US"

    def text(self, chinese: str, english: str, **values: object) -> str:
        template = english if self.english else chinese
        return template.format(**values)

    def integer(self, value: int) -> str:
        return self.locale.toString(value)

    def decimal(self, value: float, places: int = 1) -> str:
        return self.locale.toString(value, "f", places)

    def money(self, currency: str, amount_micros: int) -> str:
        return f"{currency} {self.locale.toString(amount_micros / 1_000_000, 'f', 6)}"


__all__ = ["Translator"]
