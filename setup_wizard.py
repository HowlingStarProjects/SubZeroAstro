"""
setup_wizard.py - "Set Up SubZeroAstro": a few pages that get a new user going.

  1. Your main AstroComputer - do the setup on the computer that stays on and sees the NAS; on another
     computer, use the profile from the NAS instead (when My Profile is there).
  2. Your folders - one main astro folder with a folder for each job inside it (incoming, library,
     calibration, master lights, processed, finished pictures, PixInsight projects).
  3. Your helper programs - RC-Astro (XTerminators), GraXpert, ASTAP and its star database.
  4. Where you observe from - latitude and longitude (target picker, weather).
  5. All set - what was set up.

Shown by itself only on a brand-new install (none of the folders set yet); Settings > Set Up SubZeroAstro…
runs it again any time. Nothing is saved until Finish; folders are only ever created, never moved or deleted.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QDialog, QDoubleSpinBox, QFileDialog, QFrame, QGridLayout,
                               QHBoxLayout, QLabel, QLineEdit, QPushButton, QRadioButton, QStackedWidget,
                               QVBoxLayout, QWidget)

import tool_check

DONE_KEY = "setup/done"                  # the wizard was finished (or skipped) on this computer
MAIN_KEY = "setup/main_computer"         # True: this is the main AstroComputer

# key, label, folder name inside the main folder, what it is for, settings keys it fills
FOLDERS = [
    ("incoming", "Incoming subs", "Incoming",
     "where new subs arrive from your ASIAIR or N.I.N.A., before they are filed",
     ("asiair/incoming", "organize/src", "auto_org/src")),
    ("library", "Library", "Library",
     "your organized subs, filed by rig and target",
     ("organize/dst", "auto_org/dst")),
    ("calibration", "Calibration", "Calibration",
     "darks, flats and bias masters, a folder for each rig",
     ("stack/cal_root",)),
    ("masters", "Master lights", "Master Lights",
     "the stacked pictures from Calibrate and Stack, a folder for each target",
     ("stack/masters_root",)),
    ("processed", "Processed", "Processed",
     "images from Astro Image Processing",
     ("prep/out_dir",)),
    ("finished", "Finished pictures", "Finished Pictures",
     "your final pictures from Finishing Touches",
     ("finish/export_dir",)),
    ("pixinsight", "PixInsight projects", "PixInsight Projects",
     "PixInsight projects SubZeroAstro makes for you",
     ("finish/pi_dir", "pi/project_dir")),
]
# a new install: none of these set yet
NEW_INSTALL_KEYS = ("organize/dst", "auto_org/dst", "asiair/incoming", "stack/cal_root", "stack/masters_root",
                    "prep/out_dir", "finish/export_dir", "finish/export_dir_last")

PAGE_TITLES = ["Your main AstroComputer", "Your folders", "Your helper programs", "Where you observe from",
               "All set"]


def is_new_install(settings) -> bool:
    if settings.value(DONE_KEY, False, type=bool):
        return False
    return not any((settings.value(k, "") or "").strip() for k in NEW_INSTALL_KEYS)


def suggested(main: str) -> dict:
    """{folder key: path} inside the main astro folder."""
    root = Path(main)
    return {k: str(root / name) for k, _l, name, _w, _s in FOLDERS}


def current(settings) -> dict:
    """{folder key: the folder set now ('' when none)}."""
    out = {}
    for k, _l, _n, _w, skeys in FOLDERS:
        out[k] = next(((settings.value(s, "") or "").strip() for s in skeys if (settings.value(s, "") or "").strip()),
                      "")
    return out


def common_parent(paths) -> str:
    ps = [Path(p) for p in paths if p]
    if not ps:
        return ""
    parents = {p.parent for p in ps}
    return str(parents.pop()) if len(parents) == 1 else ""


def _set(settings, key, value):
    """Write only a real change: with My Profile on, every write is shared with the other computers, so an
    unchanged folder must not count as one (it would overwrite the main computer's choice)."""
    old = settings.value(key, None)
    if old is not None and str(old).strip().lower() == str(value).strip().lower():
        return
    settings.setValue(key, value)


def save_folders(settings, folders: dict, use_pi: bool, create=True) -> list:
    """Write the folders to the settings (and create the missing ones). Returns the problems (list of text)."""
    problems = []
    for k, label, _n, _w, skeys in FOLDERS:
        p = (folders.get(k) or "").strip()
        if not p or (k == "pixinsight" and not use_pi):
            continue
        if create:
            try:
                Path(p).mkdir(parents=True, exist_ok=True)
            except OSError as e:
                problems.append(f"{label}: could not create {p} ({e.strerror or e})")
                continue
        for s in skeys:
            _set(settings, s, p)
    inc, lib = (folders.get("incoming") or "").strip(), (folders.get("library") or "").strip()
    if inc and lib:
        _set(settings, "organize/same", Path(inc) == Path(lib))
    return problems


def _title(text, sub=""):
    lab = QLabel(f"<span style='font-size:15pt; font-weight:bold'>{text}</span>"
                 + (f"<br><span style='color:#8a93a6'>{sub}</span>" if sub else ""))
    lab.setWordWrap(True)
    return lab


def _para(text):
    lab = QLabel(text)
    lab.setWordWrap(True)
    lab.setOpenExternalLinks(True)
    return lab


class SetupWizard(QDialog):
    def __init__(self, settings, parent=None, first_run=False):
        super().__init__(parent)
        self.settings = settings
        self.first_run = first_run
        self.main_window = parent
        self.setWindowTitle("Set Up SubZeroAstro")
        self.setMinimumSize(860, 600)
        self.checker = None
        self.results = []
        self.summary_lines = []
        lay = QVBoxLayout(self)
        self.step_label = QLabel()
        self.step_label.setStyleSheet("color:#8a93a6")
        lay.addWidget(self.step_label)
        self.stack = QStackedWidget()
        lay.addWidget(self.stack, 1)
        for build in (self._page_computer, self._page_folders, self._page_tools, self._page_location,
                      self._page_done):
            w = QWidget()
            v = QVBoxLayout(w)
            v.setContentsMargins(4, 4, 4, 4)
            build(v)
            v.addStretch(1)
            self.stack.addWidget(w)
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        lay.addWidget(line)
        bottom = QHBoxLayout()
        self.b_later = QPushButton("Not Now")
        self.b_later.setToolTip("Close the setup. Settings › Set Up SubZeroAstro… brings it back any time.")
        self.b_later.clicked.connect(self.later)
        bottom.addWidget(self.b_later)
        bottom.addStretch(1)
        self.b_back = QPushButton("‹ Back")
        self.b_back.clicked.connect(lambda: self.go(self.stack.currentIndex() - 1))
        self.b_next = QPushButton("Next ›")
        self.b_next.setDefault(True)
        self.b_next.clicked.connect(self.next_page)
        bottom.addWidget(self.b_back)
        bottom.addWidget(self.b_next)
        lay.addLayout(bottom)
        self.go(0)

    # ------------------------------------------------------------------ pages
    def _page_computer(self, v):
        v.addWidget(_title("Welcome to SubZeroAstro",
                           "A few minutes now and everything has its place from the first night."))
        v.addWidget(_para(
            "<b>First, pick the computer you want as your main AstroComputer</b> and do this whole setup on it. "
            "That's usually the one that stays on, can see your NAS or big drive, and does the automatic jobs: "
            "fetching subs from the ASIAIR overnight and filing them in your library."))
        self.rb_main = QRadioButton("This is my main AstroComputer - set it up")
        self.rb_other = QRadioButton("This is another computer (a laptop, a second PC)")
        self.rb_main.setChecked(self.settings.value(MAIN_KEY, True, type=bool))
        self.rb_other.setChecked(not self.rb_main.isChecked())
        grp = QButtonGroup(self)
        for rb in (self.rb_main, self.rb_other):
            grp.addButton(rb)
            v.addWidget(rb)
        self.other_box = QFrame()
        ov = QVBoxLayout(self.other_box)
        ov.setContentsMargins(24, 0, 0, 0)
        self.has_profile = bool(self.main_window is not None and hasattr(self.main_window, "open_profile"))
        if self.has_profile:
            ov.addWidget(_para("Already set up your main AstroComputer? Use its profile from the NAS and this "
                               "computer gets the same folders, rigs and choices. You can still check your "
                               "helper programs on the next pages."))
            b = QPushButton("Use My Profile From the NAS…")
            b.clicked.connect(self.use_profile)
            ov.addWidget(b)
        else:
            ov.addWidget(_para("Best: set up your main AstroComputer first. On this computer, pick the same "
                               "folders on the NAS on the next page, so both computers see the same library."))
        v.addWidget(self.other_box)
        self.rb_other.toggled.connect(self.other_box.setVisible)
        self.other_box.setVisible(self.rb_other.isChecked())
        v.addSpacing(10)
        v.addWidget(_para("<span style='color:#8a93a6'>Installing SubZeroAstro itself is in the first chapter of "
                          "the manual (Help › User Manual). This setup can be run again any time from "
                          "Settings › Set Up SubZeroAstro….</span>"))

    def _page_folders(self, v):
        v.addWidget(_title("Your folders", "Where your pictures arrive, are kept and end up."))
        v.addWidget(_para(
            "Pick one <b>main folder for your astro work</b> - on your NAS or a big drive - and SubZeroAstro "
            "suggests a folder for each job inside it. Change any of them if you already have folders you like. "
            "Missing folders are created when you click Next; nothing is moved or deleted."))
        h = QHBoxLayout()
        h.addWidget(QLabel("<b>Main astro folder:</b>"))
        now = current(self.settings)
        self.main_edit = QLineEdit(common_parent(now.values()))
        self.main_edit.setPlaceholderText("for example D:\\Astro or \\\\NAS\\Astro")
        h.addWidget(self.main_edit, 1)
        b = QPushButton("Browse…")
        b.clicked.connect(self.browse_main)
        h.addWidget(b)
        b = QPushButton("Suggest Folders")
        b.setToolTip("Fill every folder below with one inside the main folder")
        b.clicked.connect(lambda: self.fill_suggested(force=True))
        h.addWidget(b)
        v.addLayout(h)
        self.use_pi = QCheckBox("I use PixInsight too")
        self.use_pi.setChecked(bool(now.get("pixinsight")))
        v.addWidget(self.use_pi)
        grid = QGridLayout()
        grid.setColumnStretch(1, 1)
        self.folder_edits = {}
        self.folder_widgets = {}
        for i, (k, label, _n, what, _s) in enumerate(FOLDERS):
            lab = QLabel(f"<b>{label}</b><br><small style='color:#8a93a6'>{what}</small>")
            lab.setWordWrap(True)
            lab.setMinimumWidth(250)
            ed = QLineEdit(now.get(k, ""))
            bb = QPushButton("Browse…")
            bb.clicked.connect(lambda _=False, e=ed, t=label: self.browse_folder(e, t))
            grid.addWidget(lab, i, 0)
            grid.addWidget(ed, i, 1)
            grid.addWidget(bb, i, 2)
            self.folder_edits[k] = ed
            self.folder_widgets[k] = (lab, ed, bb)
        v.addLayout(grid)
        self.use_pi.toggled.connect(self._pi_toggled)
        self._pi_toggled(self.use_pi.isChecked())
        self.folder_problem = _para("")
        self.folder_problem.setStyleSheet("color:#eba932")
        v.addWidget(self.folder_problem)

    def _page_tools(self, v):
        v.addWidget(_title("Your helper programs",
                           "Optional - SubZeroAstro culls and stacks without them."))
        v.addWidget(_para(
            "These add sharpening, noise and star removal, gradient removal and plate solving on the "
            "<b>Astro Image Processing</b> page. Install the ones you want with <b>Download</b>, then click "
            "<b>Check Again</b>. If one is installed somewhere unusual, use <b>Find It…</b>"))
        grid = QGridLayout()
        grid.setColumnStretch(1, 1)
        self.tool_rows = {}
        names = {"rcastro": "RC-Astro XTerminators", "graxpert": "GraXpert", "astap": "ASTAP and a star catalog"}
        for i, (key, name, skey, _f, does, _w, url) in enumerate(tool_check.TOOLS):
            title = QLabel(f"<b>{names.get(key, name)}</b><br><small style='color:#8a93a6'>{does}</small>")
            title.setWordWrap(True)
            title.setMinimumWidth(260)
            status = QLabel("checking…")
            status.setWordWrap(True)
            b_get = QPushButton("Download")
            b_get.setToolTip(url)
            b_get.clicked.connect(lambda _=False, u=url: QDesktopServices.openUrl(QUrl(u)))
            b_find = QPushButton("Find It…")
            b_find.clicked.connect(lambda _=False, k=skey, n=name: self.find_tool(k, n))
            grid.addWidget(title, i, 0)
            grid.addWidget(status, i, 1)
            grid.addWidget(b_get, i, 2)
            grid.addWidget(b_find, i, 3)
            self.tool_rows[key] = status
            if key == "rcastro":
                self.b_lic = QPushButton("Activate…")
                self.b_lic.setToolTip("See and activate your RC-Astro licences")
                self.b_lic.setEnabled(False)
                self.b_lic.clicked.connect(self.licences)
                grid.addWidget(self.b_lic, i, 4)
        v.addLayout(grid)
        h = QHBoxLayout()
        b = QPushButton("Check Again")
        b.clicked.connect(self.recheck)
        h.addWidget(b)
        h.addStretch(1)
        v.addLayout(h)
        v.addWidget(_para("<span style='color:#8a93a6'><b>RC-Astro:</b> buy BlurXTerminator, NoiseXTerminator "
                          "and StarXTerminator on rc-astro.com, install the RC-Astro program, then Activate here "
                          "with your licence codes. <b>ASTAP:</b> also install a star catalog from the same page - "
                          "D50 for most telescopes, D05 or D20 for wide-field lenses. <b>PixInsight</b> is not "
                          "needed by SubZeroAstro; if you use it, its projects folder is on the folders page."
                          "</span>"))

    def _page_location(self, v):
        v.addWidget(_title("Where you observe from",
                           "Used by Tonight's Targets and the weather. Optional."))
        v.addWidget(_para("Type your latitude and longitude in degrees (north and east are positive, so the USA "
                          "has a negative longitude). A map app shows them when you press on your spot."))
        g = QGridLayout()
        self.lat = QDoubleSpinBox()
        self.lat.setRange(-90, 90)
        self.lat.setDecimals(4)
        self.lon = QDoubleSpinBox()
        self.lon.setRange(-180, 180)
        self.lon.setDecimals(4)
        try:
            self.lat.setValue(float(self.settings.value("picker/lat", 0.0) or 0.0))
            self.lon.setValue(float(self.settings.value("picker/lon", 0.0) or 0.0))
        except (TypeError, ValueError):
            pass
        g.addWidget(QLabel("Latitude:"), 0, 0)
        g.addWidget(self.lat, 0, 1)
        g.addWidget(QLabel("Longitude:"), 1, 0)
        g.addWidget(self.lon, 1, 1)
        g.setColumnStretch(2, 1)
        v.addLayout(g)

    def _page_done(self, v):
        v.addWidget(_title("All set", "Here's what SubZeroAstro now knows."))
        self.summary = _para("")
        v.addWidget(self.summary)
        v.addWidget(_para(
            "<b>Next:</b> tell SubZeroAstro about your telescopes in <b>Settings › My Rigs…</b> (or read them from "
            "N.I.N.A. in Settings › Work With N.I.N.A.…), then open your first night's subs with "
            "<b>File › Load Light Frame Data…</b>. On your main AstroComputer you can also let it work by itself: "
            "<b>File › Fetch From My ASIAIRs…</b> and "
            "<b>Organize Automatically</b> (in File › Organize My Astro Files…). The manual (Help › User Manual, "
            "or F1) walks you through every page."))

    # ------------------------------------------------------------------ moving between pages
    def go(self, i):
        i = max(0, min(i, self.stack.count() - 1))
        self.stack.setCurrentIndex(i)
        self.step_label.setText(f"Step {i + 1} of {self.stack.count()} · {PAGE_TITLES[i]}")
        self.b_back.setEnabled(i > 0)
        last = i == self.stack.count() - 1
        self.b_next.setText("Finish" if last else "Next ›")
        self.b_later.setVisible(not last)
        if i == 1 and not any(e.text().strip() for e in self.folder_edits.values()):
            self.fill_suggested(force=False)
        if i == 2:
            self.recheck()
        if last:
            self.summary.setText(self.summary_text())

    def next_page(self):
        i = self.stack.currentIndex()
        if i == 1 and not self.apply_folders():
            return
        if i == self.stack.count() - 1:
            self.finish()
            return
        self.go(i + 1)

    def later(self):
        self.settings.setValue(DONE_KEY, True)       # don't open by itself again; Settings brings it back
        self.reject()

    # ------------------------------------------------------------------ folders
    def _pi_toggled(self, on):
        for w in self.folder_widgets["pixinsight"]:
            w.setVisible(on)

    def browse_main(self):
        d = QFileDialog.getExistingDirectory(self, "Main folder for your astro work",
                                             self.main_edit.text().strip() or str(Path.home()))
        if d:
            self.main_edit.setText(d)
            self.fill_suggested(force=True)

    def browse_folder(self, edit, label):
        d = QFileDialog.getExistingDirectory(self, label, edit.text().strip() or self.main_edit.text().strip()
                                             or str(Path.home()))
        if d:
            edit.setText(d)

    def fill_suggested(self, force=False):
        main = self.main_edit.text().strip()
        if not main:
            return
        for k, p in suggested(main).items():
            if force or not self.folder_edits[k].text().strip():
                self.folder_edits[k].setText(p)

    def folders(self) -> dict:
        return {k: e.text().strip() for k, e in self.folder_edits.items()}

    def apply_folders(self) -> bool:
        main = self.main_edit.text().strip()
        if main and not any(self.folders().values()):
            self.fill_suggested(force=True)
        problems = save_folders(self.settings, self.folders(), self.use_pi.isChecked())
        self.folder_problem.setText("<br>".join(problems))
        return not problems

    # ------------------------------------------------------------------ tools
    def find_tool(self, skey, name):
        f = tool_check.browse_program(self, name, self.settings.value(skey, "") or "")
        if f:
            self.settings.setValue(skey, f)
            self.recheck()

    def recheck(self):
        if self.checker is not None and self.checker.isRunning():
            return
        for st in self.tool_rows.values():
            st.setText("<span style='color:#8a93a6'>checking…</span>")
        self.checker = tool_check.Checker(tool_check.settings_paths(self.settings))
        self.checker.done.connect(self.show_tools)
        self.checker.start()

    def show_tools(self, results):
        self.results = results
        for r in results:
            st = self.tool_rows.get(r["key"])
            if st is None:
                continue
            if r["ok"] and not r["problem"]:
                st.setText(f"<span style='color:#5fd27a'>✓ {r['detail']}</span>")
            else:
                st.setText(f"<span style='color:#eba932'>{'⚠' if r['ok'] else '✗'} {r['detail']}</span>")
            st.setToolTip(r["path"] or "")
            if r["key"] == "rcastro":
                self.b_lic.setEnabled(bool(r["path"]))

    def licences(self):
        r = next((r for r in self.results if r["key"] == "rcastro"), None)
        if not r or not r["path"]:
            return
        import rc_licence
        dlg = rc_licence.LicenceDialog(r["path"], self)
        dlg.exec()
        if dlg.changed:
            self.recheck()

    # ------------------------------------------------------------------ profile, summary, finish
    def use_profile(self):
        try:
            self.main_window.open_profile()
        except Exception:  # noqa: BLE001 - the wizard carries on without it
            return
        # the profile may have brought folders: show them on the folders page
        now = current(self.settings)
        for k, e in self.folder_edits.items():
            if now.get(k):
                e.setText(now[k])
        if now.get("pixinsight"):
            self.use_pi.setChecked(True)
        if not self.main_edit.text().strip():
            self.main_edit.setText(common_parent(now.values()))

    def summary_text(self) -> str:
        tick, cross = "<span style='color:#5fd27a'>✓</span>", "<span style='color:#eba932'>✗</span>"
        lines = [f"{tick} This is {'your main AstroComputer' if self.rb_main.isChecked() else 'another computer'}"]
        f = self.folders()
        for k, label, _n, _w, _s in FOLDERS:
            if k == "pixinsight" and not self.use_pi.isChecked():
                continue
            lines.append(f"{tick if f.get(k) else cross} {label}: {f.get(k) or 'not set'}")
        names = {"rcastro": "RC-Astro XTerminators", "graxpert": "GraXpert", "astap": "ASTAP"}
        for r in self.results:
            ok = r["ok"] and not r["problem"]
            lines.append(f"{tick if ok else cross} {names.get(r['key'], r['name'])}: "
                         f"{'ready' if ok else r['problem'] or 'not found'}")
        if self.lat.value() or self.lon.value():
            lines.append(f"{tick} Observing from {self.lat.value():.4f}, {self.lon.value():.4f}")
        else:
            lines.append(f"{cross} Observing location: not set")
        self.summary_lines = lines
        return "<br>".join(lines)

    def finish(self):
        self.settings.setValue(MAIN_KEY, self.rb_main.isChecked())
        if self.lat.value() or self.lon.value():
            for k, val in (("picker/lat", self.lat.value()), ("picker/lon", self.lon.value())):
                try:
                    same = abs(float(self.settings.value(k, 0.0) or 0.0) - val) < 1e-6
                except (TypeError, ValueError):
                    same = False
                if not same:
                    self.settings.setValue(k, val)
        self.settings.setValue(DONE_KEY, True)
        self.accept()

    def done(self, code):
        if self.checker is not None and self.checker.isRunning():
            self.checker.wait(15000)
        tool_check.refresh_open_pages()
        super().done(code)
