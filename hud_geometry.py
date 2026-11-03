# -*- coding: utf-8 -*-
"""Shared HUD outlines and independent, per-window edge docking (PySide6)."""
import math
from PySide6.QtCore import (QObject, Signal, Slot, QThread, QPoint, QRect, QRectF,
                            QTimer, QEvent, QPropertyAnimation, QEasingCurve)
from PySide6.QtGui import QGuiApplication, QPainterPath, QCursor

SHAPES = ['圆角', '微圆角', '直角', '圆形', '正方形', '三角形', '胶囊']
_EDGES = ('left', 'right', 'top', 'bottom')


def _bounds(rect, shape):
    r = QRectF(rect)
    if shape in ('圆形', '正方形'):
        side = min(r.width(), r.height())
        r = QRectF(r.center().x() - side / 2, r.center().y() - side / 2, side, side)
    return r


def _radius(r, shape, radius=18):
    value = {'微圆角': 8, '直角': 0, '正方形': 0,
             '胶囊': min(r.width(), r.height()) / 2}.get(shape, radius)
    return max(0., min(float(value), r.width() / 2, r.height() / 2))


def shape_path(rect, shape, radius=18, dock_edge=None):
    """Return the actual outline; round shapes have a flat dock-facing side."""
    r = _bounds(rect, shape)
    path = QPainterPath()
    if r.isEmpty():
        return path
    if shape == '圆形':
        path.addEllipse(r)
    elif shape == '三角形':
        path.moveTo(r.center().x(), r.top())
        path.lineTo(r.right(), r.bottom())
        path.lineTo(r.left(), r.bottom())
        path.closeSubpath()
    else:
        rad = _radius(r, shape, radius)
        path.addRoundedRect(r, rad, rad)
        # Uniting a strip removes only the two corners touching the screen edge.
        if rad and dock_edge in _EDGES:
            strip = QRectF(r)
            if dock_edge == 'left': strip.setWidth(rad)
            elif dock_edge == 'right': strip.setLeft(r.right() - rad)
            elif dock_edge == 'top': strip.setHeight(rad)
            else: strip.setTop(r.bottom() - rad)
            extra = QPainterPath(); extra.addRect(strip)
            path = path.united(extra)
    return path


def _inside_integer(r):
    left, top = math.ceil(r.left()), math.ceil(r.top())
    right, bottom = math.floor(r.right()), math.floor(r.bottom())
    return QRect(left, top, max(0, right - left), max(0, bottom - top))


def content_rect(rect, shape):
    """A conservative axis-aligned layout area completely inside the outline."""
    r = _bounds(rect, shape)
    if shape == '圆形':
        inset = r.width() * (1 - 1 / math.sqrt(2)) / 2 + 2
        r.adjust(inset, inset, -inset, -inset)
    elif shape == '三角形':
        r = QRectF(r.left() + r.width() / 4 + 2, r.top() + r.height() / 2 + 3,
                   max(0, r.width() / 2 - 4), max(0, r.height() / 2 - 6))
    else:
        inset = _radius(r, shape) * (1 - 1 / math.sqrt(2)) + 3
        r.adjust(inset, inset, -inset, -inset)
    return _inside_integer(r)


def grip_position(rect, shape):
    """Top-left of the 22px grip, as far toward the lower right as fits."""
    r = _bounds(rect, shape)
    inner = content_rect(r, shape)
    point = QPoint(inner.x() + inner.width() - 22, inner.y() + inner.height() - 22)
    if shape == '三角形' and r.height() > 0:
        y = r.bottom() - 25
        right = r.center().x() + r.width() / 2 * ((y - r.top()) / r.height())
        point = QPoint(math.floor(right - 25), math.floor(y))
    path = shape_path(QRectF(rect), shape)
    if path.contains(QRectF(point.x(), point.y(), 22, 22)):
        return point
    # A window smaller than 22px cannot contain a full grip; center it safely.
    return QPoint(round(r.center().x() - 11), round(r.center().y() - 11))


def _subtract(intervals, start, end):
    result = []
    for a, b in intervals:
        if end <= a or start >= b:
            result.append((a, b))
        else:
            if start > a: result.append((a, min(start, b)))
            if end < b: result.append((max(end, a), b))
    return result


def _external_segments(screen, screens, edge):
    """Subtract all neighbouring displays from a work-area edge."""
    area = screen.availableGeometry()
    vertical = edge in ('left', 'right')
    segments = [(area.top(), area.bottom() + 1)] if vertical else [(area.left(), area.right() + 1)]
    line = {'left': area.left(), 'right': area.right() + 1,
            'top': area.top(), 'bottom': area.bottom() + 1}[edge]
    for other in screens:
        if other is screen: continue
        r = other.geometry()
        outside = ((r.left() < line <= r.right() + 1) if edge == 'left' else
                   (r.left() <= line < r.right() + 1) if edge == 'right' else
                   (r.top() < line <= r.bottom() + 1) if edge == 'top' else
                   (r.top() <= line < r.bottom() + 1))
        if outside:
            segments = _subtract(segments, r.top() if vertical else r.left(),
                                  r.bottom() + 1 if vertical else r.right() + 1)
    return segments


class DockManager(QObject):
    """One manager per window. Config keys: dock_edge, dock_screen, dock_offset.

    x/y record the fully revealed position, never the half-hidden animation frame.
    Public operations called from a worker are queued onto the GUI thread.
    """
    _request = Signal(str)
    SNAP_DISTANCE = 14
    HIDE_DELAY = 420

    def __init__(self, window, config, on_change=None):
        super().__init__(window)
        self.window, self.config, self.on_change = window, config, on_change
        self.dock_edge = config.get('dock_edge') if config.get('dock_edge') in _EDGES else None
        self.hidden = False
        self._screen = None
        self._segment = None
        self._revealed = QPoint(window.pos())
        self._request.connect(self._dispatch)
        self.animation = QPropertyAnimation(window, b'pos', self)
        self.animation.setDuration(190)
        self.animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self._hide)
        window.installEventFilter(self)
        app = QGuiApplication.instance()
        if app:
            app.screenAdded.connect(self._screen_added)
            app.screenRemoved.connect(self._screens_changed)
            for screen in app.screens(): self._screen_added(screen)

    @Slot(str)
    def _dispatch(self, name):
        getattr(self, name)()

    def _queue(self, name):
        if QThread.currentThread() != self.thread():
            self._request.emit(name)
            return True
        return False

    def _screen_added(self, screen):
        screen.availableGeometryChanged.connect(self._screens_changed)
        screen.geometryChanged.connect(self._screens_changed)

    def _screens_changed(self, *_):
        QTimer.singleShot(0, self.ensure_reachable)

    def _notify(self):
        self.config['dock_edge'] = self.dock_edge
        self.config['x'], self.config['y'] = self._revealed.x(), self._revealed.y()
        if self.dock_edge and self._screen:
            self.config['dock_screen'] = self._screen.name()
            area = self._screen.availableGeometry()
            self.config['dock_offset'] = (self._revealed.y() - area.top() if self.dock_edge in ('left', 'right')
                                          else self._revealed.x() - area.left())
        else:
            self.config.pop('dock_screen', None)
            self.config.pop('dock_offset', None)
        self.window.update()
        if self.on_change: self.on_change()

    def _screens(self):
        app = QGuiApplication.instance()
        return app.screens() if app else []

    def _segments(self, screen, edge):
        length = self.window.height() if edge in ('left', 'right') else self.window.width()
        return [ab for ab in _external_segments(screen, self._screens(), edge) if ab[1] - ab[0] >= length]

    def _target(self, hidden):
        p = QPoint(self._revealed)
        if not hidden or not self.dock_edge or not self._screen: return p
        w, h = self.window.width(), self.window.height()
        if self.dock_edge == 'left': p.setX(p.x() - w // 2)
        elif self.dock_edge == 'right': p.setX(p.x() + w // 2)
        elif self.dock_edge == 'top': p.setY(p.y() - h // 2)
        else: p.setY(p.y() + h // 2)
        return p

    def _move(self, hidden, animate=True):
        self.hidden = bool(hidden and self.dock_edge)
        target = self._target(self.hidden)
        self.animation.stop()
        if animate and self.window.isVisible() and self.window.pos() != target:
            self.animation.setStartValue(self.window.pos())
            self.animation.setEndValue(target)
            self.animation.start()
        else:
            self.window.move(target)

    def _attach(self, screen, edge, segment, along=None):
        area = screen.availableGeometry()
        w, h = self.window.width(), self.window.height()
        self._screen, self.dock_edge, self._segment = screen, edge, segment
        a, b = segment
        length = h if edge in ('left', 'right') else w
        current = self.window.y() if edge in ('left', 'right') else self.window.x()
        value = current if along is None else along
        # The whole window must face a genuine external segment, including
        # partially shared edges on displays of different resolutions.
        low, high = a, b - length
        value = round(max(low, min(value, high)))
        if edge in ('left', 'right'):
            value = max(area.top(), min(value, max(area.top(), area.bottom() + 1 - h)))
            self._revealed = QPoint(area.left() if edge == 'left' else area.right() + 1 - w, value)
        else:
            value = max(area.left(), min(value, max(area.left(), area.right() + 1 - w)))
            self._revealed = QPoint(value, area.top() if edge == 'top' else area.bottom() + 1 - h)

    def finish_drag(self):
        if self._queue('finish_drag'): return
        self.timer.stop(); self.animation.stop()
        if getattr(self.window, 'locked', False):
            self._move(self.hidden, False)
            return
        screens = self._screens()
        r = self.window.frameGeometry()
        # Use the display containing the center; at seams never snap to a farther display.
        screen = QGuiApplication.screenAt(r.center())
        if screen is None and screens:
            screen = min(screens, key=lambda s: self._distance(r.center(), s.availableGeometry()))
        choices = []
        if screen:
            area = screen.availableGeometry()
            for edge in _EDGES:
                distance = {'left': r.left() - area.left(), 'right': area.right() - r.right(),
                            'top': r.top() - area.top(), 'bottom': area.bottom() - r.bottom()}[edge]
                if distance > self.SNAP_DISTANCE: continue
                center = r.center().y() if edge in ('left', 'right') else r.center().x()
                for segment in self._segments(screen, edge):
                    if segment[0] <= center < segment[1]:
                        choices.append((abs(distance), edge, segment))
        if choices:
            _, edge, segment = min(choices, key=lambda item: item[0])
            self._attach(screen, edge, segment)
            self._move(True)
            self._notify()
        else:
            self.dock_edge = None; self.hidden = False
            self._revealed = QPoint(self.window.pos())
            self.ensure_reachable()
            self._notify()

    @staticmethod
    def _distance(point, area):
        dx = max(area.left() - point.x(), 0, point.x() - area.right())
        dy = max(area.top() - point.y(), 0, point.y() - area.bottom())
        return dx * dx + dy * dy

    def restore(self):
        if self._queue('restore'): return
        screens = self._screens()
        if not screens: return
        if self.dock_edge:
            screen = next((s for s in screens if s.name() == self.config.get('dock_screen')), None)
            if screen is None:
                screen = min(screens, key=lambda s: self._distance(self.window.frameGeometry().center(), s.availableGeometry()))
            segments = self._segments(screen, self.dock_edge)
            if segments:
                area = screen.availableGeometry()
                origin = area.top() if self.dock_edge in ('left', 'right') else area.left()
                try: along = origin + int(self.config.get('dock_offset', 0))
                except (TypeError, ValueError, OverflowError): along = origin
                length = self.window.height() if self.dock_edge in ('left', 'right') else self.window.width()
                segment = min(segments, key=lambda ab: abs((ab[0] + ab[1]) / 2 - along - length / 2))
                self._attach(screen, self.dock_edge, segment, along)
                self._move(True, False); self._notify()
                return
            self.dock_edge = None
        self.ensure_reachable()

    def ensure_reachable(self):
        if self._queue('ensure_reachable'): return
        screens = self._screens()
        if not screens: return
        if self.dock_edge:
            screen = next((s for s in screens if s.name() == self.config.get('dock_screen')), None)
            if screen is not None:
                segments = self._segments(screen, self.dock_edge)
                if segments:
                    length = self.window.height() if self.dock_edge in ('left', 'right') else self.window.width()
                    along = self._revealed.y() if self.dock_edge in ('left', 'right') else self._revealed.x()
                    segment = min(segments, key=lambda ab: abs((ab[0] + ab[1]) / 2 - along - length / 2))
                    was_hidden = self.hidden
                    self._attach(screen, self.dock_edge, segment, along)
                    self._move(was_hidden, False); self._notify()
                    return
            self.dock_edge = None; self.hidden = False
        screen = min(screens, key=lambda s: self._distance(self.window.frameGeometry().center(), s.availableGeometry()))
        area = screen.availableGeometry()
        x = max(area.left(), min(self.window.x(), max(area.left(), area.right() + 1 - self.window.width())))
        y = max(area.top(), min(self.window.y(), max(area.top(), area.bottom() + 1 - self.window.height())))
        self._revealed = QPoint(x, y)
        self._move(False, False); self._notify()

    def reveal(self):
        if self._queue('reveal'): return
        self.timer.stop()
        if self.dock_edge: self._move(False)

    def schedule_hide(self):
        if self._queue('schedule_hide'): return
        if self.dock_edge: self.timer.start(self.HIDE_DELAY)

    def _hide(self):
        if not self.dock_edge: return
        can_auto_hide = getattr(self.window, 'can_auto_hide', None)
        if callable(can_auto_hide) and not can_auto_hide():
            self.timer.start(self.HIDE_DELAY)
            return
        # Locks prevent manual dragging, never automatic docking animation.
        if self.window.underMouse() or self.window.frameGeometry().contains(QCursor.pos()): return
        if getattr(self.window, 'resizing', False):
            self.timer.start(self.HIDE_DELAY)
            return
        self._move(True)

    def eventFilter(self, watched, event):
        if watched is self.window:
            kind = event.type()
            if kind == QEvent.Type.Enter: self.reveal()
            elif kind == QEvent.Type.Leave: self.schedule_hide()
            elif kind == QEvent.Type.Hide:
                self.timer.stop(); self.animation.stop()
            elif kind == QEvent.Type.Resize and self.dock_edge:
                QTimer.singleShot(0, self.ensure_reachable)
        return super().eventFilter(watched, event)
