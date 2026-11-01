"""Ana pencerenin 'lazım olunca çıkan' medya eylemleri (MainWindow'a katılan mixin).

İlke: kullanıcıyı gereksiz denetimle boğma. Her şey bağlama bağlıdır:
* ses/altyazı düğmesi yalnızca seçilecek parça varken görünür;
* "kaldığın yerden devam" yalnızca uzun videoda, kendiliğinden çalışır;
* kare kaydı yalnızca video/Canlı Fotoğraf oynarken;
* "döndürmeyi kaydet" ipucu fotoğraf döndürülünce, bir kez çıkar;
* kırpma, taşıma/kopyalama sağ tık menüsündedir.
"""

from __future__ import annotations

import os
from pathlib import Path

from PyQt6.QtCore import QPoint, QRect, QStandardPaths, Qt, QTimer
from PyQt6.QtGui import QAction, QActionGroup, QImage
from PyQt6.QtWidgets import QFileDialog, QMenu

from app.core import fileops, histogram, image_edit
from app.core.edit_worker import EditWorker
from app.core.matcher import VIDEO_EXTS
from app.core.paths import get_user_data_dir
from app.core.resume_store import ResumeStore
from app.core.tracks import needs_track_menu, next_track_id, track_label
from app.core.win_shell import reveal_in_explorer
from app.resources.locales import translate_error
from app.ui.chrome_widgets import Toast
from app.ui.crop_overlay import CropOverlay
from app.ui.live_button import _fmt_ms
from app.ui.message_box import MessageBox


class MediaActionsMixin:
    _crop_active = False
    _crop_overlay: CropOverlay | None = None
    _shown_image: tuple[str, QImage] | None = None
    _rot_hint_path: str | None = None
    _edit_token = 0

    # ------------------------------------------------------------------ kurulum
    def _init_media_actions(self) -> None:
        self._toast = Toast(self)
        self._resume = ResumeStore(get_user_data_dir() / "resume.json")
        self._resume_timer = QTimer(self)
        self._resume_timer.setInterval(10_000)
        self._resume_timer.timeout.connect(self._remember_position)
        self.player.tracks_changed.connect(self._update_tracks_button)

    # ------------------------------------------------------------------ bilgi kapsülü
    def _flash(self, text: str, action_text: str = "", action=None, msec: int = 3200, bottom: bool = False) -> None:
        """`bottom=True`: kapsül altta, kontrol çubuğunun hemen üstünde çıkar (ör. 'Kaldığın yerden devam')."""
        toast = getattr(self, "_toast", None)
        if toast is None:
            return
        anchor = QRect(self.stage.mapToGlobal(QPoint(0, 0)), self.stage.size())
        limit = None
        if bottom:
            dock = getattr(self, "_dock_host", None)
            limit = dock.geometry().top() if dock is not None and dock.isVisible() else anchor.bottom() - 24
        toast.show_message(text, anchor, action_text, action, msec, bottom_limit=limit)

    # ------------------------------------------------------------------ ses / altyazı
    def _tracks_context(self) -> bool:
        """Parça seçimi yalnızca oynatma açıkken/duraklatılmışken anlamlı."""
        return bool(
            self.current_match is not None
            and (self._playing or self._opening_play or self.player.is_paused)
        )

    def _update_tracks_button(self) -> None:
        btn = getattr(self, "btn_tracks", None)
        if btn is None:
            return
        audio, subs = self.player.track_lists() if self._tracks_context() else ([], [])
        show = needs_track_menu(audio, subs)
        if btn.isHidden() == show:  # gizliyken gösterilecek ya da görünürken gizlenecek
            btn.setVisible(show)
            if self.isFullScreen():
                self._layout_overlay_dock()
            else:
                self._layout_window_dock()

    def _show_tracks_menu(self) -> None:
        audio, subs = self.player.track_lists()
        menu = QMenu(self)
        if len(audio) > 1:
            menu.addSection(self.t("audio_track"))
            group = QActionGroup(menu)
            for tr in audio:
                act = QAction(track_label(tr, self.lang, self.t("track")), menu)
                act.setCheckable(True)
                act.setChecked(bool(tr.get("selected")))
                act.triggered.connect(lambda _c=False, i=int(tr["id"]): self.player.set_audio_track(i))
                group.addAction(act)
                menu.addAction(act)
        if subs:
            menu.addSection(self.t("subtitle_track"))
            group = QActionGroup(menu)
            off = QAction(self.t("subtitle_off"), menu)
            off.setCheckable(True)
            off.setChecked(self.player.selected_track_id(subs) is None)
            off.triggered.connect(lambda _c=False: self.player.set_subtitle_track(None))
            group.addAction(off)
            menu.addAction(off)
            for tr in subs:
                act = QAction(track_label(tr, self.lang, self.t("track")), menu)
                act.setCheckable(True)
                act.setChecked(bool(tr.get("selected")))
                act.triggered.connect(lambda _c=False, i=int(tr["id"]): self.player.set_subtitle_track(i))
                group.addAction(act)
                menu.addAction(act)
        menu.addSeparator()
        menu.addAction(self.t("subtitle_open"), self._open_subtitle_file)
        top_left = self.btn_tracks.mapToGlobal(QPoint(0, 0))
        menu.exec(QPoint(top_left.x(), top_left.y() - menu.sizeHint().height() - 4))

    def _open_subtitle_file(self) -> None:
        path = self.current_file()
        start = str(path.parent) if path is not None else ""
        name, _ = QFileDialog.getOpenFileName(self, self.t("subtitle_open"), start, self.t("subtitle_filter"))
        if name:
            self.player.add_subtitle_file(name)
            self._flash(self.t("subtitle_now").format(Path(name).name))

    def _cycle_subtitle(self) -> None:
        if not self._tracks_context():
            return
        _audio, subs = self.player.track_lists()
        if not subs:
            return
        nxt = next_track_id(subs, self.player.selected_track_id(subs), allow_off=True)
        self.player.set_subtitle_track(nxt)
        label = self.t("subtitle_off")
        if nxt is not None:
            label = next((track_label(t, self.lang, self.t("track")) for t in subs if int(t["id"]) == nxt), label)
        self._flash(self.t("subtitle_now").format(label))

    def _cycle_audio(self) -> None:
        if not self._tracks_context():
            return
        audio, _subs = self.player.track_lists()
        if len(audio) < 2:
            return
        nxt = next_track_id(audio, self.player.selected_track_id(audio), allow_off=False)
        if nxt is None:
            return
        self.player.set_audio_track(nxt)
        label = next((track_label(t, self.lang, self.t("track")) for t in audio if int(t["id"]) == nxt), str(nxt))
        self._flash(self.t("audio_now").format(label))

    # ------------------------------------------------------------------ kaldığın yerden devam
    def _resume_enabled(self) -> bool:
        return self._setting_bool("resume_video", True)

    def _set_resume_enabled(self, enabled: bool) -> None:
        self.settings.setValue("resume_video", bool(enabled))
        if not enabled:
            self._resume_timer.stop()

    def _standalone_video_path(self) -> Path | None:
        m = self.current_match
        if m is None or m.source != "video":
            return None
        return Path(m.video_path)

    def _remember_position(self) -> None:
        """Uzun bağımsız videoda konumu kaydet (duraklatma, gezinme, kapanış, 10 sn'de bir)."""
        if not self._resume_enabled():
            return
        path = self._standalone_video_path()
        if path is None:
            return
        dur = self.player.duration()
        if dur <= 0:
            return
        self._resume.update(path, self.player.position(), dur)

    def _apply_resume(self) -> bool:
        """Video açılınca kayıtlı konuma git ve gerekirse 'Baştan' seçeneği sun."""
        if not self._resume_enabled():
            return False
        path = self._standalone_video_path()
        if path is None:
            return False
        pos = self._resume.get(path)
        if not pos:
            return False
        self.player.seek(pos)
        self._flash(
            self.t("resume_from").format(_fmt_ms(pos)),
            self.t("resume_restart"),
            lambda: self.player.seek(0),
            msec=5000,
            bottom=True,
        )
        return True

    # ------------------------------------------------------------------ kare kaydet
    def _frame_save_dir(self, path: Path) -> Path:
        if os.access(path.parent, os.W_OK):
            return path.parent
        pics = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.PicturesLocation)
        return Path(pics) if pics else Path.home()

    def _save_frame(self) -> None:
        """Geçerli kareyi PNG olarak videonun yanına kaydet (kısayol: S)."""
        if not self._video_can_seek():
            return
        path = self.current_file()
        if path is None:
            return
        ms = self.player.position()
        h, rem = divmod(ms, 3_600_000)
        m, rem = divmod(rem, 60_000)
        s, milli = divmod(rem, 1000)
        stamp = (f"{h}-" if h else "") + f"{m:02d}-{s:02d}-{milli:03d}"
        dest = image_edit.unique_path(self._frame_save_dir(path), f"{path.stem}_{stamp}", ".png")
        if self.player.save_frame(dest):
            self._flash(
                self.t("frame_saved").format(dest.name),
                self.t("show"),
                lambda d=dest: reveal_in_explorer(d),
            )
        else:
            self._flash(self.t("frame_save_fail"))

    # ------------------------------------------------------------------ histogram
    def _update_histogram(self) -> None:
        details = getattr(self, "details", None)
        if details is None or not self._details_on:
            return
        path = self.current_file()
        shown = self._shown_image
        is_video = path is not None and path.suffix.lower() in VIDEO_EXTS
        display = getattr(self, "_display_path", None)
        if is_video or shown is None or display is None or shown[0] != str(display):
            details.set_histogram(None)
            return
        try:
            small = shown[1].scaled(
                512, 512, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.FastTransformation
            )
            details.set_histogram(histogram.compute(histogram.qimage_to_rgb(small), max_side=512))
        except Exception:  # noqa: BLE001 — histogram yardımcı bilgidir; hata arayüzü bozmasın
            details.set_histogram(None)

    def _remember_shown_image(self, path: str, qimage: QImage) -> None:
        self._shown_image = (str(path), qimage)
        self._update_histogram()

    # ------------------------------------------------------------------ döndürmeyi kaydet
    def _can_save_rotation(self) -> bool:
        path = self.current_file()
        return bool(
            path is not None
            and path.is_file()
            and path.suffix.lower() not in VIDEO_EXTS
            and not self._playing
            and not self.canvas.pixmap_item.pixmap().isNull()
            and (self.canvas._rotation % 360) != 0
            and image_edit.can_rotate_save(path)  # en son: PNG/WebP/TIFF'te dosyayı açıp denetler
        )

    def _maybe_hint_rotate_save(self) -> None:
        """Fotoğraf ilk kez döndürülünce (görünüm) 'Kaydet' önerisi — bir fotoğraf için bir kez."""
        path = self.current_file()
        if path is None or not self._can_save_rotation() or self._rot_hint_path == str(path):
            return
        self._rot_hint_path = str(path)
        self._flash(self.t("rotate_hint"), self.t("rotate_save"), self._save_rotation, msec=4500)

    def _next_edit_token(self) -> int:
        self._edit_token += 1
        return self._edit_token

    def _save_rotation(self) -> None:
        path = self.current_file()
        deg = self.canvas._rotation % 360
        if path is None or not deg or not image_edit.can_rotate_save(path):
            return
        token = self._next_edit_token()
        worker = EditWorker(lambda: image_edit.rotate_and_save(path, deg), token)
        worker.signals.done.connect(lambda res, err, tok, p=path: self._on_rotation_saved(err, tok, p))
        self.pool.start(worker)

    def _on_rotation_saved(self, err: str, _token: int, path: Path) -> None:
        if err:
            MessageBox.warning(self, self.t("error"), self.t("rotate_fail").format(translate_error(err, self.lang)))
            return
        self._flash(self.t("rotate_saved"))
        if self.current_file() == path:
            self.display_current()  # dosyanın yeni yönüyle yeniden yükle (görünüm döndürmesi sıfırlanır)

    # ------------------------------------------------------------------ kırp
    def _can_crop(self) -> bool:
        path = self.current_file()
        return bool(
            path is not None
            and path.is_file()
            and path.suffix.lower() not in VIDEO_EXTS
            and path.suffix.lower() not in (".gif", ".livp")  # .livp bir arşiv: kırpma onu görüntü olarak açamaz
            and not self._playing
            and not self.player.is_paused
            and not self.canvas.pixmap_item.pixmap().isNull()
            and not self._crop_active
        )

    def _start_crop(self) -> None:
        if not self._can_crop():
            return
        self._stop_slideshow()
        self.canvas.reset_view_to_fit()
        self._crop_active = True
        labels = {
            "free": self.t("crop_free"),
            "cancel": self.t("crop_cancel"),
            "save": self.t("crop_save"),
        }
        self._crop_overlay = CropOverlay(self.canvas, labels)
        self._crop_overlay.finished.connect(self._finish_crop)

    def _cancel_crop(self) -> None:
        if self._crop_active:
            self._finish_crop(None)

    def _finish_crop(self, frac) -> None:
        overlay, self._crop_overlay = self._crop_overlay, None
        self._crop_active = False
        if overlay is not None:
            overlay.hide()
            overlay.deleteLater()
        self.canvas.setFocus()
        src = self.current_file()
        if frac is None or src is None:
            return
        rotation = self.canvas._rotation % 360
        has_alpha = self.canvas.pixmap_item.pixmap().hasAlphaChannel()
        suffix = image_edit.output_suffix(src, has_alpha)
        dest = image_edit.unique_path(self._frame_save_dir(src), f"{src.stem} ({self.t('crop_suffix')})", suffix)
        self._flash(self.t("crop_saving"), msec=15_000)
        token = self._next_edit_token()
        worker = EditWorker(lambda: image_edit.crop_and_save(src, tuple(frac), rotation, dest), token)
        worker.signals.done.connect(lambda res, err, tok, d=dest: self._on_crop_saved(err, d))
        self.pool.start(worker)

    def _on_crop_saved(self, err: str, dest: Path) -> None:
        if err:
            self._toast.hide()
            MessageBox.warning(self, self.t("error"), self.t("crop_fail").format(translate_error(err, self.lang)))
            return
        self._flash(
            self.t("crop_saved").format(dest.name),
            self.t("show"),
            lambda d=dest: reveal_in_explorer(d),
            msec=5000,
        )

    # ------------------------------------------------------------------ taşı / kopyala
    def _move_to_folder(self) -> None:
        self._transfer_to_folder(move=True)

    def _copy_to_folder(self) -> None:
        self._transfer_to_folder(move=False)

    def _transfer_to_folder(self, move: bool) -> None:
        path = self.current_file()
        if path is None or not path.is_file():
            return
        start = str(path.parent)
        chosen = QFileDialog.getExistingDirectory(self, self.t("move_pick" if move else "copy_pick"), start)
        if not chosen:
            return
        dest_dir = Path(chosen)
        try:
            if dest_dir.resolve() == path.parent.resolve():
                return
        except OSError:
            return
        sources = self._delete_targets(path)  # eşlenik dosyalarla (HEIC + MOV) birlikte
        if move:
            self._stop_playback(release=True)
            self.canvas.stop_gif()
        try:
            done = fileops.transfer(sources, dest_dir, move)
        except Exception as exc:  # noqa: BLE001
            MessageBox.warning(self, self.t("error"), self.t("move_fail").format(exc))
            return
        name = done[0][1].name if done else path.name
        if move:
            self._drop_from_listing([src for src, _dst in done])
            self._flash(
                self.t("moved").format(name),
                self.t("undo_move"),
                lambda d=done: fileops.undo_move(d),
                msec=6000,
            )
        else:
            self._flash(
                self.t("copied").format(name),
                self.t("show"),
                lambda d=done[0][1]: reveal_in_explorer(d),
            )

