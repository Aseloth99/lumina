"""Çerçevesiz pencere — geniş köşe tutamaçları + hafif yuvarlak köşe."""

from __future__ import annotations

import sys

from PyQt6.QtCore import QEvent, QPoint, QRect, Qt
from PyQt6.QtWidgets import (
    QAbstractButton,
    QApplication,
    QCheckBox,
    QComboBox,
    QMainWindow,
    QMenu,
    QPushButton,
    QSlider,
    QTabBar,
    QWidget,
)

_EDGE_CURSORS = {
    Qt.Edge.LeftEdge: Qt.CursorShape.SizeHorCursor,
    Qt.Edge.RightEdge: Qt.CursorShape.SizeHorCursor,
    Qt.Edge.TopEdge: Qt.CursorShape.SizeVerCursor,
    Qt.Edge.BottomEdge: Qt.CursorShape.SizeVerCursor,
    Qt.Edge.TopEdge | Qt.Edge.LeftEdge: Qt.CursorShape.SizeFDiagCursor,
    Qt.Edge.BottomEdge | Qt.Edge.RightEdge: Qt.CursorShape.SizeFDiagCursor,
    Qt.Edge.TopEdge | Qt.Edge.RightEdge: Qt.CursorShape.SizeBDiagCursor,
    Qt.Edge.BottomEdge | Qt.Edge.LeftEdge: Qt.CursorShape.SizeBDiagCursor,
}

# FramelessWindow.eventFilter'ın işlediği tek olay türleri (taşıma/yeniden boyutlandırma).
_FRAMELESS_EVENT_TYPES = frozenset(
    {
        QEvent.Type.MouseButtonPress,
        QEvent.Type.MouseButtonDblClick,
        QEvent.Type.MouseMove,
        QEvent.Type.MouseButtonRelease,
    }
)

_INTERACTIVE_ROLES = {"nav", "sys", "icon", "glass"}
_INTERACTIVE_NAMES = {
    "brandBar",
    "navBar",
    "closeBtn",
    "livePlay",
    "liveOrb",
    "filmstrip",
    "detailsClose",
    "fileChip",
    "busyChip",
    "seekBar",
    "volumePopup",
    "seekPreview",
    "dockWrap",
    "bottomDock",
}


def top_level_window_type() -> Qt.WindowType:
    """Ana pencerenin Qt pencere türü.

    Windows'ta Qt, süreçteki ilk `Qt::Window` türü pencereyi oluştururken GPU'yu Direct3D 9 ile
    tanımlar (qwindowswindow.cpp calcPosition -> forcedScreenForGLWindow -> GpuDescription::detect,
    AMD için QTBUG-50371 geçici çözümü). `Direct3DCreate9` ekran kartı sürücüsünü yükleyip imzasını
    doğruladığı için NVIDIA dizüstünde ~190 ms sürüyor ve her açılışta ilk kareyi geciktiriyordu.
    `Qt::Dialog` türü bu denetimi atlar; çerçevesiz pencerede Win32 stilleri aynı kalır (sahipsiz,
    görev çubuğu düğmesi var). Diğer platformlarda (X11/macOS dialog anlamı farklı) `Window` kalır.
    """
    return Qt.WindowType.Dialog if sys.platform == "win32" else Qt.WindowType.Window


class FramelessWindow(QMainWindow):
    BORDER = 10
    CORNER = 28

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        flags = (
            top_level_window_type()
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowSystemMenuHint
            | Qt.WindowType.WindowMinimizeButtonHint
            | Qt.WindowType.WindowMaximizeButtonHint
        )
        self.setWindowFlags(flags)
        self.setMouseTracking(True)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self._filters_ready = False
        self._resize_edge: Qt.Edge | None = None
        self._resize_origin = QPoint()
        self._resize_geom = QRect()
        self._pending_move: QPoint | None = None
        self._move_origin: QPoint | None = None
        self._move_geom = QRect()
        self._is_snapped: str | None = None
        self._normal_geom: QRect | None = None

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._apply_os_rounding()
        self._enable_windows_snap()
        self.hook_resize_filters()

    def _apply_os_rounding(self) -> None:
        """Windows 11 yerel DWM yuvarlak köşe desteği (DWMWA_WINDOW_CORNER_PREFERENCE = 33)."""
        if sys.platform != "win32":
            return
        try:
            import ctypes
            hwnd = int(self.winId())
            # DWMWA_WINDOW_CORNER_PREFERENCE = 33, DWMWCP_ROUND = 2
            pref = ctypes.c_int(2)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, 33, ctypes.byref(pref), ctypes.sizeof(pref)
            )
        except Exception:
            pass

    def _enable_windows_snap(self) -> None:
        """Windows Aero Snap desteğini aktif et (WS_THICKFRAME + WS_MAXIMIZEBOX)."""
        if sys.platform != "win32":
            return
        try:
            import ctypes

            hwnd = int(self.winId())
            gwl_style = -16
            ws_thickframe = 0x00040000
            ws_maximizebox = 0x00010000
            ws_minimizebox = 0x00020000
            user32 = ctypes.windll.user32
            style = user32.GetWindowLongW(hwnd, gwl_style)
            user32.SetWindowLongW(
                hwnd, gwl_style, style | ws_thickframe | ws_maximizebox | ws_minimizebox
            )
            swp_framechanged = 0x0020
            swp_nomove = 0x0002
            swp_nosize = 0x0001
            swp_nozorder = 0x0004
            user32.SetWindowPos(
                hwnd, 0, 0, 0, 0, 0, swp_framechanged | swp_nomove | swp_nosize | swp_nozorder
            )
        except Exception:
            pass

    def hook_resize_filters(self) -> None:
        widgets = [self, self.centralWidget(), *self.resize_targets(), *self.drag_widgets()]
        for widget in widgets:
            if widget is None:
                continue
            widget.setMouseTracking(True)
            widget.installEventFilter(self)
            if widget is self:
                continue
            for child in widget.findChildren(QWidget):
                if child.property("role") in _INTERACTIVE_ROLES:
                    continue
                if child.objectName() in _INTERACTIVE_NAMES:
                    continue
                child.setMouseTracking(True)
                child.installEventFilter(self)
        self._filters_ready = True

    def resize_targets(self) -> list[QWidget]:
        return []

    def eventFilter(self, obj, event):  # noqa: N802
        # Bu filter uygulama genelinde HER olaya çağrılır (Paint/Timer/UpdateRequest dahil).
        # Aşağıdaki mantık yalnızca 4 fare olayını işler, kalanı hep QObject varsayılanına düşer;
        # pahalı kontrol zincirini (popup sorgusu, isinstance, ebeveyn gezintisi) atla.
        if event.type() not in _FRAMELESS_EVENT_TYPES:
            return super().eventFilter(obj, event)

        if QApplication.activePopupWidget() is not None:
            return False

        from PyQt6.QtGui import QWindow
        if isinstance(obj, QWindow):
            return False

        if isinstance(obj, (QPushButton, QSlider, QMenu, QComboBox, QCheckBox, QTabBar, QAbstractButton)):
            return False
        # Eğer olay ana pencere dışındaki bir üst pencereye (Tool penceresi, popup, menü vb.) aitse
        # frameless taşıma/boyutlandırma işlemlerini çalıştırma
        if isinstance(obj, QWidget):
            obj_win = obj.window()
            if obj_win is not None and obj_win is not self:
                return super().eventFilter(obj, event)
            curr = obj
            while curr is not None and curr is not self:
                if isinstance(curr, (QPushButton, QSlider, QMenu, QComboBox, QCheckBox, QTabBar, QAbstractButton)):
                    return False
                if curr.property("role") in _INTERACTIVE_ROLES or curr.objectName() in _INTERACTIVE_NAMES:
                    return False
                curr = curr.parentWidget()

        et = event.type()
        if et in (
            QEvent.Type.MouseButtonPress,
            QEvent.Type.MouseButtonDblClick,
            QEvent.Type.MouseMove,
            QEvent.Type.MouseButtonRelease,
        ):
            # Tam ekran modunda pencere taşıma ve kenar boyutlandırma tamamen devre dışıdır
            if self.isFullScreen():
                self._pending_move = None
                return super().eventFilter(obj, event)

            if not hasattr(event, "globalPosition"):
                return super().eventFilter(obj, event)
            global_pos = event.globalPosition().toPoint()
            local = self.mapFromGlobal(global_pos)
            if et == QEvent.Type.MouseMove:
                if self._resize_edge is not None:
                    self._apply_manual_resize(global_pos)
                    return True
                if self._move_origin is not None:
                    self._apply_manual_move(global_pos)
                    return True
                if self._pending_move is not None:
                    if (global_pos - self._pending_move).manhattanLength() >= 8:
                        origin = self._pending_move
                        self._pending_move = None
                        self._start_drag(origin, global_pos)
                        return True
                self._refresh_cursor(local)
            elif et == QEvent.Type.MouseButtonRelease:
                if self._resize_edge is not None and event.button() == Qt.MouseButton.LeftButton:
                    self._end_manual_resize()
                    return True
                if self._move_origin is not None and event.button() == Qt.MouseButton.LeftButton:
                    self._end_manual_move()
                    return True
                self._pending_move = None
            elif et == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
                if self._try_begin_resize(local, global_pos):
                    return True
                if self._in_drag_region(global_pos):
                    self._pending_move = QPoint(global_pos)
                    return False
            elif et == QEvent.Type.MouseButtonDblClick and event.button() == Qt.MouseButton.LeftButton:
                if self._is_media_surface(obj):
                    # Fotoğraf/video üzerinde çift tık TAM EKRAN'dır: yüzey kendi `double_clicked`'ını yayınlar. Burada "başlık
                    # çubuğu çift tıkı" sayılıp pencere büyütülürse olay yutulur, ilk tıkın (anında) duraklatması da geri alınmazdı.
                    return super().eventFilter(obj, event)
                if self._is_top_drag_strip(local) or self._in_drag_region(global_pos):
                    self.toggle_maximize()
                    return True
        return super().eventFilter(obj, event)


    def drag_widgets(self) -> list[QWidget]:
        return []

    def _in_interactive_child(self, global_pos: QPoint) -> bool:
        child = self.childAt(self.mapFromGlobal(global_pos))
        while child is not None and child is not self:
            if child.property("role") in _INTERACTIVE_ROLES:
                return True
            if child.objectName() in _INTERACTIVE_NAMES:
                return True
            if isinstance(child, (QPushButton, QSlider, QMenu, QComboBox, QCheckBox, QTabBar, QAbstractButton)):
                return True
            child = child.parentWidget()
        return False

    def _zoomed_canvas_at(self, global_pos: QPoint) -> bool:
        child = self.childAt(self.mapFromGlobal(global_pos))
        canvas = getattr(self, "canvas", None)
        player = getattr(self, "player", None)
        surface = getattr(player, "widget", None) if player is not None else None
        while child is not None and child is not self:
            if canvas is not None and child is canvas and not getattr(canvas, "_fitted", True):
                return True
            if surface is not None and child is surface and getattr(surface, "zoomed", lambda: False)():
                return True
            child = child.parentWidget()
        return False

    def _in_drag_region(self, global_pos: QPoint) -> bool:
        if QApplication.activePopupWidget() is not None:
            return False
        # Tam ekran veya büyütülmüşse taşıma tamamen devre dışı
        if self.isFullScreen() or self.isMaximized():
            return False

        top_widget = QApplication.widgetAt(global_pos)

        if top_widget is not None:
            top_win = top_widget.window()
            if top_win is not None and top_win is not self:
                return False
            curr = top_widget
            while curr is not None and curr is not self:
                if isinstance(curr, (QPushButton, QSlider, QMenu, QComboBox, QCheckBox, QTabBar, QAbstractButton)):
                    return False
                if curr.property("role") in _INTERACTIVE_ROLES:
                    return False
                if curr.objectName() in _INTERACTIVE_NAMES:
                    return False
                curr = curr.parentWidget()

        if self._edge_at(self.mapFromGlobal(global_pos)):
            return False
        if self._in_interactive_child(global_pos):
            return False
        if self._media_content_at(global_pos):
            return False
        return True

    def _is_media_surface(self, obj) -> bool:
        """Olayın hedefi fotoğraf (PhotoCanvas ve görünümü) ya da video yüzeyi (ya da onların çocuğu) mü?"""
        player = getattr(self, "player", None)
        surface = getattr(player, "widget", None) if player is not None else None
        canvas = getattr(self, "canvas", None)
        viewport = canvas.viewport() if canvas is not None and hasattr(canvas, "viewport") else None
        curr = obj
        while isinstance(curr, QWidget) and curr is not self:
            if curr is surface or curr is canvas or curr is viewport:
                return True
            curr = curr.parentWidget()
        return False

    def _media_content_at(self, global_pos: QPoint) -> bool:
        """Yakınlaştırılmış medyada taşıma başlamasın; sığdır/doldurda sürükleme serbest."""
        player = getattr(self, "player", None)
        surface = getattr(player, "widget", None) if player is not None else None
        stage = getattr(self, "stage", None)
        if stage is not None and surface is not None:
            if stage.stack.currentWidget() is surface:
                if getattr(surface, "zoomed", lambda: False)():
                    return True
                return False

        child = self.childAt(self.mapFromGlobal(global_pos))
        canvas = getattr(self, "canvas", None)
        while child is not None and child is not self:
            if surface is not None and (child is surface or child.parentWidget() is surface):
                if getattr(surface, "zoomed", lambda: False)():
                    return True
                return False
            # Canvas: sığdır/doldur modunda taşımayı serbest bırak
            if canvas is not None and (child is canvas or child is canvas.viewport()):
                if getattr(canvas, "_fitted", True):
                    # Sığdır modunda canvas tıklanmış: taşımaya izin ver
                    return False
                pixmap = canvas.pixmap_item.pixmap()
                if pixmap.isNull():
                    return False
                viewport_pos = canvas.viewport().mapFromGlobal(global_pos)
                return canvas.pixmap_item.contains(canvas.mapToScene(viewport_pos))
            child = child.parentWidget()
        return False



    def _is_top_drag_strip(self, local: QPoint) -> bool:
        if self.isMaximized() or self.isFullScreen():
            return False
        return (
            local.y() <= self.BORDER
            and self.CORNER < local.x() < self.width() - self.CORNER
            and not self._in_interactive_child(self.mapToGlobal(local))
        )

    def _edge_at(self, local: QPoint) -> Qt.Edge | None:
        if self.isMaximized() or self.isFullScreen():
            return None
        x, y = local.x(), local.y()
        w, h = self.width(), self.height()
        c = self.CORNER
        b = self.BORDER
        if x <= c and y <= c:
            return Qt.Edge.TopEdge | Qt.Edge.LeftEdge
        if x >= w - c and y <= c:
            return Qt.Edge.TopEdge | Qt.Edge.RightEdge
        if x <= c and y >= h - c:
            return Qt.Edge.BottomEdge | Qt.Edge.LeftEdge
        if x >= w - c and y >= h - c:
            return Qt.Edge.BottomEdge | Qt.Edge.RightEdge
        edge = Qt.Edge(0)
        if x <= b:
            edge |= Qt.Edge.LeftEdge
        elif x >= w - b:
            edge |= Qt.Edge.RightEdge
        if y <= b:
            edge |= Qt.Edge.TopEdge
        elif y >= h - b:
            edge |= Qt.Edge.BottomEdge
        return edge or None

    def _refresh_cursor(self, local: QPoint) -> None:
        if self._resize_edge is not None:
            return
        edge = self._edge_at(local)
        if edge:
            self.setCursor(_EDGE_CURSORS.get(edge, Qt.CursorShape.ArrowCursor))
            return
        if self._in_interactive_child(self.mapToGlobal(local)):
            self.unsetCursor()
            return
        self.unsetCursor()

    def _try_begin_resize(self, local: QPoint, global_pos: QPoint) -> bool:
        edge = self._edge_at(local)
        if not edge:
            return False
        handle = self.windowHandle()
        if handle is not None:
            try:
                if handle.startSystemResize(edge):
                    return True
            except Exception:
                pass
        self._begin_manual_resize(edge, global_pos)
        return True

    def _begin_manual_resize(self, edge: Qt.Edge, global_pos: QPoint) -> None:
        self._resize_edge = edge
        self._resize_origin = QPoint(global_pos)
        self._resize_geom = QRect(self.geometry())
        self.setCursor(_EDGE_CURSORS.get(edge, Qt.CursorShape.SizeAllCursor))
        self.grabMouse()

    def _apply_manual_resize(self, global_pos: QPoint) -> None:
        if self._resize_edge is None:
            return
        dx = global_pos.x() - self._resize_origin.x()
        dy = global_pos.y() - self._resize_origin.y()
        g = QRect(self._resize_geom)
        min_w = max(160, self.minimumWidth())
        min_h = max(120, self.minimumHeight())
        edge = self._resize_edge
        if edge & Qt.Edge.LeftEdge:
            width = max(min_w, g.width() - dx)
            g.setX(g.x() + g.width() - width)
            g.setWidth(width)
        elif edge & Qt.Edge.RightEdge:
            g.setWidth(max(min_w, g.width() + dx))
        if edge & Qt.Edge.TopEdge:
            height = max(min_h, g.height() - dy)
            g.setY(g.y() + g.height() - height)
            g.setHeight(height)
        elif edge & Qt.Edge.BottomEdge:
            g.setHeight(max(min_h, g.height() + dy))
        self.setGeometry(g)

    def _end_manual_resize(self) -> None:
        if self._resize_edge is None:
            return
        self._resize_edge = None
        self.releaseMouse()
        self.unsetCursor()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if self.isFullScreen():
            super().mousePressEvent(event)
            return
        if event.button() == Qt.MouseButton.LeftButton:
            local = event.position().toPoint()
            global_pos = event.globalPosition().toPoint()
            if self._try_begin_resize(local, global_pos):
                return
            if self._in_drag_region(global_pos):
                self._pending_move = QPoint(global_pos)
                return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        if self.isFullScreen():
            super().mouseDoubleClickEvent(event)
            return
        local = event.position().toPoint()
        global_pos = event.globalPosition().toPoint()
        if event.button() == Qt.MouseButton.LeftButton and (
            self._is_top_drag_strip(local) or self._in_drag_region(global_pos)
        ):
            self.toggle_maximize()
            return
        super().mouseDoubleClickEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self.isFullScreen():
            super().mouseMoveEvent(event)
            return
        if self._resize_edge is not None:
            self._apply_manual_resize(event.globalPosition().toPoint())
            return
        global_pos = event.globalPosition().toPoint()
        if self._move_origin is not None:
            self._apply_manual_move(global_pos)
            return
        if self._pending_move is not None:
            if (global_pos - self._pending_move).manhattanLength() >= 8:
                origin = self._pending_move
                self._pending_move = None
                self._start_drag(origin, global_pos)
                return
        self._refresh_cursor(event.position().toPoint())
        super().mouseMoveEvent(event)


    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self._resize_edge is not None:
            self._end_manual_resize()
            return
        if event.button() == Qt.MouseButton.LeftButton and self._move_origin is not None:
            self._end_manual_move()
            return
        self._pending_move = None
        super().mouseReleaseEvent(event)

    def toggle_maximize(self) -> None:
        from PyQt6.QtWidgets import QApplication

        screen = self.screen() or QApplication.primaryScreen()
        avail = screen.availableGeometry() if screen is not None else QRect(0, 0, 1920, 1080)

        if self.isMaximized() or self._is_snapped:
            self._is_snapped = None
            self.showNormal()
            if self._normal_geom and not self._normal_geom.isEmpty():
                self.setGeometry(self._normal_geom)
            else:
                g = self.geometry()
                if g.width() >= avail.width() - 30 and g.height() >= avail.height() - 30:
                    nw = int(avail.width() * 0.72)
                    nh = int(avail.height() * 0.72)
                    nx = avail.x() + (avail.width() - nw) // 2
                    ny = avail.y() + (avail.height() - nh) // 2
                    self.setGeometry(nx, ny, nw, nh)
        else:
            self._normal_geom = QRect(self.geometry())
            g = self.geometry()
            # If window is already nearly the size of screen, toggle it down to normal floating size
            if g.width() >= avail.width() - 30 and g.height() >= avail.height() - 30:
                nw = int(avail.width() * 0.72)
                nh = int(avail.height() * 0.72)
                nx = avail.x() + (avail.width() - nw) // 2
                ny = avail.y() + (avail.height() - nh) // 2
                self.setGeometry(nx, ny, nw, nh)
                return
            self.showMaximized()

    def snap_to(self, side: str, screen=None) -> None:
        """Pencereyi ekranın sol/sağ yarısına veya tepesine yasla (Aero Snap)."""
        from PyQt6.QtWidgets import QApplication

        if screen is None:
            screen = self.screen() or QApplication.primaryScreen()
        if not screen:
            return
        avail = screen.availableGeometry()
        if not self.isMaximized() and not self._is_snapped:
            self._normal_geom = QRect(self.geometry())
        self._is_snapped = side
        if self.isMaximized():
            self.showNormal()
        if side == "left":
            half_w = avail.width() // 2
            self.setGeometry(avail.x(), avail.y(), half_w, avail.height())
        elif side == "right":
            half_w = avail.width() // 2
            self.setGeometry(avail.x() + half_w, avail.y(), avail.width() - half_w, avail.height())
        elif side == "top":
            self._is_snapped = None
            self.showMaximized()

    def _check_snap_after_move(self) -> None:
        """Taşıma işlemi bittiğinde pencere/imleç ekran kenarındaysa yarı ekrana yasla."""
        from PyQt6.QtGui import QCursor
        from PyQt6.QtWidgets import QApplication

        cursor_pos = QCursor.pos()
        screen = QApplication.screenAt(cursor_pos) or self.screen() or QApplication.primaryScreen()
        if not screen:
            return
        screen_geo = screen.geometry()
        cx, cy = cursor_pos.x(), cursor_pos.y()
        threshold = 12
        if cx <= screen_geo.x() + threshold:
            self.snap_to("left", screen)
        elif cx >= screen_geo.right() - threshold:
            self.snap_to("right", screen)
        elif cy <= screen_geo.y() + threshold:
            self.showMaximized()

    def _start_drag(self, origin: QPoint, global_pos: QPoint) -> None:
        """Pencere taşımayı Windows Aero Snap destekli olarak başlat."""
        if self.isFullScreen():
            return
        if self.isMaximized() or self._is_snapped:
            self._unmaximize_for_drag(global_pos)
            return


        self._normal_geom = QRect(self.geometry())
        handle = self.windowHandle()
        if handle is not None:
            try:
                if handle.startSystemMove():
                    self._check_snap_after_move()
                    return
            except Exception:
                pass

        if sys.platform == "win32":
            try:
                import ctypes

                hwnd = int(self.winId())
                ctypes.windll.user32.ReleaseCapture()
                ctypes.windll.user32.SendMessageW(hwnd, 0x0112, 0xF012, 0)
                if self.pos() != self._normal_geom.topLeft():
                    self._check_snap_after_move()
                    return
            except Exception:
                pass

        self._begin_manual_move(origin, global_pos)

    def _unmaximize_for_drag(self, global_pos: QPoint) -> None:
        """Büyütülmüş veya yaslanmış pencereyi sürükleme anında imlecin altına orantılı küçült."""
        from PyQt6.QtWidgets import QApplication

        screen = QApplication.screenAt(global_pos) or self.screen() or QApplication.primaryScreen()
        avail = screen.availableGeometry() if screen is not None else QRect(0, 0, 1920, 1080)
        norm_w = self._normal_geom.width() if self._normal_geom else int(avail.width() * 0.72)
        norm_h = self._normal_geom.height() if self._normal_geom else int(avail.height() * 0.72)
        norm_w = max(400, min(norm_w, avail.width() - 40))
        norm_h = max(300, min(norm_h, avail.height() - 40))

        current_w = max(1, self.width())
        click_ratio = max(0.1, min(0.9, (global_pos.x() - self.x()) / current_w))
        new_x = int(global_pos.x() - click_ratio * norm_w)
        new_y = global_pos.y() - 10
        self._is_snapped = None
        self.showNormal()
        self.setGeometry(new_x, new_y, norm_w, norm_h)

        if sys.platform == "win32":
            try:
                import ctypes

                hwnd = int(self.winId())
                ctypes.windll.user32.ReleaseCapture()
                ctypes.windll.user32.SendMessageW(hwnd, 0x0112, 0xF012, 0)
                return
            except Exception:
                pass

        self._begin_manual_move(global_pos, global_pos)

    def _begin_manual_move(self, origin: QPoint, global_pos: QPoint) -> None:
        self._move_origin = QPoint(origin)
        self._move_geom = QRect(self.geometry())
        self.grabMouse()
        self._apply_manual_move(global_pos)

    def _apply_manual_move(self, global_pos: QPoint) -> None:
        if self._move_origin is None:
            return
        delta = global_pos - self._move_origin
        self.move(self._move_geom.topLeft() + delta)

    def _end_manual_move(self) -> None:
        self._move_origin = None
        self.releaseMouse()
        self._check_snap_after_move()
