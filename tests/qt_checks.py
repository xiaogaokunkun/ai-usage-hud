"""Injected offline Qt acceptance checks. Uses only an isolated HUD_QT_CONFIG."""
from copy import deepcopy
from PySide6.QtCore import QPointF, QRect, QRectF
from PySide6.QtGui import QEnterEvent, QMouseEvent
import hud_geometry as geometry

out = Path(os.environ.get('HUD_TEST_OUTPUT', str(Path(os.environ['HUD_QT_TEST_SCRIPT']).parent)))
out.mkdir(parents=True,exist_ok=True)
result = {'checks': [], 'errors': []}
h, d = controller.hud, controller.details


def check(name, ok, detail=None):
    item = {'name': name, 'pass': bool(ok)}
    if detail is not None:
        item['detail'] = detail
    result['checks'].append(item)


def group(name, function):
    try:
        function()
    except Exception:
        result['errors'].append({'group': name, 'error': traceback.format_exc()})


def pump():
    app.processEvents()


def finish_animation(dock):
    dock.animation.setCurrentTime(dock.animation.duration())
    pump()


def enter(window):
    point = QPointF(3, 3)
    QApplication.sendEvent(window, QEnterEvent(point, point, QPointF(window.mapToGlobal(QPoint(3, 3)))))
    pump()


def apply(window, shape, width, height):
    window.profile.update(shape=shape, width=width, height=height, dock_edge=None)
    window.dock.dock_edge = None
    window.dock.hidden = False
    window.apply_profile(resize=True)
    pump()


def check_window(window, name):
    path = geometry.shape_path(QRectF(window.rect()).adjusted(1, 1, -1, -1), window.profile['shape'])
    rect = window.scroll.geometry() if window.scroll else window.content.geometry()
    check(name + ' safe content inside outline', path.contains(QRectF(rect)),
          [rect.x(), rect.y(), rect.width(), rect.height()])
    grip = QRectF(window.grip.geometry())
    check(name + ' white grip inside outline', path.contains(grip))
    labels = [label for label in window.content.findChildren(QLabel) if label.isVisible()]
    # Scroll contents may extend below the viewport, but actual label layouts must not overlap.
    rectangles = [(label, QRect(label.mapTo(window.content, QPoint()), label.size())) for label in labels]
    if window.role == 'rail':
        check(name + ' rail labels stay in safe area',
              all(window.content.rect().contains(rect) for _, rect in rectangles))
        ring_rects = [QRect(ring.mapTo(window.content, QPoint()), ring.size()) for ring in window.rings.values()]
        check(name + ' rail rings stay in safe area',
              all(window.content.rect().contains(rect) for rect in ring_rects))
    overlap = [(a.text(), b.text()) for i, (a, ra) in enumerate(rectangles)
               for b, rb in rectangles[i + 1:] if ra.intersects(rb)]
    check(name + ' text labels do not overlap', not overlap, overlap[:3])


def basic_checks():
    check('test config explicitly isolated', CONFIG.resolve() != (HERE / 'hud-qt.json').resolve())
    timers = [getattr(controller, key, None) for key in ('poll', 'quota_poll')]
    check('test mode has no real polling timer', not any(timer and timer.isActive() for timer in timers))
    check('default narrow rail is 68x252', h.width() == 68 and h.height() == 252)
    check('details initially hidden', not d.isVisible())
    check('distinct native windows', h.isWindow() and d.isWindow() and h.winId() != d.winId())
    before = h.geometry()
    enter(h)
    check('hover opens separate detail without resizing rail', d.isVisible() and h.geometry() == before)
    for _ in range(10):
        d.hide(); d.open_next_to(h); pump()
    check('repeated hover leaves rail geometry unchanged', h.geometry() == before)


def shape_checks():
    for shape in geometry.SHAPES:
        for window, dimensions in ((h, [(160, 300), (360, 360)]), (d, [(320, 280), (720, 620)])):
            other = d if window is h else h
            original_other_shape = other.profile['shape']
            for width, height in dimensions:
                apply(window, shape, width, height)
                window.show(); pump()
                name = '%s %s %dx%d' % (window.role, shape, width, height)
                check_window(window, name)
                check(name + ' independent shape', other.profile['shape'] == original_other_shape)
                current = h.size()
                d.hide(); d.open_next_to(h); pump()
                check(name + ' opening detail never resizes rail', h.size() == current)
    for window in (h, d):
        window.grab().save(str(out / ('vnext-' + window.role + '.png')))


def docking_checks():
    controller.cfg['locked'] = False
    d.hide()
    apply(h, '圆角', 68, 252)
    area = h.screen().availableGeometry()
    horizontal = area.left() + (area.width() - h.width()) // 2
    vertical = area.top() + (area.height() - h.height()) // 2
    positions = {'left': QPoint(area.left() - 8, vertical),
                 'right': QPoint(area.right() - h.width() + 9, vertical),
                 'top': QPoint(horizontal, area.top() - 8),
                 'bottom': QPoint(horizontal, area.bottom() - h.height() + 9)}
    for edge, point in positions.items():
        dock = h.dock
        dock.animation.stop(); dock.timer.stop(); dock.dock_edge = None
        h.move(point); dock.finish_drag(); finish_animation(dock)
        check(edge + ' drag attaches and half hides', dock.dock_edge == edge and dock.hidden)
        revealed = QPoint(dock._revealed)
        delta = h.pos() - revealed
        expected = {'left': QPoint(-h.width() // 2, 0), 'right': QPoint(h.width() // 2, 0),
                    'top': QPoint(0, -h.height() // 2), 'bottom': QPoint(0, h.height() // 2)}[edge]
        check(edge + ' half-hidden displacement', delta == expected)
        check(edge + ' stores revealed position', dock.config['x'] == revealed.x() and dock.config['y'] == revealed.y())
        dock.reveal(); finish_animation(dock)
        check(edge + ' hover reveal restores full window', not dock.hidden and h.pos() == revealed)
        controller.cfg['locked'] = True
        dock._move(True, False)
        dock.reveal(); finish_animation(dock)
        check(edge + ' locked window still reveals automatically', h.pos() == revealed and not dock.hidden)
        dock.restore(); finish_animation(dock)
        check(edge + ' restart restores docking without drift', dock.hidden and dock._revealed == revealed)
        controller.cfg['locked'] = False


def screen_checks():
    class Screen:
        def __init__(self, rect, name): self.rect, self._name = rect, name
        def geometry(self): return QRect(self.rect)
        def availableGeometry(self): return QRect(self.rect)
        def name(self): return self._name
    left = Screen(QRect(-1000, 0, 1000, 800), 'left')
    right = Screen(QRect(0, 200, 1200, 600), 'right')
    check('shared display seam excluded', geometry._external_segments(left, [left, right], 'right') == [(0, 200)])
    check('negative coordinate exterior retained', geometry._external_segments(left, [left, right], 'left') == [(0, 800)])
    check('reverse shared seam excluded', geometry._external_segments(right, [left, right], 'left') == [])
    dock = h.dock
    old_screens = dock._screens
    try:
        dock._screens = lambda: [right]
        dock.dock_edge = None
        h.move(-18000, -18000); dock.ensure_reachable()
        check('removed monitor recovers unreachable window', right.availableGeometry().contains(h.frameGeometry()))
    finally:
        dock._screens = old_screens


def automatic_hide_checks():
    class OutsideCursor:
        @staticmethod
        def pos(): return QPoint(-30000, -30000)
    old_geometry_cursor, old_main_cursor = geometry.QCursor, globals()['QCursor']
    old_under_mouse = [(window, window.underMouse) for window in (h, d)]
    try:
        geometry.QCursor = OutsideCursor
        globals()['QCursor'] = OutsideCursor
        for window in (h, d): window.underMouse = lambda: False
        d.hide(); controller.cfg['locked'] = False
        apply(h, '圆角', 68, 252)
        area = h.screen().availableGeometry()
        h.move(area.left() - 8, area.top() + 50)
        h.dock.finish_drag(); finish_animation(h.dock)
        h.dock.reveal(); finish_animation(h.dock)
        controller.cfg['locked'] = True
        h.dock._hide(); finish_animation(h.dock)
        check('locked rail automatic leave still half hides', h.dock.hidden)
        controller.cfg['locked'] = False
        apply(d, '圆角', 312, 360)
        d.show(); d.profile['follow'] = False
        d.move(area.right() - d.width() + 9, area.top() + 50)
        d.dock.finish_drag(); finish_animation(d.dock)
        d.dock.reveal(); finish_animation(d.dock)
        h.maybe_hide_details()
        check('leaving docked detail keeps native window visible', d.isVisible() and d.dock.timer.isActive())
        d.dock.timer.stop(); d.dock._hide(); finish_animation(d.dock)
        check('docked detail leave half hides rather than disappears', d.isVisible() and d.dock.hidden)
        d.dock.reveal(); finish_animation(d.dock)
        check('docked detail hover restores full window', d.isVisible() and not d.dock.hidden)
    finally:
        geometry.QCursor = old_geometry_cursor
        globals()['QCursor'] = old_main_cursor
        for window, method in old_under_mouse: window.underMouse = method
        for window in (h, d): window.dock.timer.stop(); window.dock.animation.stop()
        controller.cfg['locked'] = False


def lock_checks():
    h.dock.dock_edge = None
    h.dock.animation.stop(); h.dock.timer.stop()
    h.move(200, 200); controller.cfg['locked'] = True; pump()
    before = h.geometry()
    local = QPointF(h.content.geometry().center())
    global_point = QPointF(h.mapToGlobal(local.toPoint()))
    for kind, point, button, buttons in (
        (QEvent.Type.MouseButtonPress, global_point, Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton),
        (QEvent.Type.MouseMove, global_point + QPointF(100, 100), Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton),
        (QEvent.Type.MouseButtonRelease, global_point + QPointF(100, 100), Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton)):
        event = QMouseEvent(kind, local, point, button, buttons, Qt.KeyboardModifier.NoModifier)
        QApplication.sendEvent(h, event)
    pump()
    check('lock rejects manual dragging', h.geometry() == before and controller.drag is None)
    point = QPointF(4, 4)
    event = QMouseEvent(QEvent.Type.MouseButtonPress, point, QPointF(h.grip.mapToGlobal(QPoint(4, 4))),
                        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(h.grip, event); pump()
    check('lock rejects white-grip resize', not h.resizing and h.geometry() == before)
    enter(h)
    check('lock allows hovering details', d.isVisible())
    controller.cfg['locked'] = False


def settings_checks():
    controller.open_settings(); pump()
    settings = controller.settings
    settings.target.setCurrentIndex(0); pump()
    old_detail = deepcopy(d.profile)
    settings.shape.setCurrentText('三角形'); pump()
    check('settings shape targets rail only', h.profile['shape'] == '三角形'
          and all(d.profile[key] == old_detail[key] for key in ('shape', 'width', 'height')))
    settings.shape.setCurrentText('圆角'); pump()
    settings.width_input.setValue(180); settings.height_input.setValue(340); pump()
    check('settings custom rail size applied and saved', h.size().width() == 180 and h.size().height() == 340
          and h.profile['width'] == 180 and h.profile['height'] == 340)
    old_rail = deepcopy(h.profile)
    settings.target.setCurrentIndex(1); settings.shape.setCurrentText('正方形'); pump()
    check('settings shape targets details only', d.profile['shape'] == '正方形'
          and all(h.profile[key] == old_rail[key] for key in ('shape', 'width', 'height')))
    settings.lock_checkbox.setChecked(True); pump()
    check('settings lock updates windows and hides grips', h.locked and d.locked and not h.grip.isVisible() and not d.grip.isVisible())
    settings.lock_checkbox.setChecked(False); settings.hide(); pump()


def minimum_checks():
    for window in (h, d):
        for shape in geometry.SHAPES:
            apply(window, shape, 1, 1)
            window.show(); pump()
            check_window(window, '%s minimum %s' % (window.role, shape))


def quota_checks():
    captured = []
    old_save = controller.save
    controller.save = lambda: captured.append(True)
    try:
        sample = {'status': 'ok', 'source': 'test', 'fetched_at': time.time(),
                  'five_hour': {'used_percent': 75, 'remaining_percent': 25, 'reset_at': time.time() + 600},
                  'weekly': {'used_percent': 80, 'remaining_percent': 20, 'reset_at': time.time() + 100000}}
        controller.update_usage(sample); pump()
        texts = {key: label.text() for key, label in d.quota_labels.items()}
        check('five-hour and weekly show supplied values', '25' in texts['five_hour'] and '20' in texts['weekly'], texts)
        sample['status'] = 'stale'
        controller.update_usage(sample); pump()
        texts = d.quota_updated.text()
        check('stale quota visibly identified', any(word in texts for word in ('旧', '缓存', '过期', '快照', 'stale')), texts)
        unavailable = {'status': 'unavailable', 'source': None, 'fetched_at': None,
                       'five_hour': None, 'weekly': None}
        controller.update_usage(unavailable); pump()
        check('failed quota refresh retains visibly stale real snapshot',
              controller.codex_usage.get('five_hour', {}).get('remaining_percent') == 25
              and '过期' in d.quota_updated.text())
        controller.codex_usage = dict(unavailable)
        controller.update_usage({'status': 'unavailable', 'source': None, 'fetched_at': None,
                                 'five_hour': None, 'weekly': None}); pump()
        texts = {key: label.text() for key, label in d.quota_labels.items()}
        check('missing quotas stay unknown', all('%' not in text and any(word in text for word in ('—', '未知', '暂无', '不可用', '未获取')) for text in texts.values()), texts)
        check('quota display does not persist fake data', not captured)
    finally:
        controller.save = old_save


def reset_checks():
    controller.cfg['locked'] = True
    apply(h, '三角形', 300, 400)
    apply(d, '圆形', 600, 600)
    controller.reset_defaults(); pump()
    check('reset restores narrow default rail', h.size().width() == 68 and h.size().height() == 252)
    check('reset clears lock and docks', not h.locked and not h.dock.dock_edge and not d.dock.dock_edge)
    check('reset restores default shape and opacity', h.profile['shape'] == '圆角' and d.profile['shape'] == '圆角' and controller.cfg['transparency'] == 40)
    controller.save()
    stored = json.loads(CONFIG.read_text(encoding='utf-8'))
    check('reset persists independent window profiles', stored['windows']['rail'] == h.profile and stored['windows']['details'] == d.profile)


def run_checks():
    try:
        for name, function in [('defaults', basic_checks), ('shapes', shape_checks),
                               ('docking', docking_checks), ('screens', screen_checks), ('automatic hiding', automatic_hide_checks),
                               ('locking', lock_checks), ('settings', settings_checks),
                               ('minimum sizes', minimum_checks), ('quota', quota_checks), ('reset', reset_checks)]:
            group(name, function)
        result['passed'] = not result['errors'] and all(item['pass'] for item in result['checks'])
    except Exception:
        result['errors'].append({'group': 'runner', 'error': traceback.format_exc()})
        result['passed'] = False
    finally:
        (out / 'vnext-results.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        for window in controller.windows:
            window.hide()
        QTimer.singleShot(50, lambda: os._exit(0 if result['passed'] else 1))

QTimer.singleShot(700, run_checks)
