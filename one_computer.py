"""
one_computer.py - the automatic jobs for a library run on one computer only.

Organize Automatically, the overnight ASIAIR fetch and the automatic N.I.N.A. clean-up each use this computer's own
folder settings (incoming folder, library, N.I.N.A. computers). Two computers running them for the same library
can file the same subs in different places, or clean up behind each other's back. So the computer that turns them
on leaves a small marker file in the library (Organized Astro Files/_SubZeroAstro_organize/automatic jobs
computer.json) naming itself. Another computer that turns them on for the same library is warned and can Take
Over Here; the first computer's automatic runs then step aside for that library.

A computer is known by the system's own id for it (Windows' MachineGuid, the Mac's hardware UUID), which stays the
same when its name or network changes; the name is only shown.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import organizer as O

MARKER = "automatic jobs computer.json"
JOBS = {"organize": "Organize Automatically",
        "asiair_night": "the overnight ASIAIR fetch",
        "nina_clean": "the automatic N.I.N.A. clean-up"}
WHY = ("Each computer has its own incoming, library and N.I.N.A. folder settings, so two computers running "
       "these jobs can file the same subs in different places, or clean up behind each other's back.")

_answer = None          # the self-test answers the Take Over question with this (True / False) instead of a window
_me = None              # (id, name), worked out once


def this_computer() -> tuple:
    """(id, name) of this computer."""
    global _me
    if _me is not None:
        return _me
    cid = ""
    try:
        from PySide6.QtCore import QSysInfo
        cid = bytes(QSysInfo.machineUniqueId()).decode("ascii", "ignore").strip()
    except Exception:  # noqa: BLE001
        pass
    name = ""
    if sys.platform == "darwin":                       # "Brian's MacBook Pro", not Brians-MacBook-Pro-2.local
        try:
            r = subprocess.run(["scutil", "--get", "ComputerName"], capture_output=True, text=True, timeout=5)
            name = r.stdout.strip() if r.returncode == 0 else ""
        except (OSError, subprocess.SubprocessError):
            pass
    if not name:
        name = os.environ.get("COMPUTERNAME", "")
    if not name:
        try:
            name = socket.gethostname()
        except OSError:
            name = ""
        if name.endswith(".local"):
            name = name[:-6]
    name = name or "this computer"
    _me = (cid or "name:" + name.lower(), name)
    return _me


def library(settings) -> str:
    """The library the automatic jobs work for: Organize Automatically's, else Organize's."""
    for k in ("auto_org/dst", "organize/dst"):
        v = (settings.value(k, "") or "").strip()
        if v:
            return v
    return ""


def marker_path(lib) -> Path:
    return O.org_base(lib) / O.LOG_DIR / MARKER


def read(lib) -> dict | None:
    """The marker, or None (no library, no marker, can't be read)."""
    if not lib:
        return None
    try:
        d = json.loads(marker_path(lib).read_text(encoding="utf-8"))
        return d if isinstance(d, dict) and d.get("id") else None
    except (OSError, ValueError):
        return None


def other_owner(lib) -> dict | None:
    """The marker when another computer runs automatic jobs for this library, else None."""
    d = read(lib)
    if d and d.get("id") != this_computer()[0] and d.get("jobs"):
        return d
    return None


def _write(lib, d) -> bool:
    p = marker_path(lib)
    try:
        if not Path(lib).is_dir():
            return False
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + ".tmp")
        tmp.write_text(json.dumps(d, indent=1), encoding="utf-8")
        os.replace(tmp, p)
        return True
    except OSError:
        return False


def claim(lib, job) -> bool:
    """This computer runs `job` for the library (taking over from any other computer)."""
    if not lib:
        return False
    cid, name = this_computer()
    d = read(lib)
    jobs = list(d.get("jobs") or []) if d and d.get("id") == cid else []
    if job not in jobs:
        jobs.append(job)
    since = d.get("since") if d and d.get("id") == cid else ""
    stamp = time.strftime("%Y-%m-%d %H:%M")
    return _write(lib, {"note": f"The automatic jobs for this library run on {name}. Written by SubZeroAstro - "
                                "run them from one computer only.",
                        "id": cid, "computer": name, "jobs": jobs, "since": since or stamp, "updated": stamp})


def release(lib, job):
    """This computer no longer runs `job` (the marker goes when nothing is left; another computer's is kept)."""
    d = read(lib)
    if not d or d.get("id") != this_computer()[0]:
        return
    jobs = [j for j in d.get("jobs") or [] if j != job]
    if jobs:
        d["jobs"], d["updated"] = jobs, time.strftime("%Y-%m-%d %H:%M")
        _write(lib, d)
    else:
        try:
            marker_path(lib).unlink()
        except OSError:
            pass


def jobs_text(d) -> str:
    names = [JOBS.get(j, j) for j in d.get("jobs") or []]
    if not names:
        return "automatic jobs"
    names[0] = names[0][0].upper() + names[0][1:] if names[0][0].islower() else names[0]
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def stand_down_text(d) -> str:
    """Why an automatic job on this computer is stepping aside."""
    return (f"this computer steps aside - the automatic jobs for this library now run on "
            f"{d.get('computer', 'another computer')} "
            f"(since {d.get('since', '?')}). Run them from one computer only; to run them here again, turn them "
            f"on again here and choose Take Over Here")


def ask_take_over(parent, settings, job, lib=None) -> bool:
    """Turning `job` on: fine (and recorded) when no other computer runs automatic jobs for this library; else
    ask. True = go ahead (this computer now runs them), False = leave it off here."""
    lib = lib if lib is not None else library(settings)
    if not lib or not Path(lib).is_dir():
        return True                                    # no library reachable: nothing to compare with
    other = other_owner(lib)
    if other:
        who = other.get("computer") or "another computer"
        if _answer is not None:
            ok = bool(_answer)
        else:
            from PySide6.QtWidgets import QMessageBox
            box = QMessageBox(parent)
            box.setIcon(QMessageBox.Warning)
            box.setWindowTitle("Run automatic jobs from one computer only")
            box.setText(f"<b>{jobs_text(other)} already run{'s' if len(other.get('jobs') or []) == 1 else ''} "
                        f"on {who}</b> for this library (since {other.get('since', '?')}).")
            box.setInformativeText(f"{WHY}<br><br><b>Take over here?</b> {JOBS.get(job, job)[0].upper()}"
                                   f"{JOBS.get(job, job)[1:]} then runs on {this_computer()[1]}, and {who} skips its "
                                   f"automatic runs for this library from then on.<br><br>Library: {lib}")
            b_take = box.addButton("Take Over Here", QMessageBox.AcceptRole)
            box.addButton("Leave It on " + who, QMessageBox.RejectRole)
            box.exec()
            ok = box.clickedButton() is b_take
        if not ok:
            return False
        _note(f"automatic jobs for {lib}: taken over from {who}")
    claim(lib, job)
    return True


def check_run(lib, job, on_here=True) -> str:
    """Before an automatic job runs by itself: '' = go ahead (and recorded, when no computer is named yet), else
    why this computer steps aside. A job not switched on here (a one-off Run Now) only goes ahead."""
    if not lib or not on_here:
        return ""
    other = other_owner(lib)
    if other:
        return stand_down_text(other)
    d = read(lib)
    if d is None or job not in (d.get("jobs") or []):
        claim(lib, job)                                # set up before this version, or the marker was removed
    return ""


def _note(text):
    try:
        import session_log
        session_log.write("organizer", text)
    except Exception:  # noqa: BLE001
        pass
