"""squeezer_window.py - File › Fitz'r Squeezer…: tick folders of your library (or any folder), see how much room
squeezing would free, then squeeze them - or tick squeezed folders and put them back to full size."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QApplication, QButtonGroup, QDialog, QFileDialog, QHBoxLayout, QLabel, QMessageBox,
                               QProgressBar, QPushButton, QRadioButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout)

import squeezer as S

PATH_ROLE = Qt.UserRole + 1
LOADED_ROLE = Qt.UserRole + 2


def library_of(settings) -> str:
    for k in ("auto_org/dst", "organize/dst"):
        v = (settings.value(k, "") or "").strip()
        if v:
            return v
    return ""


def organize_busy(settings) -> str | None:
    """A reason to hold back while Organize Automatically is moving files into the library, else None."""
    lib = library_of(settings)
    if not lib:
        return None
    try:
        import organizer as O
        import organizer_auto as OA
        lock = O.org_base(Path(lib)) / O.LOG_DIR / "auto_organize.lock"
        if lock.exists() and time.time() - lock.stat().st_mtime < OA.LOCK_STALE_H * 3600 and OA._lock_alive(lock):
            return "Organize Automatically is filing new subs - carrying on when it has finished"
    except Exception:  # noqa: BLE001
        return None
    return None


class SqueezerDialog(QDialog):
    def __init__(self, owner, settings):
        super().__init__(owner)
        self.owner, self.settings = owner, settings
        self.setWindowTitle(S.NAME)
        self.job, self.survey_rep, self.run_rep = None, None, None
        lay = QVBoxLayout(self)
        intro = QLabel(
            "Raw subs are what fill a drive. The Fitz'r Squeezer shrinks them to about half their size or less "
            "<b>without losing a single bit</b>, using FITS's own lossless compression. The files keep their names "
            "and still open in SubZeroAstro, PixInsight, Siril, N.I.N.A. and ASTAP. Every file is checked before "
            "its original is replaced, and Unsqueeze puts files back to full size whenever you like.<br>"
            "Tick the folders below, press <b>Work Out the Savings</b>, then <b>Squeeze</b>. Stacks and processed "
            "pictures are left as they are (they hardly shrink).")
        intro.setWordWrap(True)
        intro.setTextFormat(Qt.RichText)
        lay.addWidget(intro)
        h = QHBoxLayout()
        self.r_squeeze = QRadioButton("Squeeze (make the files smaller)")
        self.r_unsqueeze = QRadioButton("Unsqueeze (back to full size)")
        self.r_squeeze.setChecked(True)
        grp = QButtonGroup(self)
        grp.addButton(self.r_squeeze)
        grp.addButton(self.r_unsqueeze)
        self.r_squeeze.toggled.connect(lambda _on: self._forget_survey())
        h.addWidget(self.r_squeeze)
        h.addWidget(self.r_unsqueeze)
        h.addStretch(1)
        lay.addLayout(h)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Folders (tick the ones to include - a ticked folder includes the ones inside)"])
        self.tree.itemExpanded.connect(self._expand)
        self.tree.itemChanged.connect(lambda *_a: self._forget_survey())
        lay.addWidget(self.tree, 1)
        h = QHBoxLayout()
        b = QPushButton("Add Another Folder…")
        b.setToolTip("A folder outside your organized library (another drive, an old archive …)")
        b.clicked.connect(lambda: self.add_root())
        h.addWidget(b)
        b = QPushButton("Remove From List")
        b.clicked.connect(self._remove_root)
        h.addWidget(b)
        h.addStretch(1)
        lay.addLayout(h)
        self.status = QLabel(settings.value("squeezer/last", "") or "")
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.RichText)
        self.status.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(self.status)
        self.bar = QProgressBar()
        self.bar.setVisible(False)
        lay.addWidget(self.bar)
        h = QHBoxLayout()
        self.b_survey = QPushButton("Work Out the Savings")
        self.b_survey.setToolTip("Read the headers in the ticked folders and squeeze a few files in memory to see how "
                                 "much room it would free. Nothing is changed.")
        self.b_survey.clicked.connect(lambda: self.work_out())
        self.b_go = QPushButton("Squeeze…")
        self.b_go.setEnabled(False)
        self.b_go.clicked.connect(lambda: self.go())
        self.b_stop = QPushButton("Stop")
        self.b_stop.setToolTip("Stops after the file it is on - every file is either fully squeezed or untouched")
        self.b_stop.setVisible(False)
        self.b_stop.clicked.connect(self._stop)
        b_close = QPushButton("Close")
        b_close.clicked.connect(self.accept)
        for b in (self.b_survey, self.b_go, self.b_stop):
            h.addWidget(b)
        h.addStretch(1)
        h.addWidget(b_close)
        lay.addLayout(h)
        for b in self.findChildren(QPushButton):
            b.setAutoDefault(False)
        self._fill_roots()
        self.resize(820, 620)

    # ---------------------------------------------------------------- the folder tree
    def roots(self) -> list:
        out = []
        lib = library_of(self.settings)
        if lib:
            import organizer as O
            base = O.org_base(Path(lib))
            out.append(str(base if base.is_dir() else Path(lib)))
        try:
            extra = json.loads(self.settings.value("squeezer/roots", "[]") or "[]")
        except (TypeError, ValueError):
            extra = []
        for p in extra:
            if p and os.path.normcase(p) not in {os.path.normcase(x) for x in out}:
                out.append(p)
        return out

    def _fill_roots(self):
        self.tree.blockSignals(True)
        self.tree.clear()
        for p in self.roots():
            it = self._item(self.tree, Path(p), top=True)
            it.setExpanded(False)
        self.tree.blockSignals(False)
        if not self.tree.topLevelItemCount():
            self.status.setText("Your organized library isn't set yet (File › Organize). Press <b>Add Another "
                                "Folder…</b> to pick a folder of subs.")

    def _item(self, parent, path: Path, top=False):
        it = QTreeWidgetItem(parent)
        it.setText(0, str(path) if top else path.name)
        it.setData(0, PATH_ROLE, str(path))
        it.setData(0, LOADED_ROLE, False)
        it.setFlags(it.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsAutoTristate)
        it.setCheckState(0, Qt.Unchecked)
        it.setChildIndicatorPolicy(QTreeWidgetItem.ShowIndicator)
        if top and not path.is_dir():
            it.setText(0, f"{path}  (can't be reached now)")
        return it

    def _expand(self, it):
        if it.data(0, LOADED_ROLE):
            return
        it.setData(0, LOADED_ROLE, True)
        p = Path(it.data(0, PATH_ROLE))
        try:
            subs = sorted((d for d in os.scandir(p) if d.is_dir() and not S._skip_dir(d.name)),
                          key=lambda d: d.name.lower())
        except OSError:
            subs = []
        self.tree.blockSignals(True)
        state = it.checkState(0)
        for d in subs:
            c = self._item(it, Path(d.path))
            c.setCheckState(0, Qt.Checked if state == Qt.Checked else Qt.Unchecked)
        if not subs:
            it.setChildIndicatorPolicy(QTreeWidgetItem.DontShowIndicator)
        self.tree.blockSignals(False)

    def checked_folders(self) -> list:
        out = []

        def walk(it):
            st = it.checkState(0)
            if st == Qt.Checked:
                out.append(Path(it.data(0, PATH_ROLE)))
            elif st == Qt.PartiallyChecked:
                for i in range(it.childCount()):
                    walk(it.child(i))
        for i in range(self.tree.topLevelItemCount()):
            walk(self.tree.topLevelItem(i))
        return out

    def tick(self, path, on=True):
        """Tick a folder (opening the tree down to it). Returns its item, or None if it isn't under a root."""
        path = Path(path)
        for i in range(self.tree.topLevelItemCount()):
            it = self.tree.topLevelItem(i)
            root = Path(it.data(0, PATH_ROLE))
            if os.path.normcase(str(root)) == os.path.normcase(str(path)):
                it.setCheckState(0, Qt.Checked if on else Qt.Unchecked)
                return it
            try:
                rel = path.relative_to(root)
            except ValueError:
                continue
            for part in rel.parts:
                self._expand(it)
                it.setExpanded(True)
                nxt = None
                for k in range(it.childCount()):
                    if it.child(k).text(0) == part:
                        nxt = it.child(k)
                        break
                if nxt is None:
                    return None
                it = nxt
            it.setCheckState(0, Qt.Checked if on else Qt.Unchecked)
            return it
        return None

    def add_root(self, path=None):
        if path is None:
            path = QFileDialog.getExistingDirectory(self, "Choose a folder of subs", str(Path.home()))
            if not path:
                return None
        path = os.path.normpath(str(path))
        try:
            extra = json.loads(self.settings.value("squeezer/roots", "[]") or "[]")
        except (TypeError, ValueError):
            extra = []
        if path not in extra:
            extra.append(path)
            self.settings.setValue("squeezer/roots", json.dumps(extra))
        self._fill_roots()
        return path

    def _remove_root(self):
        it = self.tree.currentItem()
        while it is not None and it.parent() is not None:
            it = it.parent()
        if it is None:
            return
        p = it.data(0, PATH_ROLE)
        try:
            extra = json.loads(self.settings.value("squeezer/roots", "[]") or "[]")
        except (TypeError, ValueError):
            extra = []
        if p not in extra:
            QMessageBox.information(self, self.windowTitle(), "Your organized library is always in the list.")
            return
        extra.remove(p)
        self.settings.setValue("squeezer/roots", json.dumps(extra))
        self._fill_roots()

    # ---------------------------------------------------------------- work out / squeeze
    def mode(self) -> str:
        return "squeeze" if self.r_squeeze.isChecked() else "unsqueeze"

    def _forget_survey(self):
        self.survey_rep = None
        if hasattr(self, "b_go"):
            self.b_go.setEnabled(False)
            self.b_go.setText("Squeeze…" if self.mode() == "squeeze" else "Unsqueeze…")

    def _busy(self, on):
        self.b_survey.setEnabled(not on)
        self.b_go.setEnabled(not on and bool(self.survey_rep and self.survey_rep["todo"]))
        self.b_stop.setVisible(on)
        self.bar.setVisible(on)
        self.tree.setEnabled(not on)
        self.r_squeeze.setEnabled(not on)
        self.r_unsqueeze.setEnabled(not on)

    def _note(self, text, i, n):
        self.status.setText(text)
        self.bar.setMaximum(max(n, 1))
        self.bar.setValue(i)

    def _stop(self):
        if self.job is not None:
            self.job.stop = True
            self.status.setText("Stopping after this file…")

    def _wait(self):
        while self.job is not None and self.job.isRunning():
            QApplication.processEvents()
            self.job.wait(20)
        for _ in range(5):
            QApplication.processEvents()

    def work_out(self, wait=False):
        folders = self.checked_folders()
        if not folders:
            QMessageBox.information(self, self.windowTitle(), "Tick at least one folder first.")
            return None
        mode = self.mode()
        from organizer_window import _Job
        self.survey_rep = None
        self.job = _Job(lambda job: S.survey(folders, mode, progress=lambda t, i, n: job.note.emit(t, i, n),
                                             stop=lambda: job.stop))
        self.job.note.connect(self._note)

        def done(rep):
            self.survey_rep = rep
            self._busy(False)
            self.status.setText(describe_survey(rep))
            n = len(rep["todo"])
            self.b_go.setText((f"Squeeze {n:,} Files…" if mode == "squeeze" else f"Unsqueeze {n:,} Files…")
                              if n else ("Squeeze…" if mode == "squeeze" else "Unsqueeze…"))
            self.b_go.setEnabled(bool(n))
        self.job.ok.connect(done)
        self.job.failed.connect(lambda e: (self._busy(False), self.status.setText("Stopped: " + e.split("\n")[0])))
        self._busy(True)
        self.job.start()
        if wait:
            self._wait()
        return self.job

    def go(self, confirm=True, wait=False):
        rep = self.survey_rep
        if not rep or not rep["todo"]:
            return None
        mode = rep["mode"]
        n = len(rep["todo"])
        if mode == "squeeze":
            q = (f"Squeeze <b>{n:,} files</b> ({S.gb(rep['todo_bytes'])}) to about {S.gb(rep['after_bytes'])}?"
                 f"<br><br>Each file is checked to unpack to exactly the original before the original is replaced, "
                 f"and keeps its name and date. You can Unsqueeze them any time.")
        else:
            grow = rep["after_bytes"] - rep["todo_bytes"]
            q = (f"Unsqueeze <b>{n:,} files</b> ({S.gb(rep['todo_bytes'])}) back to full size (about "
                 f"{S.gb(rep['after_bytes'])})?<br><br>That needs about {S.gb(max(grow, 0))} more room on the drive.")
            free = S._free(rep["todo"][0].parent)
            if free is not None and free < grow:
                QMessageBox.warning(self, self.windowTitle(),
                                    f"There isn't room: unsqueezing needs about {S.gb(grow)} more and the drive has "
                                    f"{S.gb(free)} free. Untick some folders, or free space first.")
                return None
        if confirm and QMessageBox.question(self, self.windowTitle(), q, QMessageBox.Yes | QMessageBox.No,
                                            QMessageBox.No) != QMessageBox.Yes:
            return None
        paths = list(rep["todo"])
        folders = self.checked_folders()
        settings = self.settings
        from organizer_window import _Job

        def work(job):
            _clear_leftovers(folders)
            return S.run(paths, mode, progress=lambda t, i, n_: job.note.emit(t, i, n_), stop=lambda: job.stop,
                         wait=lambda: organize_busy(settings), log=_log)
        self.job = _Job(work)
        self.job.note.connect(self._note)

        def done(r):
            self.run_rep = r
            self.survey_rep = None
            self._busy(False)
            self.b_go.setEnabled(False)
            text = describe_run(r)
            self.status.setText(text)
            self.settings.setValue("squeezer/last", f"Last time ({r['started']}): " + text.split("<br>")[0])
            _log(text.replace("<br>", "; "))
        self.job.ok.connect(done)
        self.job.failed.connect(lambda e: (self._busy(False), self.status.setText("Stopped: " + e.split("\n")[0])))
        self._busy(True)
        self.job.start()
        if wait:
            self._wait()
        return self.job

    def reject(self):
        if self.job is not None and self.job.isRunning():
            if QMessageBox.question(self, self.windowTitle(), "Stop squeezing? The file it is on is finished or left "
                                    "untouched - nothing is ever half done.", QMessageBox.Yes | QMessageBox.No,
                                    QMessageBox.No) != QMessageBox.Yes:
                return
            self.job.stop = True
            self._wait()
        super().reject()

    def accept(self):
        if self.job is not None and self.job.isRunning():
            self.reject()
            return
        super().accept()


def _log(text):
    try:
        import session_log
        session_log.write("squeezer", text)
    except Exception:  # noqa: BLE001
        pass


def _clear_leftovers(folders):
    """Half-written copies left by a crash or power cut: the original next to it was never touched."""
    for p in S.leftovers(folders):
        orig = p.name[:-len(S.TMP_SUFFIX)].rsplit(".", 1)[0]
        if (p.parent / orig).exists():
            try:
                p.unlink()
            except OSError:
                pass


def describe_survey(rep) -> str:
    n = len(rep["todo"])
    lines = []
    if rep["mode"] == "squeeze":
        if n:
            saved = rep["todo_bytes"] - rep["after_bytes"]
            lines.append(f"<b>{n:,} files to squeeze: {S.gb(rep['todo_bytes'])} now, about {S.gb(rep['after_bytes'])} "
                         f"squeezed - that frees about {S.gb(saved)}.</b>")
            if rep.get("sampled"):
                lines.append(f"(Worked out by squeezing {rep['sampled']} of them in memory: they shrank to "
                             f"{rep['ratio'] * 100:.0f}% of their size. Press Squeeze… to go ahead.)")
        else:
            lines.append("<b>Nothing to squeeze in the ticked folders.</b>")
        if rep["done"]:
            lines.append(f"Already squeezed: {rep['done']:,} files ({S.gb(rep['done_bytes'])}).")
    else:
        if n:
            lines.append(f"<b>{n:,} squeezed files: {S.gb(rep['todo_bytes'])} now, about {S.gb(rep['after_bytes'])} "
                         f"unsqueezed.</b> Press Unsqueeze… to go ahead.")
        else:
            lines.append("<b>Nothing squeezed in the ticked folders.</b>")
        if rep["done"]:
            lines.append(f"Already full size: {rep['done']:,} files.")
    for why, k in sorted(rep["skipped"].items(), key=lambda x: -x[1]):
        lines.append(f"Left as they are: {k:,} - {why}.")
    return "<br>".join(lines)


def describe_run(r) -> str:
    sq = r["mode"] == "squeeze"
    lines = []
    if sq:
        lines.append(f"<b>Squeezed {r['done']:,} files: {S.gb(r['before'])} → {S.gb(r['after'])}, "
                     f"{S.gb(r['before'] - r['after'])} freed.</b>")
    else:
        lines.append(f"<b>Unsqueezed {r['done']:,} files: {S.gb(r['before'])} → {S.gb(r['after'])}.</b>")
    if r.get("stopped"):
        lines.append("Stopped before the end - the rest are as they were.")
    why = {}
    for _p, w in r["skipped"] + r["failed"]:
        why[w] = why.get(w, 0) + 1
    for w, k in sorted(why.items(), key=lambda x: -x[1])[:8]:
        lines.append(f"Left as they were: {k:,} - {w}.")
    mins = r.get("seconds", 0) / 60
    lines.append(f"Took {mins:.0f} min." if mins >= 1 else "Took under a minute.")
    return "<br>".join(lines)
