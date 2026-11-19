"""
profile_window.py - Settings › My Profile…: use one profile on the NAS from every computer, and Export / Import My
Settings. The work itself is in profile_sync.py.
"""
from __future__ import annotations

import datetime as _dt
from pathlib import Path

from PySide6.QtWidgets import (QDialog, QFileDialog, QHBoxLayout, QLabel, QMessageBox, QPushButton, QVBoxLayout)

import profile_sync as ps

SHARED_TEXT = ("My Rigs, your location, Organize choices and library folders, the ASIAIR list, the N.I.N.A. "
               "computers and their keep-days, Target Picker and weather, and the processing choices on pages 1 to 4")
LOCAL_TEXT = ("where PixInsight, ASTAP, GraXpert and RC-Astro are installed, window sizes, the crash-recovery folder, "
              "and the automatic jobs (Organize Automatically, the overnight ASIAIR fetch, the automatic N.I.N.A. "
              "clean-up) - they run on one computer only")


class ProfileDialog(QDialog):
    def __init__(self, parent, settings, syncer=None, version=""):
        super().__init__(parent)
        self.settings, self.syncer, self.version = settings, syncer, version
        self.setWindowTitle("My Profile")
        self.setMinimumWidth(560)
        lay = QVBoxLayout(self)
        intro = QLabel(
            "<b>The same settings on every computer.</b> Keep your profile in a folder on your NAS and use it "
            "from each computer: a change made on one appears on the others (within a minute while SubZeroAstro "
            "is open, otherwise the next time it starts).<br><br>"
            f"<b>Shared:</b> {SHARED_TEXT}.<br>"
            f"<b>Kept on each computer:</b> {LOCAL_TEXT}.<br><br>"
            "Windows and Mac can share one profile: a NAS folder (\\\\astronas\\Astro or Z:\\ on Windows, "
            "/Volumes/Astro on a Mac) is translated for each computer. A folder on one computer's own disk can't be "
            "shared - the other computers keep their own choice for it.")
        intro.setWordWrap(True)
        lay.addWidget(intro)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setStyleSheet("padding: 8px; border: 1px solid #555; border-radius: 4px;")
        lay.addWidget(self.status)
        row = QHBoxLayout()
        self.btn_use = QPushButton("Use a Profile on My NAS…")
        self.btn_use.setToolTip("Pick a folder on your NAS - a new profile is made there from this computer's "
                                "settings, or this computer joins the profile already there")
        self.btn_use.clicked.connect(self.choose)
        self.btn_sync = QPushButton("Sync Now")
        self.btn_sync.clicked.connect(self.sync_now)
        self.btn_stop = QPushButton("Stop Using the Profile")
        self.btn_stop.setToolTip("This computer keeps the settings it has now and no longer follows the profile")
        self.btn_stop.clicked.connect(self.stop)
        for b in (self.btn_use, self.btn_sync, self.btn_stop):
            row.addWidget(b)
        row.addStretch(1)
        lay.addLayout(row)
        lay.addSpacing(8)
        lab = QLabel("<b>Export / Import My Settings</b> - everything above plus this computer's tool locations and "
                     "schedules in one file: for a new computer, or a backup. An import never switches an "
                     "automatic job on.")
        lab.setWordWrap(True)
        lay.addWidget(lab)
        row = QHBoxLayout()
        self.btn_export = QPushButton("Export My Settings…")
        self.btn_export.clicked.connect(self.export)
        self.btn_import = QPushButton("Import My Settings…")
        self.btn_import.clicked.connect(self.import_)
        row.addWidget(self.btn_export)
        row.addWidget(self.btn_import)
        row.addStretch(1)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        row.addWidget(close)
        lay.addLayout(row)
        self.refresh()

    def refresh(self):
        fol = ps.folder(self.settings)
        if fol:
            self.status.setText(f"This computer uses the profile in <b>{fol}</b>.<br>Last sync: "
                                f"{ps.describe_last(self.settings)}")
            self.btn_use.setText("Use Another Profile Folder…")
        else:
            self.status.setText("No profile in use - this computer keeps its own settings.")
            self.btn_use.setText("Use a Profile on My NAS…")
        self.btn_sync.setEnabled(bool(fol))
        self.btn_stop.setEnabled(bool(fol))

    def choose(self):
        start = ps.folder(self.settings) or (self.settings.value("organize/dst", "") or "")
        fol = QFileDialog.getExistingDirectory(self, "Folder on your NAS for your profile", start)
        if fol:
            self.use_folder(fol)

    def use_folder(self, fol, ask=True):
        """ask=False: no questions (the self-test)."""
        if ask and not self._confirm_folder(fol):
            return None
        r = ps.start_using(self.settings, fol, version=self.version)
        if not r.get("ok"):
            if ask:
                QMessageBox.warning(self, "My Profile", f"The profile couldn't be used:\n\n{r.get('msg')}")
            self.refresh()
            return r
        if ask:
            if r.get("new"):
                msg = (f"A new profile was made in\n{fol}\nfrom this computer's settings.\n\nOn each of your other "
                       "computers: Settings › My Profile… › Use a Profile on My NAS, and pick the same folder.")
            else:
                n = len(r.get("pulled") or [])
                msg = (f"This computer now uses the profile in\n{fol}\n({n} setting{'s' if n != 1 else ''} taken "
                       "from it).")
                if r.get("backup"):
                    msg += f"\n\nThis computer's settings from before were saved to\n{r['backup']}"
                msg += "\n\nWindows that are open show the new settings the next time they are opened."
            QMessageBox.information(self, "My Profile", msg)
        self.refresh()
        return r

    def _confirm_folder(self, fol) -> bool:
        import folder_dialogs as fd
        if fd.share_root(fol) is None and not ps.Places.here().to_neutral(str(fol)).startswith("smb://"):
            b = QMessageBox.question(
                self, "My Profile", f"{fol}\n\nlooks like a folder on this computer's own disk - your other "
                "computers can't reach it. A profile belongs on your NAS.\n\nUse this folder anyway?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if b != QMessageBox.Yes:
                return False
        if (Path(fol) / ps.PROFILE_FILE).exists():
            b = QMessageBox.question(
                self, "My Profile", "There is a profile in that folder already.\n\nUse it? Its settings replace "
                "this computer's shared settings (a backup of this computer's settings is saved first). Tool "
                "locations and automatic jobs here stay as they are.", QMessageBox.Yes | QMessageBox.No)
            return b == QMessageBox.Yes
        return True

    def sync_now(self):
        if self.syncer is not None:
            self.syncer.start(force=True)
            self.syncer.wait(20)
        else:
            ps.sync(self.settings, version=self.version)
        self.refresh()

    def stop(self, ask=True):
        if ask and QMessageBox.question(
                self, "My Profile", "Stop using the profile on this computer?\n\nThis computer keeps the settings it "
                "has now; changes made here no longer reach your other computers, and theirs no longer arrive "
                "here. The profile file on the NAS stays.", QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
            return
        ps.stop_using(self.settings)
        self.refresh()

    def export(self, path=None):
        if path is None:
            name = f"SubZeroAstro Settings {ps.host()} {_dt.date.today()}.json"
            path, _f = QFileDialog.getSaveFileName(self, "Export My Settings", str(Path.home() / name),
                                                   "SubZeroAstro settings (*.json)")
            if not path:
                return None
        try:
            out = ps.export_settings(self.settings, path, version=self.version)
        except OSError as e:
            QMessageBox.warning(self, "Export My Settings", f"The settings couldn't be saved:\n\n{e}")
            return None
        return out

    def import_(self, path=None):
        ask = path is None
        if ask:
            path, _f = QFileDialog.getOpenFileName(self, "Import My Settings", str(Path.home()),
                                                   "SubZeroAstro settings (*.json)")
            if not path:
                return None
            if QMessageBox.question(
                    self, "Import My Settings", "Replace this computer's settings with the ones in this file?\n\n"
                    "Automatic jobs are not switched on by an import - turn them on on the one computer that "
                    "should run them.", QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
                return None
        r = ps.import_settings(self.settings, path)
        if ask:
            if not r.get("ok"):
                QMessageBox.warning(self, "Import My Settings", r.get("msg", ""))
            else:
                msg = f"{len(r['imported'])} settings imported."
                if any(k in ps.TOOL_KEYS for k in r["skipped"]):
                    msg += ("\n\nSome helper programs aren't at the same place on this computer - set them in "
                            "Settings › Your Tools.")
                msg += "\n\nWindows that are open show the new settings the next time they are opened."
                QMessageBox.information(self, "Import My Settings", msg)
        self.refresh()
        return r
