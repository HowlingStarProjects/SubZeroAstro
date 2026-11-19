"""Stack › Stitch a Mosaic…: pick the stacked masters of a mosaic's panels and join them into one master per filter
(see mosaic.py), then carry on in Astro Image Processing like with any target."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (QAbstractItemView, QApplication, QDialog, QFileDialog, QHBoxLayout, QHeaderView,
                               QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton,
                               QTableWidget, QTableWidgetItem, QVBoxLayout)

import fits_core as core
import mosaic as MZ
import prepare as P
import stacking as st


def describe(path):
    """(target, filter, how it will be placed) for the table, read from the header only."""
    try:
        src = st.ImageSource(path)
        look = src.look
    except Exception:  # noqa: BLE001
        return "?", "?", "can't be read"
    tgt = str(look.get("OBJECT") or Path(path).stem)
    filt = core.normalize_filter(look.get("FILTER")) if look.get("FILTER") else "—"
    if st.wcs_of(look) is not None:
        how = "plate solved"
    elif MZ.pointing(look) is not None:
        how = "to be plate solved"
    else:
        how = "by its stars"
    return tgt, filt, how


class MosaicDialog(QDialog):
    def __init__(self, owner, settings, files=None):
        super().__init__(owner)
        self.owner = owner
        self.settings = settings
        self.files = []
        self.job = None
        self.result = None
        self.setWindowTitle("Stitch a Mosaic")
        lay = QVBoxLayout(self)
        intro = QLabel(
            "Joins the panels of a mosaic into one big picture. Stack each panel as usual first (each one is its own "
            "target, like <i>M31 Panel 1</i>), then add the panel masters here - all filters at once is fine. "
            "SubZeroAstro works out where each panel sits on the sky (plate solving with ASTAP, or the stars the "
            "panels share), evens out their brightness so the joins don't show, and blends the overlaps softly. "
            "You get one master per filter, ready for Astro Image Processing.")
        intro.setWordWrap(True)
        lay.addWidget(intro)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Panel master", "Target", "Filter", "Placed by"])
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.verticalHeader().setVisible(False)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.Stretch)
        for c in (1, 2, 3):
            hh.setSectionResizeMode(c, QHeaderView.ResizeToContents)
        lay.addWidget(self.table, 1)
        h = QHBoxLayout()
        b = QPushButton("Add Panel Masters…")
        b.clicked.connect(lambda: self.pick_files())
        h.addWidget(b)
        b = QPushButton("Add a Folder…")
        b.setToolTip("Every master light in the folder and the folders inside it")
        b.clicked.connect(lambda: self.pick_folder())
        h.addWidget(b)
        b = QPushButton("Remove")
        b.clicked.connect(lambda: self.remove_selected())
        h.addWidget(b)
        h.addStretch(1)
        lay.addLayout(h)
        h = QHBoxLayout()
        h.addWidget(QLabel("Save to:"))
        self.out_edit = QLineEdit()
        self.out_edit.setPlaceholderText("a new '<target>_Mosaic' folder next to the panels' folders")
        h.addWidget(self.out_edit, 1)
        b = QPushButton("Choose…")
        b.clicked.connect(lambda: self.pick_out())
        h.addWidget(b)
        lay.addLayout(h)
        import tools
        self.astap = tools.find_astap(self.settings.value("prep/astap", "") or None) if settings is not None else None
        self.astap_label = QLabel(
            "ASTAP: <span style='color:#5fd27a'>✓ found</span> - panels without a plate solution are solved first"
            if self.astap else
            "ASTAP: <span style='color:#e0864a'>not found</span> - panels are placed by the stars they share "
            "(set ASTAP up under Settings › Your Tools for the most reliable placing)")
        lay.addWidget(self.astap_label)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        lay.addWidget(self.status)
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setVisible(False)
        lay.addWidget(self.bar)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(150)
        lay.addWidget(self.log)
        h = QHBoxLayout()
        self.b_go = QPushButton("Stitch the Mosaic")
        self.b_go.clicked.connect(lambda: self.run())
        self.b_stop = QPushButton("Stop")
        self.b_stop.setVisible(False)
        self.b_stop.clicked.connect(lambda: setattr(self.job, "stop", True) if self.job else None)
        self.b_next = QPushButton("Open in Astro Image Processing ▶")
        self.b_next.setEnabled(False)
        self.b_next.clicked.connect(lambda: self.open_next())
        b_close = QPushButton("Close")
        b_close.clicked.connect(self.close)
        for b in (self.b_go, self.b_stop, self.b_next):
            h.addWidget(b)
        h.addStretch(1)
        h.addWidget(b_close)
        lay.addLayout(h)
        for b in self.findChildren(QPushButton):
            b.setAutoDefault(False)
        self.resize(980, 620)
        if files:
            self.add(files)
        self._update()

    # ------------------------------------------------------------------ the list
    def add(self, paths):
        new = 0
        for p in P.find_masters([Path(x) for x in paths]):
            if p not in self.files:
                self.files.append(p)
                new += 1
        self._fill()
        return new

    def _fill(self):
        self.table.setRowCount(0)
        for p in self.files:
            r = self.table.rowCount()
            self.table.insertRow(r)
            for c, t in enumerate((p.name, *describe(p))):
                it = QTableWidgetItem(t)
                if c == 0:
                    it.setToolTip(str(p))
                self.table.setItem(r, c, it)
        self._update()

    def _update(self):
        n = len(self.files)
        filters = {self.table.item(r, 2).text() for r in range(self.table.rowCount()) if self.table.item(r, 2)}
        busy = bool(self.job and self.job.isRunning())
        self.b_go.setEnabled(n >= 2 and not busy)
        if not busy:
            self.status.setText("Add the masters of at least two panels." if n < 2 else
                                f"{n} masters, {len(filters)} filter(s): {', '.join(sorted(filters))}.")

    def pick_files(self):
        start = self.settings.value("stack/masters_root", "") if self.settings is not None else ""
        files, _ = QFileDialog.getOpenFileNames(self, "Pick the panel masters (all filters)", start or str(Path.home()),
                                                "Master lights (*.fits *.fit *.fts *.xisf)")
        if files:
            self.add(files)

    def pick_folder(self):
        start = self.settings.value("stack/masters_root", "") if self.settings is not None else ""
        d = QFileDialog.getExistingDirectory(self, "Folder with the panel masters", start or str(Path.home()))
        if d:
            dirs = [Path(d)] + [p for p in sorted(Path(d).rglob("*")) if p.is_dir()][:200]
            n = self.add(dirs)
            if not n:
                QMessageBox.information(self, "Stitch a Mosaic", "No master lights were found in that folder.")

    def pick_out(self):
        d = QFileDialog.getExistingDirectory(self, "Save the mosaic in", self.out_edit.text() or str(Path.home()))
        if d:
            self.out_edit.setText(d)

    def remove_selected(self):
        rows = sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True)
        for r in rows:
            if 0 <= r < len(self.files):
                self.files.pop(r)
        self._fill()

    # ------------------------------------------------------------------ running
    def run(self, wait=False):
        from organizer_window import _Job
        if len(self.files) < 2:
            return None
        files = list(self.files)
        out = self.out_edit.text().strip() or None
        astap = self.astap

        def work(job):
            return MZ.stitch(files, out_dir=out, astap_exe=astap,
                             log=lambda t: job.note.emit("LOG " + t, 0, 0),
                             progress=lambda t, f: job.note.emit(t, int(1000 * f), 1000),
                             cancel=lambda: job.stop)
        self.log.clear()
        self.result = None
        self.b_next.setEnabled(False)
        self.job = _Job(work)
        self.job.note.connect(self._note)
        self.job.ok.connect(self._done)
        self.job.failed.connect(self._failed)
        self.b_stop.setVisible(True)
        self.bar.setVisible(True)
        self.bar.setValue(0)
        self.job.start()
        self._update()
        if wait:
            while self.job.isRunning():
                QApplication.processEvents()
                self.job.wait(20)
            for _ in range(5):
                QApplication.processEvents()
        return self.job

    def _note(self, text, i, n):
        if text.startswith("LOG "):
            self.log.appendPlainText(text[4:])
            return
        self.status.setText(text)
        self.bar.setValue(i)

    def _finish(self):
        self.b_stop.setVisible(False)
        self.bar.setVisible(False)
        self._update()

    def _failed(self, err):
        self._finish()
        first = err.split("\n")[0]
        if "Cancelled" in err.split("\n\n")[-1][-300:] and not first:
            first = "Stopped."
        self.status.setText("Not stitched: " + (first or "stopped"))
        self.log.appendPlainText("Not stitched: " + (first or "stopped"))

    def _done(self, res):
        self.result = res
        self._finish()
        fl = ", ".join(res.filters)
        self.status.setText(f"Done: {res.name}, {res.panels} panels, {res.shape[1]:,} x {res.shape[0]:,} pixels "
                            f"({fl}). Saved in {Path(res.files[0]).parent}, with a log of how each panel was placed and "
                            "evened out" if res.files else "Nothing saved.")
        self.b_next.setEnabled(bool(res.files))
        try:
            import session_log as _slog
            _slog.write("mosaic", f"{res.name}: {res.panels} panels, {res.shape}, {fl}")
        except Exception:  # noqa: BLE001
            pass

    def open_next(self):
        if not (self.result and self.result.files):
            return None
        fits = [Path(p) for p in self.result.files if Path(p).suffix.lower() in (".fits", ".fit", ".xisf")]
        self.close()
        if hasattr(self.owner, "open_existing_masters"):
            return self.owner.open_existing_masters(fits)
        import prepare_window
        import ui_header
        return ui_header.open_page(self.owner, "prep_win", tuple(str(f) for f in fits),
                                   lambda: prepare_window.PrepareWindow(self.owner, self.settings, fits))
