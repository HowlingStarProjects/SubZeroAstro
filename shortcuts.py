"""
shortcuts.py - Help › Put SubZeroAstro on My Desktop: a desktop shortcut (Windows, plus one in the Start menu) or a
Finder alias on the Desktop (Mac) to the copy of SubZeroAstro that is running now - so the shortcut never opens an
old copy left in another folder. A shortcut made here is kept pointing at the app when a newer copy is unzipped
somewhere else (checked at each start, like the automatic run's schedule).
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

NAME = "SubZeroAstro"
K_TARGET = "shortcut/target"           # what the shortcut made here points at ("" = none made)


def app_target():
    """The thing a shortcut should open: SubZeroAstro.exe on Windows, the SubZeroAstro.app bundle on a Mac.
    None when running from the Python source (no app to point at)."""
    if not getattr(sys, "frozen", False):
        return None
    exe = Path(sys.executable)
    if sys.platform == "darwin":
        for p in exe.parents:
            if p.suffix == ".app":
                return p
        return None
    return exe


def windows_ps(target) -> str:
    """PowerShell that makes SubZeroAstro.lnk on the Desktop (the real one - OneDrive moves it) and in the
    Start menu. The paths come in through environment variables, so no quoting can break them."""
    return ("$w = New-Object -ComObject WScript.Shell; "
            "foreach ($d in @([Environment]::GetFolderPath('Desktop'), "
            "[Environment]::GetFolderPath('Programs')) ) { "
            "$s = $w.CreateShortcut((Join-Path $d ($env:SZA_NAME + '.lnk'))); "
            "$s.TargetPath = $env:SZA_TARGET; $s.WorkingDirectory = $env:SZA_DIR; "
            "$s.IconLocation = $env:SZA_TARGET + ',0'; $s.Description = 'SubZeroAstro'; $s.Save() }")


def mac_osa() -> str:
    """AppleScript that makes (or replaces) a Finder alias to the app on the Desktop; the app's path is argv 1."""
    return ('on run argv\n'
            '  set a to POSIX file (item 1 of argv)\n'
            '  tell application "Finder"\n'
            f'    if exists alias file "{NAME}" of desktop then delete alias file "{NAME}" of desktop\n'
            '    set f to make alias file to a at desktop\n'
            f'    set name of f to "{NAME}"\n'
            '  end tell\n'
            'end run')


def make(settings=None) -> tuple:
    """Make the shortcut. Returns (ok, plain words for the person)."""
    t = app_target()
    if t is None:
        return False, "SubZeroAstro is running from its Python source here, so there is no app to make a shortcut to."
    try:
        if sys.platform == "win32":
            env = dict(os.environ, SZA_TARGET=str(t), SZA_DIR=str(t.parent), SZA_NAME=NAME)
            r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", windows_ps(t)],
                               env=env, capture_output=True, text=True, timeout=60, creationflags=0x08000000)
            ok = r.returncode == 0
            msg = ("A <b>SubZeroAstro</b> shortcut is on your Desktop and in the Start menu. It opens this copy:<br>"
                   f"{t}" if ok else f"Windows couldn't make the shortcut ({(r.stderr or r.stdout).strip()[:200]})")
        elif sys.platform == "darwin":
            r = subprocess.run(["osascript", "-e", mac_osa(), str(t)], capture_output=True, text=True, timeout=60)
            ok = r.returncode == 0
            msg = ("A <b>SubZeroAstro</b> alias is on your Desktop. It opens this copy:<br>"
                   f"{t}<br><br>To keep it in the Dock too: while SubZeroAstro is open, right-click its icon in "
                   "the Dock › Options › <b>Keep in Dock</b>." if ok
                   else f"The Mac couldn't make the alias ({(r.stderr or r.stdout).strip()[:200]}) - if it asked "
                        "about controlling Finder, allow it and try again")
        else:
            return False, "Shortcuts are made on Windows and macOS only."
    except (OSError, subprocess.SubprocessError) as e:
        return False, f"The shortcut couldn't be made ({e})"
    if ok and settings is not None:
        settings.setValue(K_TARGET, str(t))
    return ok, msg


def keep_current(settings) -> str:
    """At start: a shortcut made here that points at another folder's copy is pointed at this one."""
    made = settings.value(K_TARGET, "") or ""
    t = app_target()
    if not made or t is None or made == str(t):
        return ""
    ok, _ = make(settings)
    return "moved" if ok else "failed"
