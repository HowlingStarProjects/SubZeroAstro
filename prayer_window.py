"""prayer_window.py - Help > Weather Prayer: the prayer of the day, and a box to send in your own."""
from __future__ import annotations

import json
import threading
import time

from PySide6.QtCore import QObject, QPropertyAnimation, QSettings, Qt, QTimer, Signal
from PySide6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QFormLayout, QGraphicsOpacityEffect,
                               QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QPushButton, QVBoxLayout)

import prayers

REFRESH_EVERY = 12 * 3600      # seconds before the approved list is downloaded again


def _cached(settings) -> dict:
    try:
        return prayers.clean_data(json.loads(settings.value("prayers/list", "") or "{}"))
    except (ValueError, TypeError):
        return {"prayers": [], "send": None}


def cached_list(settings) -> list[dict]:
    return _cached(settings)["prayers"]


def send_setup(settings) -> dict | None:
    return _cached(settings)["send"]


def refresh_list(settings, force=False) -> bool:
    """Download the approved list (call from a background thread). True when a new list was saved."""
    try:
        last = float(settings.value("prayers/fetched", 0) or 0)
    except (TypeError, ValueError):
        last = 0.0
    if not force and time.time() - last < REFRESH_EVERY:
        return False
    try:
        got = prayers.fetch_list()
    except Exception:                       # noqa: BLE001 - offline: keep what we have
        return False
    settings.setValue("prayers/fetched", time.time())
    if got["prayers"] or got["send"]:
        settings.setValue("prayers/list", json.dumps(got))
        return True
    return False


def todays_prayer(settings) -> dict:
    return prayers.prayer_of_the_day(cached_list(settings))


def by_line(p: dict) -> str:
    return f"\N{EM DASH} {p['by']}" if p.get("by") else ""


class _Relay(QObject):
    done = Signal(bool, str)
    refreshed = Signal()


class SendPrayerDialog(QDialog):
    """Write a Weather Prayer and send it to Brian (it shows only once he has picked it)."""

    def __init__(self, parent, settings, app_version=""):
        super().__init__(parent)
        self.settings = settings
        self.app_version = app_version
        self.setWindowTitle("Send In Your Own Weather Prayer")
        self.setMinimumWidth(520)
        lay = QVBoxLayout(self)
        intro = QLabel("Write your own Weather Prayer for clear skies. Brian reads every one, and the "
                       "favourites take a turn as the Weather Prayer of the day, here in SubZeroAstro and on "
                       "subzeroastro.com.")
        intro.setWordWrap(True)
        lay.addWidget(intro)
        self.text = QPlainTextEdit()
        self.text.setPlaceholderText("Dear God, ...")
        self.text.setMinimumHeight(150)
        lay.addWidget(self.text)
        self.count = QLabel()
        self.count.setAlignment(Qt.AlignRight)
        lay.addWidget(self.count)
        form = QFormLayout()
        self.name = QLineEdit()
        self.name.setPlaceholderText("e.g. Dave (leave empty to stay anonymous)")
        self.name.setMaxLength(60)
        self.place = QLineEdit()
        self.place.setPlaceholderText("e.g. Texas")
        self.place.setMaxLength(60)
        form.addRow("Your first name:", self.name)
        form.addRow("Where you are:", self.place)
        lay.addLayout(form)
        self.ok_to_show = QCheckBox("OK to show my prayer, first name and place in SubZeroAstro and on subzeroastro.com")
        self.ok_to_show.setChecked(True)
        lay.addWidget(self.ok_to_show)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(self.status)
        self.buttons = QDialogButtonBox()
        self.send_btn = self.buttons.addButton("Send My Prayer", QDialogButtonBox.AcceptRole)
        self.close_btn = self.buttons.addButton("Cancel", QDialogButtonBox.RejectRole)
        self.send_btn.clicked.connect(self.send)
        self.close_btn.clicked.connect(self.reject)
        lay.addWidget(self.buttons)
        self.relay = _Relay(self)
        self.relay.done.connect(self._sent)
        self.text.textChanged.connect(self._count)
        self._count()
        self.relay.refreshed.connect(self._check_on)
        if not self._check_on():                # not switched on (or just switched on): ask the internet again
            relay = self.relay

            def ask():
                refresh_list(QSettings("FITSBlasterWin", "FITSBlasterWin"), True)
                try:
                    relay.refreshed.emit()
                except RuntimeError:            # the box was closed meanwhile
                    pass
            threading.Thread(target=ask, daemon=True).start()

    def _check_on(self) -> bool:
        on = prayers.submit_hook is not None or send_setup(self.settings) is not None
        if self.status.text() in ("", self._OFF) or on:
            if not on:
                self._say(self._OFF, bad=True)
            elif self.status.text() == self._OFF:
                self.status.setText("")
        self.send_btn.setEnabled(on and not self.text.isReadOnly())
        return on

    _OFF = "Sending prayers isn't switched on yet - please try again in a few days."

    def _count(self):
        n = len(self.text.toPlainText().strip())
        self.count.setText(f"{n} / {prayers.MAX_LEN} letters")

    def _say(self, msg, bad=False):
        self.status.setText(msg)
        self.status.setStyleSheet("color:#d9534f" if bad else "color:#3c9d5d")

    def send(self):
        problem = prayers.check_prayer(self.text.toPlainText())
        if problem:
            self._say(problem, bad=True)
            return
        try:
            last = float(self.settings.value("prayers/last_sent", 0) or 0)
        except (TypeError, ValueError):
            last = 0.0
        wait = prayers.WAIT_BETWEEN - (time.time() - last)
        if wait > 0:
            self._say(f"Thanks for your enthusiasm! Please wait {int(wait // 60) + 1} more minutes before "
                      "sending another prayer.", bad=True)
            return
        self.send_btn.setEnabled(False)
        self._say("Sending your prayer...")
        args = (self.text.toPlainText(), self.name.text(), self.place.text(), self.ok_to_show.isChecked(),
                self.app_version, send_setup(self.settings))

        relay = self.relay

        def work():
            try:
                ok, msg = prayers.submit(*args)
            except Exception as e:          # noqa: BLE001
                ok, msg = False, f"Your prayer couldn't be sent ({e})."
            try:
                relay.done.emit(ok, msg)
            except RuntimeError:                # the box was closed meanwhile
                pass
        threading.Thread(target=work, daemon=True).start()

    def _sent(self, ok, msg):
        self.last_result = (ok, msg)
        self._say(msg, bad=not ok)
        if ok:
            self.settings.setValue("prayers/last_sent", time.time())
            self.text.setReadOnly(True)
            self.close_btn.setText("Close")
        else:
            self.send_btn.setEnabled(True)


class PrayerDialog(QDialog):
    """The Weather Prayer of the day first, then the others in turn every ROTATE_S seconds (with back/next
    arrows), and a link to send in your own."""

    ROTATE_S = 30

    def __init__(self, parent, settings, app_version=""):
        super().__init__(parent)
        self.settings = settings
        self.app_version = app_version
        self.setWindowTitle("Weather Prayer")
        self.setMinimumWidth(520)
        self.prayers = cached_list(settings) or [prayers.BUILT_IN]
        today = todays_prayer(settings)
        self.today_index = next((i for i, p in enumerate(self.prayers) if p is today or p == today), 0)
        self.index = self.today_index
        lay = QVBoxLayout(self)
        top = QHBoxLayout()
        self.head = QLabel()
        top.addWidget(self.head)
        top.addStretch(1)
        self.prev_btn = QPushButton("\N{BLACK LEFT-POINTING TRIANGLE}")
        self.next_btn = QPushButton("\N{BLACK RIGHT-POINTING TRIANGLE}")
        for btn, tip, step in ((self.prev_btn, "Previous prayer", -1), (self.next_btn, "Next prayer", 1)):
            btn.setFixedWidth(34)
            btn.setToolTip(tip)
            btn.setAutoDefault(False)
            btn.clicked.connect(lambda _=False, d=step: self.step(d, by_hand=True))
            btn.setVisible(len(self.prayers) > 1)
            top.addWidget(btn)
        lay.addLayout(top)
        self.prayer_label = QLabel()
        self.prayer_label.setWordWrap(True)
        self.prayer_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.prayer_label.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        lay.addWidget(self.prayer_label)
        self.by_label = QLabel()
        self.by_label.setAlignment(Qt.AlignRight)
        self.by_label.setStyleSheet("font-style:italic")
        lay.addWidget(self.by_label)
        row = QHBoxLayout()
        self.send_link = QPushButton("Send in your own Weather Prayer and see it here\N{HORIZONTAL ELLIPSIS}")
        self.send_link.setFlat(True)
        self.send_link.setAutoDefault(False)
        self.send_link.setCursor(Qt.PointingHandCursor)
        self.send_link.setStyleSheet("color:#4a90d9; text-decoration:underline; text-align:left")
        self.send_link.clicked.connect(self.open_send)
        row.addWidget(self.send_link)
        row.addStretch(1)
        ok = QPushButton("Amen")
        ok.setDefault(True)
        ok.clicked.connect(self.accept)
        row.addWidget(ok)
        lay.addLayout(row)
        self.send_dialog = None
        # room for the longest prayer, so the box doesn't jump in size as they change
        fm = self.prayer_label.fontMetrics()
        self.prayer_label.setMinimumHeight(max(fm.boundingRect(0, 0, 480, 4000, Qt.TextWordWrap, p["text"]).height()
                                               for p in self.prayers) + 4)
        self.opacity = QGraphicsOpacityEffect(self.prayer_label)
        self.opacity.setOpacity(1.0)
        self.prayer_label.setGraphicsEffect(self.opacity)
        self.fade = QPropertyAnimation(self.opacity, b"opacity", self)
        self.fade.setDuration(450)
        self.timer = QTimer(self)
        self.timer.setInterval(self.ROTATE_S * 1000)
        self.timer.timeout.connect(lambda: self.step(1))
        self._show()
        if len(self.prayers) > 1:
            self.timer.start()

    def _show(self):
        p = self.prayers[self.index]
        self.prayer_label.setText(p["text"])
        self.by_label.setText(by_line(p))
        self.by_label.setVisible(bool(p.get("by")))
        n = len(self.prayers)
        title = "Today's Weather Prayer" if self.index == self.today_index else "Weather Prayer"
        self.head.setText(f"<b>{title}</b>" + (f" &nbsp;<span style='color:gray'>{self.index + 1} of {n}</span>"
                                               if n > 1 else ""))

    def step(self, d=1, by_hand=False):
        """Show the next (d=1) or previous (d=-1) prayer, fading it in."""
        if len(self.prayers) < 2:
            return
        self.index = (self.index + d) % len(self.prayers)
        self._show()
        self.fade.stop()
        self.fade.setStartValue(0.0)
        self.fade.setEndValue(1.0)
        self.fade.start()
        if by_hand:
            self.timer.start()          # a full 30 s to read the one you picked

    def text(self) -> str:
        return self.prayer_label.text()

    def open_send(self):
        d = SendPrayerDialog(self, self.settings, self.app_version)
        d.setAttribute(Qt.WA_DeleteOnClose)
        d.open()
        self.send_dialog = d
        return d
