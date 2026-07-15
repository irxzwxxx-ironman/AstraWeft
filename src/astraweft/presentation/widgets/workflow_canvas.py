"""Zoomable dark DAG canvas with movable workflow node cards."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import QPointF, QRect, QRectF, Qt, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QPainter,
    QPainterPath,
    QPen,
    QWheelEvent,
)
from PySide6.QtWidgets import (
    QGraphicsItem,
    QGraphicsObject,
    QGraphicsPathItem,
    QGraphicsScene,
    QGraphicsView,
    QStyleOptionGraphicsItem,
    QWidget,
)

from astraweft.presentation.design_system.tokens import Colors
from astraweft.presentation.i18n import Translator


@dataclass(frozen=True, slots=True)
class CanvasNode:
    key: str
    name: str
    node_type: str
    input_schema: Mapping[str, object]
    output_schema: Mapping[str, object]
    x: int
    y: int
    status: str | None = None


@dataclass(frozen=True, slots=True)
class CanvasEdge:
    source_node: str
    source_port: str
    target_node: str
    target_port: str


class WorkflowNodeItem(QGraphicsObject):
    moved = Signal(str, int, int)

    def __init__(
        self,
        node: CanvasNode,
        *,
        editable: bool,
        translator: Translator | None = None,
    ) -> None:
        super().__init__()
        self.node = node
        self._translator = translator or Translator()
        self.setData(0, node.key)
        self.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
            | (
                QGraphicsItem.GraphicsItemFlag.ItemIsMovable
                | QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges
                if editable
                else QGraphicsItem.GraphicsItemFlag(0)
            )
        )
        self.setPos(node.x, node.y)
        self.setZValue(2)

    def boundingRect(self) -> QRectF:
        return QRectF(0, 0, 224, 132)

    def input_anchor(self) -> QPointF:
        return self.scenePos() + QPointF(0, 66)

    def output_anchor(self) -> QPointF:
        return self.scenePos() + QPointF(224, 66)

    def paint(
        self,
        painter: QPainter,
        _option: QStyleOptionGraphicsItem,
        _widget: QWidget | None = None,
    ) -> None:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        status_color = _status_color(self.node.status)
        border = QColor(
            Colors.PRIMARY if self.isSelected() else status_color or Colors.BORDER_STRONG
        )
        painter.setPen(QPen(border, 1.7 if self.isSelected() else 1.1))
        painter.setBrush(QBrush(QColor(Colors.ELEVATED)))
        painter.drawRoundedRect(self.boundingRect(), 13, 13)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(status_color or Colors.PRIMARY))
        painter.drawRoundedRect(QRectF(0, 0, 4, 132), 2, 2)

        painter.setPen(QColor(Colors.TEXT_DIM))
        type_font = QFont(painter.font())
        type_font.setPointSize(8)
        type_font.setBold(True)
        painter.setFont(type_font)
        painter.drawText(QRectF(16, 13, 190, 16), self.node.node_type.replace("_", " "))

        painter.setPen(QColor(Colors.TEXT))
        title_font = QFont(painter.font())
        title_font.setPointSize(11)
        title_font.setBold(True)
        painter.setFont(title_font)
        painter.drawText(
            QRectF(16, 34, 190, 23),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            self.node.name,
        )

        painter.setPen(QColor(Colors.TEXT_MUTED))
        body_font = QFont(painter.font())
        body_font.setPointSize(8)
        body_font.setBold(False)
        painter.setFont(body_font)
        inputs = ", ".join(_port_names(self.node.input_schema)) or self._translator.text(
            "无输入", "No inputs"
        )
        outputs = ", ".join(_port_names(self.node.output_schema)) or self._translator.text(
            "无输出", "No outputs"
        )
        painter.drawText(QRectF(16, 66, 190, 16), f"IN   {_elide(inputs, 30)}")
        painter.drawText(QRectF(16, 89, 190, 16), f"OUT  {_elide(outputs, 30)}")
        painter.setPen(QColor(Colors.TEXT_DIM))
        painter.drawText(QRectF(16, 111, 190, 14), _elide(self.node.key, 32))

        painter.setPen(QPen(QColor(Colors.CYAN), 1))
        painter.setBrush(QColor(Colors.SURFACE))
        painter.drawEllipse(QPointF(0, 66), 5, 5)
        painter.setPen(QPen(QColor(Colors.PRIMARY_BRIGHT), 1))
        painter.drawEllipse(QPointF(224, 66), 5, 5)

    def itemChange(
        self,
        change: QGraphicsItem.GraphicsItemChange,
        value: Any,
    ) -> Any:
        result = super().itemChange(change, value)
        if change is QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged:
            position = self.pos()
            self.moved.emit(self.node.key, round(position.x()), round(position.y()))
        return result


class WorkflowCanvas(QGraphicsView):
    node_selected = Signal(str)
    node_moved = Signal(str, int, int)

    def __init__(self, translator: Translator | None = None) -> None:
        super().__init__()
        self._translator = translator or Translator()
        self.setObjectName("WorkflowCanvas")
        self.setAccessibleName(self._translator.text("工作流画布", "Workflow canvas"))
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self._items: dict[str, WorkflowNodeItem] = {}
        self._edge_items: list[QGraphicsPathItem] = []
        self._edges: tuple[CanvasEdge, ...] = ()
        self.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing)
        self.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setBackgroundBrush(QColor(Colors.CANVAS))
        self._scene.selectionChanged.connect(self._selection_changed)

    def set_graph(
        self,
        nodes: Sequence[CanvasNode],
        edges: Sequence[CanvasEdge],
        *,
        editable: bool,
    ) -> None:
        self._scene.clear()
        self._items.clear()
        self._edge_items.clear()
        self._edges = tuple(edges)
        for node in nodes:
            item = WorkflowNodeItem(node, editable=editable, translator=self._translator)
            item.moved.connect(self._node_moved)
            self._scene.addItem(item)
            self._items[node.key] = item
        self._rebuild_edges()
        bounds = self._scene.itemsBoundingRect().adjusted(-140, -120, 180, 140)
        self._scene.setSceneRect(bounds if not bounds.isEmpty() else QRectF(-400, -250, 800, 500))

    def selected_key(self) -> str | None:
        selected = self._scene.selectedItems()
        if not selected:
            return None
        value = selected[0].data(0)
        return value if isinstance(value, str) else None

    def select_node(self, key: str) -> None:
        item = self._items.get(key)
        if item is None:
            return
        self._scene.clearSelection()
        item.setSelected(True)
        self.centerOn(item)

    def fit_graph(self) -> None:
        bounds = self._scene.itemsBoundingRect()
        if not bounds.isEmpty():
            self.fitInView(bounds.adjusted(-70, -60, 70, 60), Qt.AspectRatioMode.KeepAspectRatio)

    def _node_moved(self, key: str, x: int, y: int) -> None:
        self._rebuild_edges()
        self.node_moved.emit(key, x, y)

    def _selection_changed(self) -> None:
        key = self.selected_key()
        if key is not None:
            self.node_selected.emit(key)

    def _rebuild_edges(self) -> None:
        for item in self._edge_items:
            self._scene.removeItem(item)
        self._edge_items.clear()
        for edge in self._edges:
            source = self._items.get(edge.source_node)
            target = self._items.get(edge.target_node)
            if source is None or target is None:
                continue
            start = source.output_anchor()
            end = target.input_anchor()
            distance = max(70.0, abs(end.x() - start.x()) * 0.48)
            path = QPainterPath(start)
            path.cubicTo(
                start + QPointF(distance, 0),
                end - QPointF(distance, 0),
                end,
            )
            item = QGraphicsPathItem(path)
            item.setPen(QPen(QColor(Colors.PRIMARY), 2.0))
            item.setZValue(0)
            item.setToolTip(
                f"{edge.source_node}.{edge.source_port} → {edge.target_node}.{edge.target_port}"
            )
            self._scene.addItem(item)
            self._edge_items.append(item)

    def drawBackground(self, painter: QPainter, rect: QRectF | QRect) -> None:
        super().drawBackground(painter, rect)
        area = QRectF(rect)
        minor = 24
        major = minor * 5
        painter.setPen(QPen(QColor("#151A25"), 0))
        left = int(area.left()) - (int(area.left()) % minor)
        top = int(area.top()) - (int(area.top()) % minor)
        x = left
        while x < area.right():
            painter.drawLine(QPointF(x, area.top()), QPointF(x, area.bottom()))
            x += minor
        y = top
        while y < area.bottom():
            painter.drawLine(QPointF(area.left(), y), QPointF(area.right(), y))
            y += minor
        painter.setPen(QPen(QColor("#1C2230"), 0))
        x = left - (left % major)
        while x < area.right():
            painter.drawLine(QPointF(x, area.top()), QPointF(x, area.bottom()))
            x += major
        y = top - (top % major)
        while y < area.bottom():
            painter.drawLine(QPointF(area.left(), y), QPointF(area.right(), y))
            y += major

    def wheelEvent(self, event: QWheelEvent) -> None:
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            factor = 1.12 if event.angleDelta().y() > 0 else 1 / 1.12
            current = self.transform().m11()
            if 0.35 <= current * factor <= 2.4:
                self.scale(factor, factor)
            event.accept()
            return
        super().wheelEvent(event)


def _port_names(schema: Mapping[str, object]) -> tuple[str, ...]:
    properties = schema.get("properties")
    if not isinstance(properties, Mapping):
        return ()
    return tuple(str(name) for name in properties)


def _elide(value: str, limit: int) -> str:
    return value if len(value) <= limit else value[: limit - 1] + "…"


def _status_color(status: str | None) -> str | None:
    if status in {"SUCCESS"}:
        return Colors.SUCCESS
    if status in {"FAILED", "CANCELED", "SKIPPED"}:
        return Colors.DANGER
    if status in {"RUNNING", "READY"}:
        return Colors.CYAN
    if status in {"WAITING_APPROVAL", "PENDING"}:
        return Colors.WARNING
    return None
