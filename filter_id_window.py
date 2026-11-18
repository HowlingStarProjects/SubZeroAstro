"""Frame › Identify Filter…: the Filter Identifier window - which filter each sub was taken through, worked out from
the picture (see filter_id.py), with buttons to use it as the filter name or write it into the files."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QDialog, QHBoxLayout, QHeaderView, QLabel,
                               QMessageBox, QProgressBar, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout)

import filter_id as F

SURE_COLOUR = {"high": "#46c85f", "medium": "#d9b45a", "low": "#e0864a", "none": "#8a93a6"}


class FilterIdDialog(QDialog):
    def __init__(self, owner, uids):
        super().__init__(owner)
        self.owner = owner
        self.uids = list(uids)
        self.rows = []                         # [(uid, Measure, Result)]
        self.job = None
        self.setWindowTitle("Filter Identifier")
        lay = QVBoxLayout(self)
        intro = QLabel(
            "Works out which filter each sub was taken through <b>from the picture itself</b> - the sky's and the "
            "stars' colour, how bright the sky is and where the focuser was - not from the FILTER header. Handy "
            "when a filter drawer meant the camera never knew. Raw subs from colour cameras; tested with the "
            "ASI2600MC (IMX571 - and the IMX455 of the 6200MC is the same design). <i>Very likely</i> means it "
            "matches one filter clearly; <i>Likely</i> names the look-alikes too.")
        intro.setWordWrap(True)
        lay.addWidget(intro)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Frame", "FILTER in the file", "Identified as", "How sure", "Why"])
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.verticalHeader().setVisible(False)
        hh = self.table.horizontalHeader()
        for c in (0, 1, 2, 3):
            hh.setSectionResizeMode(c, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(4, QHeaderView.Stretch)
        lay.addWidget(self.table, 1)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        lay.addWidget(self.status)
        self.bar = QProgressBar()
        lay.addWidget(self.bar)
        h = QHBoxLayout()
        self.b_use = QPushButton("Use as Filter Name")
        self.b_use.setToolTip("SubZeroAstro groups, stacks and matches flats by the identified filter - the files "
                              "are not changed (like Frame › Set Filter Name)")
        self.b_use.clicked.connect(lambda: self.use_names())
        self.b_write = QPushButton("Write FILTER Into the Files…")
        self.b_write.setToolTip("Writes the identified filter into each FITS file's FILTER keyword, so every program "
                                "sees it - the old value is kept for Frame › Undo Last Header Change")
        self.b_write.clicked.connect(lambda: self.write_files())
        self.b_teach = QPushButton("Teach It From Frames With a Known Filter")
        self.b_teach.setToolTip("Frames whose FILTER header is right become references for your camera and rig, so "
                                "it gets better at your own filters (they're kept on this computer)")
        self.b_teach.clicked.connect(lambda: self.teach())
        self.b_stop = QPushButton("Stop")
        self.b_stop.clicked.connect(lambda: setattr(self.job, "stop", True) if self.job else None)
        b_close = QPushButton("Close")
        b_close.clicked.connect(self.close)
        for b in (self.b_use, self.b_write, self.b_teach, self.b_stop):
            h.addWidget(b)
        h.addStretch(1)
        h.addWidget(b_close)
        lay.addLayout(h)
        for b in self.findChildren(QPushButton):
            b.setAutoDefault(False)
        self.resize(1150, 560)
        self._buttons(False)

    # ------------------------------------------------------------------ running
    def _buttons(self, done):
        for b in (self.b_use, self.b_write):
            b.setEnabled(done and any(r.best for _u, _m, r in self.rows))
        self.b_teach.setEnabled(done and any(m.ok and m.header_filter for _u, m, _r in self.rows))
        self.b_stop.setVisible(not done)
        self.bar.setVisible(not done)

    def run(self, wait=False):
        from organizer_window import _Job
        frames = [(u, self.owner.frames[u].path) for u in self.uids if u in self.owner.frames]
        learned = F.load_learned()

        def work(job):
            out = []
            for i, (u, p) in enumerate(frames):
                if job.stop:
                    break
                job.note.emit(f"Measuring {Path(p).name}", i, len(frames))
                m = F.measure(p)
                out.append((u, m, F.identify(m, learned)))
            return out
        self.job = _Job(work)
        self.job.note.connect(self._note)
        self.job.ok.connect(self._done)
        self.job.failed.connect(lambda e: (self._buttons(True), self.status.setText("Stopped: " + e.split("\n")[0])))
        self.status.setText(f"Measuring {len(frames)} frame(s) - about a second each…")
        self.job.start()
        if wait:
            while self.job.isRunning():
                QApplication.processEvents()
                self.job.wait(20)
            for _ in range(5):
                QApplication.processEvents()
        return self.job

    def _note(self, text, i, n):
        self.status.setText(text)
        self.bar.setMaximum(max(n, 1))
        self.bar.setValue(i)

    def _done(self, rows):
        self.rows = rows
        self.table.setRowCount(0)
        for u, m, r in rows:
            row = self.table.rowCount()
            self.table.insertRow(row)
            sure = F.SURE_TEXT.get(r.sure, r.sure)
            for c, t in enumerate((Path(m.path).name, m.header_filter or "—", r.name, sure, r.why)):
                it = QTableWidgetItem(t)
                if c == 3:
                    it.setForeground(QColor(SURE_COLOUR.get(r.sure, "#8a93a6")))
                if c == 4:
                    it.setToolTip(r.why + ("\n\nClosest: " + ", ".join(f"{n} {s}" for n, s in r.scores)
                                           if r.scores else ""))
                self.table.setItem(row, c, it)
        found = {}
        for _u, _m, r in rows:
            if r.best:
                found[r.best] = found.get(r.best, 0) + 1
        self.status.setText("Identified: " + (", ".join(f"{n} × {k}" for k, n in sorted(found.items(),
                                                                                      key=lambda kv: -kv[1]))
                                              or "nothing") + ".  Hover a row's Why for the closest matches.")
        self._buttons(True)
        try:
            import session_log as _slog
            _slog.write("filter id", "; ".join(f"{Path(m.path).name}: {r.name} ({r.sure})" for _u, m, r in rows[:40]))
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------------ actions
    def _usable(self):
        """Rows with a filter to use: Very likely / Likely (an Unsure guess is never written without you choosing)."""
        return [(u, m, r) for u, m, r in self.rows if r.best and r.sure in ("high", "medium")]

    def use_names(self):
        by = {}
        for u, _m, r in self._usable():
            by.setdefault(r.best, []).append(u)
        for name, uids in by.items():
            self.owner.set_filter(uids, name)
        n = sum(len(v) for v in by.values())
        self.status.setText(f"Filter name set for {n} frame(s) - the files are unchanged." if n else
                            "Nothing sure enough to use.")
        return n

    def write_files(self, ask=True):
        import fits_edit as fe
        rows = [(u, r) for u, _m, r in self._usable()
                if Path(self.owner.frames[u].path).suffix.lower() in fe.FITS_EXT]
        if not rows:
            QMessageBox.information(self, "Filter Identifier", "Nothing sure enough to write (or no FITS files - "
                                    "XISF headers aren't edited).") if ask else None
            return 0
        by = {}
        for u, r in rows:
            by.setdefault(r.best, []).append(self.owner.frames[u])
        what = "\n".join(f"FILTER = {k}  →  {len(v)} file(s)" for k, v in by.items())
        if ask and QMessageBox.question(self, "Filter Identifier", f"Write into the files?\n\n{what}\n\nThe old values "
                                        "are kept for Frame › Undo Last Header Change.") != QMessageBox.Yes:
            return 0
        for name, frames in by.items():
            self.owner._write_headers(frames, {"FILTER": name})
        for u, m, _r in self.rows:                      # the table shows what the files say now
            f = self.owner.frames.get(u)
            if f is not None:
                m.header_filter = str(f.info.get("filter") or m.header_filter)
        for i, (_u, m, _r) in enumerate(self.rows):
            it = self.table.item(i, 1)
            if it is not None:
                it.setText(m.header_filter or "—")
        n = sum(len(v) for v in by.values())
        self.status.setText(f"FILTER written into {n} file(s).")
        return n

    def teach(self, ask=True):
        ms = [m for _u, m, _r in self.rows if m.ok and m.header_filter]
        n = F.teach(ms)
        if ask:
            QMessageBox.information(self, "Filter Identifier",
                                    f"Learned from {n} frame(s) with a known filter." if n else
                                    "Nothing new to learn (frames need a FILTER header, and each is learned once).")
        self.status.setText(f"Learned from {n} frame(s).")
        return n

    def closeEvent(self, ev):
        if self.job is not None and self.job.isRunning():
            self.job.stop = True
            self.job.wait(10000)
        super().closeEvent(ev)
