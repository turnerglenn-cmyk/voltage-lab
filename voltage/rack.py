"""The rack: module panels, knobs, jacks and patch cables."""
import math

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import (QBrush, QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen,
                         QRadialGradient)
from PyQt6.QtWidgets import QGraphicsItem, QGraphicsPathItem, QGraphicsScene, QGraphicsView, QMenu

from .engine import STEP, format_value, pos_to_value, value_to_pos

COLW, PAD = 66, 10
TITLE_H, KROW, JROW = 50, 86, 60
PANEL_H = 500
ROW_H = PANEL_H + 26
GRID = 10
CABLE_COLORS = ["#d8362f", "#e8b923", "#3f7fd9", "#3da95a", "#e07a2a", "#b9bcc2", "#9b59d0"]


def font(px, bold=True, spacing=0.0):
    f = QFont("DejaVu Sans")
    f.setPixelSize(px)
    f.setBold(bold)
    if spacing:
        f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, spacing)
    return f


F_TITLE, F_MODEL, F_LABEL, F_VALUE = font(12, True, 1.2), font(9, False, 1.0), font(9), font(9, False)


class KnobItem(QGraphicsItem):
    """Skirted knob. Drag up/down to turn (Shift = fine), wheel to nudge, double-click to reset."""

    def __init__(self, module, index, prm):
        super().__init__(module)
        self.module, self.index, self.prm = module, index, prm
        self.stepped = prm["curve"] == STEP
        self.value = prm["def"]
        self.pos01 = value_to_pos(prm, self.value)
        self.setCursor(Qt.CursorShape.SizeVerCursor)
        self.setToolTip(f"{prm['name']} — drag to turn, Shift for fine, double-click to reset")

    def boundingRect(self):
        return QRectF(-COLW / 2, -42, COLW, 84)

    def set_value(self, value, notify=True):
        value = min(self.prm["max"], max(self.prm["min"], value))
        if self.stepped:
            value = float(round(value))
        self.value = value
        self.pos01 = value_to_pos(self.prm, value)
        if notify:
            self.module.scene().engine.set_param(self.module.mid, self.index, value)
        self.update()

    def set_pos(self, pos):
        pos = min(1.0, max(0.0, pos))
        self.set_value(pos_to_value(self.prm, pos))
        self.pos01 = pos       # keep the raw travel so stepped knobs click over evenly

    def paint(self, p, option, widget=None):
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setFont(F_LABEL)
        p.setPen(QColor("#e9e6df"))
        p.drawText(QRectF(-COLW / 2, -42, COLW, 12), Qt.AlignmentFlag.AlignCenter, self.prm["name"])
        ticks = len(self.prm["opts"]) if self.prm["opts"] else 11
        p.setPen(QPen(QColor("#a9a59c"), 1.2))
        for i in range(ticks):
            a = math.radians(-135 + 270 * i / (ticks - 1))
            p.drawLine(QPointF(22 * math.sin(a), -22 * math.cos(a)), QPointF(25 * math.sin(a), -25 * math.cos(a)))
        skirt = QRadialGradient(-5, -6, 26)
        skirt.setColorAt(0, QColor("#3a3a3a"))
        skirt.setColorAt(1, QColor("#050505"))
        p.setPen(QPen(QColor("#000"), 1))
        p.setBrush(QBrush(skirt))
        p.drawEllipse(QPointF(0, 0), 19, 19)
        cap = QLinearGradient(-10, -10, 10, 10)
        cap.setColorAt(0, QColor("#f4f4f2"))
        cap.setColorAt(0.5, QColor("#b5b6b8"))
        cap.setColorAt(1, QColor("#77797d"))
        p.setPen(QPen(QColor("#4b4b4b"), 0.8))
        p.setBrush(QBrush(cap))
        p.drawEllipse(QPointF(0, 0), 11.5, 11.5)
        pos = value_to_pos(self.prm, self.value) if self.stepped else self.pos01
        a = math.radians(-135 + 270 * pos)
        p.setPen(QPen(QColor("#ffffff"), 2.4, cap=Qt.PenCapStyle.RoundCap))
        p.drawLine(QPointF(12.5 * math.sin(a), -12.5 * math.cos(a)), QPointF(18 * math.sin(a), -18 * math.cos(a)))
        p.setPen(QPen(QColor("#1b1b1b"), 2, cap=Qt.PenCapStyle.RoundCap))
        p.drawLine(QPointF(4 * math.sin(a), -4 * math.cos(a)), QPointF(10 * math.sin(a), -10 * math.cos(a)))
        p.setFont(F_VALUE)
        p.setPen(QColor("#e8b923") if self.prm["opts"] else QColor("#8f8b83"))
        p.drawText(QRectF(-COLW / 2, 28, COLW, 12), Qt.AlignmentFlag.AlignCenter,
                   format_value(self.prm, self.value))

    def mousePressEvent(self, ev):
        if ev.button() == Qt.MouseButton.LeftButton:
            self._y, self._start = ev.screenPos().y(), self.pos01
            ev.accept()
        else:
            ev.ignore()

    def mouseMoveEvent(self, ev):
        fine = ev.modifiers() & Qt.KeyboardModifier.ShiftModifier
        travel = 900.0 if fine else (90.0 if self.prm["opts"] else 180.0)
        self.set_pos(self._start + (self._y - ev.screenPos().y()) / travel)

    def mouseReleaseEvent(self, ev):
        self.pos01 = value_to_pos(self.prm, self.value)

    def mouseDoubleClickEvent(self, ev):
        self.set_value(self.prm["def"])

    def wheelEvent(self, ev):
        up = 1 if ev.delta() > 0 else -1
        if self.stepped:
            self.set_value(self.value + up)
        else:
            self.set_pos(self.pos01 + up * (0.004 if ev.modifiers() & Qt.KeyboardModifier.ShiftModifier else 0.025))
        ev.accept()


class JackItem(QGraphicsItem):
    """A patch point. Drag between an output and an input to run a cable."""

    def __init__(self, module, index, name, is_output):
        super().__init__(module)
        self.module, self.index, self.name, self.is_output = module, index, name, is_output
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setToolTip(("Output: " if is_output else "Input: ") + name
                        + " — drag to patch, right-click to unplug")

    def boundingRect(self):
        return QRectF(-COLW / 2, -30, COLW, 48)

    def center(self):
        return self.mapToScene(QPointF(0, 0))

    def paint(self, p, option, widget=None):
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setFont(F_LABEL)
        label = QRectF(-COLW / 2 + 5, -30, COLW - 10, 13)
        if self.is_output:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor("#e9e6df"))
            p.drawRoundedRect(label, 2, 2)
            p.setPen(QColor("#111"))
        else:
            p.setPen(QColor("#e9e6df"))
        p.drawText(label, Qt.AlignmentFlag.AlignCenter, self.name)
        ring = QLinearGradient(-11, -11, 11, 11)
        ring.setColorAt(0, QColor("#fafafa"))
        ring.setColorAt(0.5, QColor("#a7a9ac"))
        ring.setColorAt(1, QColor("#5d5f63"))
        p.setPen(QPen(QColor("#2a2a2a"), 1))
        p.setBrush(QBrush(ring))
        p.drawEllipse(QPointF(0, 0), 11.5, 11.5)
        p.setBrush(QColor("#6f7175"))
        p.drawEllipse(QPointF(0, 0), 8, 8)
        p.setBrush(QColor("#050505"))
        p.drawEllipse(QPointF(0, 0), 5.5, 5.5)

    def mousePressEvent(self, ev):
        scene = self.scene()
        if ev.button() == Qt.MouseButton.RightButton:
            for cable in scene.cables_at(self):
                scene.remove_cable(cable)
        elif ev.button() == Qt.MouseButton.LeftButton:
            scene.begin_drag(self)
        ev.accept()

    def mouseMoveEvent(self, ev):
        self.scene().drag_to(ev.scenePos())

    def mouseReleaseEvent(self, ev):
        self.scene().end_drag(ev.scenePos())

    def contextMenuEvent(self, ev):
        ev.accept()


class CableItem(QGraphicsPathItem):
    def __init__(self, src, dst, color, loose=None):
        super().__init__()
        self.src, self.dst, self.color = src, dst, color
        self.loose = loose           # free end while being dragged
        self.setZValue(10)
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.refresh()

    def ends(self):
        a = self.src.center() if self.src else self.loose
        b = self.dst.center() if self.dst else self.loose
        return a, b

    def refresh(self):
        a, b = self.ends()
        sag = min(160.0, 30 + abs(b.x() - a.x()) * 0.22 + abs(b.y() - a.y()) * 0.1)
        path = QPainterPath(a)
        path.cubicTo(QPointF(a.x(), a.y() + sag), QPointF(b.x(), b.y() + sag), b)
        self.setPath(path)

    def boundingRect(self):
        return super().boundingRect().adjusted(-12, -12, 12, 12)

    def paint(self, p, option, widget=None):
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = QColor(self.color)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(QColor(0, 0, 0, 110), 8, cap=Qt.PenCapStyle.RoundCap))
        p.drawPath(self.path().translated(1.5, 3))
        p.setPen(QPen(c.darker(170), 6.5, cap=Qt.PenCapStyle.RoundCap))
        p.drawPath(self.path())
        p.setPen(QPen(c, 4.2, cap=Qt.PenCapStyle.RoundCap))
        p.drawPath(self.path())
        p.setPen(QPen(c.lighter(150), 1.2, cap=Qt.PenCapStyle.RoundCap))
        p.drawPath(self.path().translated(-0.8, -0.8))
        for end in self.ends():
            p.setPen(QPen(QColor("#111"), 1))
            p.setBrush(c.darker(120))
            p.drawEllipse(end, 7.5, 7.5)
            p.setBrush(QColor("#d9d9d9"))
            p.drawEllipse(end, 3, 3)


class ModuleItem(QGraphicsItem):
    def __init__(self, mid, desc):
        super().__init__()
        self.mid, self.desc = mid, desc
        cols = desc["cols"]
        self.w = cols * COLW + 2 * PAD
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges)
        self.knobs, self.ins, self.outs = [], [], []
        for i, prm in enumerate(desc["params"]):
            k = KnobItem(self, i, prm)
            k.setPos(PAD + COLW * (i % cols + 0.5), TITLE_H + KROW * (i // cols) + 44)
            self.knobs.append(k)
        # Jacks sit at the bottom: inputs fill from the first cell, outputs end at the last.
        n_in, n_out = len(desc["ins"]), len(desc["outs"])
        rows = -(-(n_in + n_out) // cols)
        top = PANEL_H - 14 - rows * JROW

        def cell(c):
            return QPointF(PAD + COLW * (c % cols + 0.5), top + JROW * (c // cols) + 36)

        for i, name in enumerate(desc["ins"]):
            j = JackItem(self, i, name, False)
            j.setPos(cell(i))
            self.ins.append(j)
        for i, name in enumerate(desc["outs"]):
            j = JackItem(self, i, name, True)
            j.setPos(cell(rows * cols - n_out + i))
            self.outs.append(j)
        self.jack_top = top

    def boundingRect(self):
        return QRectF(0, 0, self.w, PANEL_H)

    def paint(self, p, option, widget=None):
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = self.boundingRect().adjusted(0.5, 0.5, -0.5, -0.5)
        g = QLinearGradient(0, 0, self.w, PANEL_H)
        g.setColorAt(0, QColor("#222224"))
        g.setColorAt(1, QColor("#0d0d0e"))
        p.setBrush(QBrush(g))
        p.setPen(QPen(QColor("#e8b923") if self.isSelected() else QColor("#6b6b6e"), 1.5 if self.isSelected() else 1))
        p.drawRoundedRect(r, 3, 3)
        for x in (9, self.w - 9):
            for y in (9, PANEL_H - 9):
                p.setPen(QPen(QColor("#222"), 0.8))
                p.setBrush(QColor("#9a9c9f"))
                p.drawEllipse(QPointF(x, y), 3.6, 3.6)
                p.drawLine(QPointF(x - 2.4, y - 1.2), QPointF(x + 2.4, y + 1.2))
        p.setPen(QColor("#f3f0e8"))
        p.setFont(F_TITLE)
        p.drawText(QRectF(0, 12, self.w, 16), Qt.AlignmentFlag.AlignCenter, self.desc["title"])
        p.setPen(QColor("#8f8b83"))
        p.setFont(F_MODEL)
        p.drawText(QRectF(0, 29, self.w, 12), Qt.AlignmentFlag.AlignCenter, self.desc["model"])
        p.setPen(QPen(QColor("#4a4a4d"), 1))
        p.drawLine(QPointF(16, self.jack_top), QPointF(self.w - 16, self.jack_top))

    # dragging the panel moves the module, snapped to the rack rows
    def mousePressEvent(self, ev):
        if ev.button() == Qt.MouseButton.LeftButton:
            self.scene().clearSelection()
            self.setSelected(True)
            self._grab = ev.scenePos() - self.pos()
            self.setZValue(2)
            ev.accept()
        else:
            ev.ignore()

    def mouseMoveEvent(self, ev):
        p = ev.scenePos() - self._grab
        self.setPos(max(0, round(p.x() / GRID) * GRID), max(0, round(p.y() / ROW_H)) * ROW_H)

    def mouseReleaseEvent(self, ev):
        self.setZValue(0)
        self.scene().fit()

    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged and self.scene():
            self.scene().refresh_cables(self)
        return super().itemChange(change, value)

    def contextMenuEvent(self, ev):
        menu = QMenu()
        reset = menu.addAction("Reset knobs")
        unplug = menu.addAction("Unplug all cables")
        delete = menu.addAction("Remove module")
        chosen = menu.exec(ev.screenPos())
        scene = self.scene()
        if chosen == reset:
            for k in self.knobs:
                k.set_value(k.prm["def"])
        elif chosen == unplug:
            for c in [c for c in scene.cables if self in (c.src.module, c.dst.module)]:
                scene.remove_cable(c)
        elif chosen == delete:
            scene.remove_module(self)


class RackScene(QGraphicsScene):
    def __init__(self, engine):
        super().__init__()
        self.engine = engine
        self.modules, self.cables = [], []
        self.drag = None
        self.color_index = 0
        self.fit()

    # -- modules -----------------------------------------------------------
    def add_module(self, type_, pos=None, params=None):
        desc = self.engine.types[type_]
        item = ModuleItem(self.engine.add_module(type_), desc)
        self.addItem(item)
        item.setPos(pos if pos is not None else self.free_spot(item.w))
        names = [p["name"] for p in desc["params"]]
        for name, value in (params or {}).items():
            if name not in names:
                continue
            knob = item.knobs[names.index(name)]
            if isinstance(value, str):
                value = knob.prm["opts"].index(value)
            knob.set_value(value)
        self.modules.append(item)
        self.fit()
        return item

    def free_spot(self, width, limit=1560):
        """Next free position: append to the last occupied row, wrapping when it is full."""
        row = max((int(m.y() // ROW_H) for m in self.modules), default=0)
        x = max((m.x() + m.w for m in self.modules if int(m.y() // ROW_H) == row), default=0)
        if x and x + width > limit:
            row, x = row + 1, 0
        return QPointF(x, row * ROW_H)

    def remove_module(self, item):
        for c in [c for c in self.cables if item in (c.src.module, c.dst.module)]:
            self.remove_cable(c)
        self.engine.remove_module(item.mid)
        self.modules.remove(item)
        self.removeItem(item)
        self.fit()

    def clear_rack(self):
        self.engine.clear()
        for item in self.cables + self.modules:
            self.removeItem(item)
        self.modules, self.cables = [], []
        self.color_index = 0
        self.fit()

    # -- cables ------------------------------------------------------------
    def add_cable(self, src, dst, color=None):
        if any(c.src is src and c.dst is dst for c in self.cables):
            return
        if color is None:
            color = CABLE_COLORS[self.color_index % len(CABLE_COLORS)]
            self.color_index += 1
        cable = CableItem(src, dst, color)
        self.addItem(cable)
        self.cables.append(cable)
        self.engine.connect(src.module.mid, src.index, dst.module.mid, dst.index, True)

    def remove_cable(self, cable):
        self.engine.connect(cable.src.module.mid, cable.src.index, cable.dst.module.mid, cable.dst.index, False)
        self.cables.remove(cable)
        self.removeItem(cable)

    def cables_at(self, jack):
        return [c for c in self.cables if jack in (c.src, c.dst)]

    def refresh_cables(self, module):
        for c in self.cables:
            if module in (c.src.module, c.dst.module):
                c.refresh()

    def begin_drag(self, jack):
        color = None
        if not jack.is_output:
            plugged = self.cables_at(jack)
            if plugged:                       # pull an existing cable out of this input
                cable = plugged[-1]
                jack, color = cable.src, cable.color
                self.remove_cable(cable)
        ghost = CableItem(jack if jack.is_output else None, None if jack.is_output else jack,
                          color or CABLE_COLORS[self.color_index % len(CABLE_COLORS)], jack.center())
        self.addItem(ghost)
        self.drag = (jack, ghost, color)

    def drag_to(self, pos):
        if self.drag:
            self.drag[1].loose = pos
            self.drag[1].refresh()

    def end_drag(self, pos):
        if not self.drag:
            return
        start, ghost, color = self.drag
        self.drag = None
        self.removeItem(ghost)
        target = next((i for i in self.items(pos) if isinstance(i, JackItem)), None)
        if target and target.is_output != start.is_output:
            src, dst = (start, target) if start.is_output else (target, start)
            self.add_cable(src, dst, color)      # a re-plugged cable keeps its colour

    # -- patches -----------------------------------------------------------
    def to_patch(self):
        mods = [{"id": f"m{m.mid}", "type": m.desc["type"], "x": m.x(), "y": m.y(),
                 "params": {k.prm["name"]: k.value for k in m.knobs}} for m in self.modules]
        wires = [[f"m{c.src.module.mid}", c.src.name, f"m{c.dst.module.mid}", c.dst.name, c.color]
                 for c in self.cables]
        return {"modules": mods, "cables": wires}

    def load_patch(self, patch):
        self.clear_rack()
        by_id = {}
        for m in patch["modules"]:
            if m["type"] not in self.engine.types:
                continue
            pos = QPointF(m["x"], m["y"]) if "x" in m else None
            by_id[m["id"]] = self.add_module(m["type"], pos, m.get("params"))
        for wire in patch["cables"]:
            s, o, d, i = wire[:4]
            if s not in by_id or d not in by_id:
                continue
            src = next((j for j in by_id[s].outs if j.name == o), None)
            dst = next((j for j in by_id[d].ins if j.name == i), None)
            if src and dst:
                self.add_cable(src, dst, wire[4] if len(wire) > 4 else None)

    # -- cabinet -----------------------------------------------------------
    def fit(self):
        w = max([1400.0] + [m.x() + m.w + 300 for m in self.modules])
        rows = max([1] + [int(m.y() // ROW_H) + 1 for m in self.modules])
        self.rows = rows
        self.setSceneRect(-24, -24, w + 48, rows * ROW_H + 30)
        self.update()

    def drawBackground(self, p, rect):
        wood = QLinearGradient(0, rect.top(), 0, rect.bottom())
        wood.setColorAt(0, QColor("#4a2e1a"))
        wood.setColorAt(1, QColor("#2e1b0f"))
        p.fillRect(rect, QBrush(wood))
        s = self.sceneRect()
        for row in range(self.rows):
            y = row * ROW_H
            p.fillRect(QRectF(s.left() + 12, y - 8, s.width() - 24, PANEL_H + 16), QColor("#0b0908"))
            p.fillRect(QRectF(s.left() + 12, y - 8, s.width() - 24, 6), QColor("#3a3a3c"))
            p.fillRect(QRectF(s.left() + 12, y + PANEL_H + 2, s.width() - 24, 6), QColor("#3a3a3c"))

    def keyPressEvent(self, ev):
        if ev.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            for m in [m for m in self.selectedItems() if isinstance(m, ModuleItem)]:
                self.remove_module(m)
        else:
            super().keyPressEvent(ev)


class RackView(QGraphicsView):
    def __init__(self, scene):
        super().__init__(scene)
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self.setFrameShape(QGraphicsView.Shape.NoFrame)
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.FullViewportUpdate)

    def wheelEvent(self, ev):
        if ev.modifiers() & Qt.KeyboardModifier.ControlModifier:      # Ctrl+wheel zooms
            f = 1.1 if ev.angleDelta().y() > 0 else 1 / 1.1
            if 0.4 < self.transform().m11() * f < 2.5:
                self.scale(f, f)
        else:
            super().wheelEvent(ev)

    def fit_rack(self):
        """Zoom out just enough to show the whole rack (never in past 100%)."""
        self.resetTransform()
        s, v = self.scene().sceneRect(), self.viewport().size()
        f = max(0.55, min(1.0, v.width() / s.width(), v.height() / s.height()))
        self.scale(f, f)
