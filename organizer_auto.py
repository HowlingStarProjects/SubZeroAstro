"""
organizer_auto.py - Organize My Astro Files on a schedule, with nobody at the computer.

A folder where new frames arrive (for example the NAS folder N.I.N.A.'s copies land in) is organized into your
library every day, twice a week or once a week: the same rules as the Organize window (Your Rigs, the layout you
chose), always a move, so the incoming folder empties out. Files that can't be filed - no rig matches, a
duplicate, unreadable - stay where they are and are listed in the run's report. Every run can be undone with
Undo Last Run in the Organize window.

The schedule is the computer's own: Windows Task Scheduler (schtasks) or a macOS LaunchAgent, which starts
SubZeroAstro with --auto-organize; it runs without a window and quits when done.
"""
from __future__ import annotations

import json
import os
import plistlib
import re
import subprocess
import sys
import time
from pathlib import Path

import organizer as O

TASK_NAME = "SubZeroAstro Auto-Organize"
MAC_LABEL = "io.github.bapoteet.subzeroastro.autoorganize"
SETTLE_MIN = 60            # a file changed in the last hour may still be arriving: it waits for the next run
LOCK_STALE_H = 12
AUTO_DUPS_MIN = 45        # at most this long comparing duplicates byte for byte; the rest next time
AUTO_FETCH_MIN = 60       # Organize Automatically starts no new ASIAIR sub after this long; the rest come next time
STATUS_EVERY_S = 3         # a running run says where it is at most this often (the windows look every few seconds)
STATUS_QUIET_MIN = 60      # a run on another computer that hasn't said anything for this long has stopped
FREQS = {"daily": "Every day", "twice": "Twice a week (Monday and Thursday)", "weekly": "Once a week (Sunday)"}
_DAYS = {"twice": ["MON", "THU"], "weekly": ["SUN"]}
_MAC_DAY = {"SUN": 0, "MON": 1, "TUE": 2, "WED": 3, "THU": 4, "FRI": 5, "SAT": 6}


def _settings():
    from PySide6.QtCore import QSettings
    return QSettings("FITSBlasterWin", "FITSBlasterWin")


def load_config(settings=None) -> dict:
    s = settings or _settings()
    return {"src": s.value("auto_org/src", "") or "", "dst": s.value("auto_org/dst", "") or "",
            "layout": s.value("auto_org/layout", "") or s.value("organize/layout", "rig") or "rig",
            "freq": s.value("auto_org/freq", "daily") or "daily", "time": s.value("auto_org/time", "11:00") or "11:00",
            "on": s.value("auto_org/on", False, type=bool),
            "jpgs": s.value("organize/delete_jpgs", True, type=bool),
            "empty": s.value("organize/delete_empty", False, type=bool),
            "other": s.value("organize/delete_other", False, type=bool),
            "dups": s.value("auto_org/delete_dups", False, type=bool)}


def save_config(cfg, settings=None):
    s = settings or _settings()
    for k in ("src", "dst", "layout", "freq", "time", "on"):
        if k in cfg:
            s.setValue(f"auto_org/{k}", cfg[k])


def problems(cfg) -> list:
    """Why a run can't go ahead (empty = fine)."""
    out = []
    src, dst = cfg.get("src", "").strip(), cfg.get("dst", "").strip()
    if not src:
        out.append("no incoming folder chosen")
    if not dst:
        out.append("no library folder chosen")
    if src and dst:
        a, b = os.path.normcase(os.path.abspath(src)), os.path.normcase(os.path.abspath(dst))
        org = os.path.normcase(os.path.abspath(str(O.org_base(dst))))
        if a == b or a == org or a.startswith(org + os.sep) or org.startswith(a + os.sep):
            out.append("the incoming folder must be outside the organized library (to re-sort the library itself, "
                       "use Change My Library's Layout in the Organize window)")
    return out


def _note(text):
    """A line in the session log (so Zip My Logs shows which files an automatic run left, and why)."""
    try:
        import session_log
        session_log.write("organizer", text)
    except Exception:  # noqa: BLE001
        pass


def run(cfg=None, settings=None, progress=None, now=None) -> dict:
    """One automatic run. Returns a report {time, filed, left: {why: n}, log, error}; also saved as text in the
    library's organize folder and as the 'last run' line shown in the app."""
    s = settings or _settings()
    cfg = cfg or load_config(s)
    stamp = time.strftime("%Y-%m-%d %H:%M")
    rep = {"time": stamp, "filed": 0, "left": {}, "log": "", "error": ""}
    outer = progress
    say = _Status(s, stamp, outer, quiet=running_status(s) is not None)   # another run going: don't talk over it
    say("Starting - checking the folders", force=True)

    def progress(text, i=None, n=None, force=False):
        say(text, i, n, force)
    bad = problems(cfg)
    src = Path(cfg.get("src", ""))
    for k in ("src", "dst"):                         # a Mac: NAS folders must be mounted - remember / reconnect
        if cfg.get(k):
            ensure_mounted(cfg[k], s)
    lib0 = Path(cfg.get("dst", ""))
    if not bad and not src.is_dir():
        bad = [f"the incoming folder {src} can't be reached ({_unreachable(src)})"]
    if not bad and not lib0.is_dir():
        bad = [f"your library {lib0} can't be reached ({_unreachable(lib0)})"]
    riglist = []
    try:
        riglist = json.loads(s.value("organize/riglist", "[]") or "[]")
    except (TypeError, ValueError):
        pass
    if not bad and not riglist:
        bad = ["Your Rigs is empty - set up your rigs once in the Organize window"]
    if not bad:                                      # another computer took the automatic jobs over: step aside
        try:
            import one_computer as J
            why = J.check_run(str(lib0), "organize", cfg.get("on", False))
            if why:
                bad = [why]
                _note(f"automatic run {why}")
        except Exception:  # noqa: BLE001
            pass
    if bad:
        rep["error"] = "; ".join(bad)
        _finish(rep, s, None)
        return rep
    lib = Path(cfg["dst"])
    base = O.org_base(lib)
    lock = base / O.LOG_DIR / "auto_organize.lock"
    try:
        lock.parent.mkdir(parents=True, exist_ok=True)
        if lock.exists() and time.time() - lock.stat().st_mtime < LOCK_STALE_H * 3600 and _lock_alive(lock):
            rep["error"] = "another automatic run is still going"
            _finish(rep, s, None)
            return rep
        lock.write_text(f"{stamp}|{os.getpid()}|{_host()}", encoding="utf-8")
        say.quiet = False
    except OSError as e:
        rep["error"] = f"the library {lib} can't be written ({e})"
        _finish(rep, s, None)
        return rep
    try:
        try:
            tol = float(s.value("organize/rig_tol", 3)) / 100.0
        except (TypeError, ValueError):
            tol = 0.03
        if s.value("asiair/with_auto", False, type=bool):       # the ASIAIRs' new subs first
            try:
                import asiair as A
                units = A.load_units(s)
                inc = (s.value("asiair/incoming", "") or "").strip() or str(src)
                if units:
                    progress(f"Fetching new subs from your {len(units)} ASIAIR(s)", force=True)
                    # an hour at most, then on to organizing: a slow or stuck ASIAIR's other subs come over
                    # next time instead of holding up the whole morning run
                    reps = A.fetch_all(units, inc, free=s.value("asiair/free", False, type=bool),
                                       progress=lambda t, i, n: progress(t),
                                       deadline=time.time() + AUTO_FETCH_MIN * 60)
                    rep["asiair"] = A.describe(reps).replace("\n", "; ")
                    rep["asiair_lines"] = A.describe(reps).splitlines()
                    try:
                        A.record_last(s, reps)
                    except Exception:  # noqa: BLE001
                        pass
            except Exception as e:  # noqa: BLE001
                rep["asiair"] = f"ASIAIR fetch failed: {e}"
        t_now = now if now is not None else time.time()
        progress("Looking through the incoming folder", force=True)
        paths, settling = [], 0
        for p in O.scan(src, progress=lambda t, k: progress(f"Looking through the incoming folder: {k:,} file(s) "
                                                             f"found so far")):
            try:
                if t_now - p.stat().st_mtime < SETTLE_MIN * 60:
                    settling += 1                       # still arriving, perhaps: next run
                    continue
            except OSError:
                continue
            paths.append(p)
        progress(f"{len(paths):,} file(s) to look at", force=True)
        items = O.plan(O.classify(paths, library=lib, layout=cfg.get("layout") or "rig", root=src,
                                  riglist=riglist, tol=tol,
                                  progress=lambda t, i: progress(
                                      f"Reading the files' headers: {min(i + 1, len(paths)):,} of {len(paths):,}")),
                       lib, progress=lambda t, i, n: progress(f"Checking for duplicates: {i:,} of {n:,}", i, n))
        todo = [i for i in items if i.status == "move"]
        if cfg.get("jpgs", True):
            rep["jpgs"] = O.delete_previews(items, lib, roots=[src])   # the ASIAIR's .jpg previews
        if cfg.get("empty", False):                          # frames that are all zeros (never written)
            gone = O.delete_empty(items)
            rep["empty"] = len(gone)
            for g in gone:
                _note(f"deleted empty frame (all zeros): {g}")
        if todo:
            progress(f"Moving {len(todo):,} file(s) into your library", force=True)
            log = O.execute(items, lib, move=True, keep=[src],
                            progress=lambda t, i, n: progress(f"Moving files into your library: {i:,} of {n:,}",
                                                              i, n))
            rep["log"] = str(log)
            try:
                d = json.loads(log.read_text(encoding="utf-8"))
                rep["filed"] = len(d.get("done", []))
                if cfg.get("other", False):                  # everything left that isn't a frame
                    gone = O.delete_other([src], lib, min_age_s=SETTLE_MIN * 60)
                    rep["other"] = len(gone)
                    for g in gone:
                        _note(f"deleted (not a frame): {g}")
                if d.get("failed"):
                    rep["left"]["could not be copied (see the log)"] = len(d["failed"])
                    _note(f"could not be copied ({len(d['failed'])}; full list in {log}):")
                    for fl in d["failed"][:40]:
                        _note(f"  {fl.get('src')}  -  {fl.get('error')}")
            except (OSError, ValueError):
                rep["filed"] = len(todo)
        if cfg.get("dups", False):        # you said so: exact twins of frames already in the library go
            base_n = os.path.normcase(str(O.org_base(lib))) + os.sep
            dups = [i for i in O.duplicates(items)
                    if os.path.normcase(str(i.same_as)).startswith(base_n)]
            if dups:
                progress(f"Deleting {len(dups):,} exact duplicate(s) of frames in your library", force=True)
                t_end = time.time() + AUTO_DUPS_MIN * 60
                try:
                    n_del, _kept, dlog = O.handle_duplicates(
                        dups, lib, delete=True, leave_unlike=True, stop=lambda: time.time() > t_end,
                        progress=lambda t, i, n: progress(f"Checking duplicates byte for byte: {i:,} of {n:,}",
                                                          i, n))
                    rep["dups"] = n_del
                    if n_del:
                        _note(f"deleted {n_del} exact duplicate(s) of frames already in the library "
                              f"(list in {dlog})")
                except OSError as e:
                    _note(f"exact duplicates not deleted: {e}")
        unread = [i for i in items if i.status == "error"]
        if unread:
            _note(f"could not be read ({len(unread)}):")
            for i in unread[:40]:
                _note(f"  {i.src}  -  {i.why}")
        for i in items:
            if i.status in ("move", "here", "handled"):
                continue
            why = {"norig": "no rig matched - Your Rigs / Which Rig? in the Organize window",
                   "duplicate": ("duplicates - Step 2 in the Organize window" if not cfg.get("dups") else
                                 "duplicates that aren't exact copies - Step 2 in the Organize window"),
                   "error": "could not be read"}.get(i.status, i.why or i.status)
            rep["left"][why] = rep["left"].get(why, 0) + 1
        if settling:
            rep["left"][f"changed in the last {SETTLE_MIN} min (next run)"] = settling
        if s.value("nina_clean/with_auto", False, type=bool):    # then free space on the N.I.N.A. computers
            try:
                import nina_cleanup as NC
                progress("Cleaning up your N.I.N.A. computers", force=True)
                txt = NC.run(s)
                if txt:
                    rep["nina"] = txt
                    _note(f"N.I.N.A. clean-up: {txt}")
            except Exception as e:  # noqa: BLE001
                import win_signin as WS
                rep["nina"] = f"N.I.N.A. clean-up failed: {WS.plain(e) if isinstance(e, OSError) else e}"
    except Exception as e:  # noqa: BLE001 - a scheduled run must report, never hang
        rep["error"] = str(e)
    finally:
        try:
            lock.unlink()
        except OSError:
            pass
    _finish(rep, s, base)
    return rep


class _Status:
    """Where a run is, for the windows: saved in the settings (auto_org/now) every few seconds, so the Organize
    window and Organize Automatically can show 'Running now…' with its progress - the run is its own process.
    Written through its own settings object: the run may be on a thread of the app while the window reads."""

    def __init__(self, s, stamp, outer=None, quiet=False):
        self.s, self.stamp, self.outer, self.quiet = s, stamp, outer, quiet
        self.t0, self.last = time.time(), 0.0

    def __call__(self, text, i=None, n=None, force=False):
        if self.outer:
            try:
                self.outer(text)
            except Exception:  # noqa: BLE001
                pass
        now = time.time()
        if self.quiet or (not force and now - self.last < STATUS_EVERY_S):
            return
        self.last = now
        _write_status(self.s, {"started": self.stamp, "t0": self.t0, "step": text, "at": now,
                               "pid": os.getpid(), "host": _host()})


def _store(s):
    """A second settings object on the same store. The usual (native) settings are opened by name - on a Mac by
    path they would bypass the system's settings service, and the window would never see the change."""
    from PySide6.QtCore import QSettings
    try:
        if s.format() == QSettings.NativeFormat and s.applicationName():
            return QSettings(QSettings.NativeFormat, s.scope(), s.organizationName(), s.applicationName())
        return QSettings(s.fileName(), s.format())
    except Exception:  # noqa: BLE001
        return s


def _write_status(s, st):
    try:
        st_ = _store(s)
        if st is None:
            st_.remove("auto_org/now")
        else:
            st_.setValue("auto_org/now", json.dumps(st))
        st_.sync()
    except Exception:  # noqa: BLE001 - never stop a run over a status line
        pass


def running_status(settings, now=None):
    """The run going right now ({started, t0, step, at, pid, host}), or None. A run that was stopped (SubZeroAstro
    closed, the computer switched off) leaves its line behind; it doesn't count once its process is gone, or - for
    a run on another computer - once it has been quiet for STATUS_QUIET_MIN minutes."""
    try:
        st = json.loads(settings.value("auto_org/now", "") or "null")
    except (TypeError, ValueError):
        return None
    if not isinstance(st, dict):
        return None
    now = now if now is not None else time.time()
    try:
        age = now - float(st.get("at", 0))
        if now - float(st.get("t0", st.get("at", 0))) > LOCK_STALE_H * 3600:
            return None
        if st.get("host") and st.get("host") != _host():
            return st if age < STATUS_QUIET_MIN * 60 else None
        return st if _pid_alive(int(st.get("pid", 0))) else None
    except (TypeError, ValueError):
        return None


def describe_running(st, now=None) -> str:
    now = now if now is not None else time.time()
    mins = max(0, int((now - float(st.get("t0", now))) // 60))
    took = "just started" if mins < 1 else f"{mins} min so far"
    t = time.strftime("%H:%M", time.localtime(float(st.get("at", now))))
    where = "" if not st.get("host") or st.get("host") == _host() else f" on {st['host']}"
    began = str(st.get("started", ""))
    if began[:10] == time.strftime("%Y-%m-%d", time.localtime(now)):
        began = began[11:]                                 # today: just the time
    return f"Running now{where} (started {began}, {took}): {st.get('step', '')} - as of {t}"


def _host() -> str:
    import socket
    try:
        return socket.gethostname()
    except OSError:
        return ""


def _pid_alive(pid) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes
        k = ctypes.windll.kernel32
        h = k.OpenProcess(0x1000, False, pid)          # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        code = ctypes.c_ulong()
        ok = k.GetExitCodeProcess(h, ctypes.byref(code))
        k.CloseHandle(h)
        return bool(ok) and code.value == 259        # STILL_ACTIVE
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except OSError:
        return True


def _lock_alive(lock) -> bool:
    """A run's marker counts only while that run is really going: SubZeroAstro closed or the computer switched off
    mid-run leaves the marker behind, and it must not block the next run. A run on another computer (the same
    library on a NAS) can't be checked from here, so it counts until it is stale."""
    try:
        parts = lock.read_text(encoding="utf-8").split("|")
        if len(parts) >= 3 and parts[2] and parts[2] != _host():
            return True
        return _pid_alive(int(parts[1])) if len(parts) >= 2 else False
    except (OSError, ValueError):
        return False


def _volume(path):
    """macOS: '/Volumes/AstroDataOrganizedSAZ/x' -> '/Volumes/AstroDataOrganizedSAZ' (else None)."""
    parts = Path(str(path)).parts
    if sys.platform == "darwin" and len(parts) >= 3 and parts[1] == "Volumes":
        return str(Path(*parts[:3]))
    return None


def _unreachable(path) -> str:
    vol = _volume(path)
    if vol and not Path(vol).exists():
        return (f"the NAS folder {Path(vol).name} isn't connected on this Mac - Finder › Go › Connect to Server…, "
                f"or add it to System Settings › General › Login Items so it connects at login")
    return "is the NAS on and connected?"


def mount_urls(text) -> dict:
    """`mount` output -> {'/Volumes/Share': 'smb://user@server/Share'} for the network shares mounted now."""
    out = {}
    for line in (text or "").splitlines():
        m = re.match(r"^//(\S+?) on (/Volumes/.+?) \((smbfs|afpfs|nfs)", line)
        if m:
            scheme = {"smbfs": "smb", "afpfs": "afp", "nfs": "nfs"}[m.group(3)]
            out[m.group(2)] = f"{scheme}://{m.group(1)}"
    return out


def ensure_mounted(path, settings=None) -> bool:
    """macOS: a library or incoming folder on a NAS lives under /Volumes/<share>, which exists only while the
    share is connected. Remember how each share was connected; if it isn't connected at run time, reconnect it
    (the password comes from the keychain, as when Finder connects). Windows network paths need nothing."""
    vol = _volume(path)
    if not vol:
        return True
    s = settings
    key = "auto_org/mount/" + Path(vol).name
    try:
        r = subprocess.run(["mount"], capture_output=True, text=True, timeout=15)
        urls = mount_urls(r.stdout)
    except (OSError, subprocess.SubprocessError):
        urls = {}
    if vol in urls:
        if s is not None:
            s.setValue(key, urls[vol])
        return True
    if Path(vol).exists():
        return True
    url = s.value(key, "") if s is not None else ""
    if not url:
        return False
    try:
        subprocess.run(["osascript", "-e", f'mount volume "{url}"'], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        pass
    for _ in range(20):
        if Path(vol).exists():
            return True
        time.sleep(0.5)
    return False


def describe(rep) -> str:
    if rep.get("error"):
        return f"{rep['time']}: not run - {rep['error']}"
    t = f"{rep['time']}: {rep['filed']} file(s) filed"
    if rep.get("asiair"):
        t += f" [ASIAIRs: {rep['asiair']}]"
    if rep.get("other"):
        t += f", {rep['other']} other file(s) deleted"
    if rep.get("empty"):
        t += f", {rep['empty']} empty frame(s) deleted"
    if rep.get("jpgs"):
        t += f", {rep['jpgs']} .jpg preview(s) deleted"
    if rep.get("dups"):
        t += f", {rep['dups']} exact duplicate(s) deleted"
    if rep["left"]:
        t += "; left in the incoming folder: " + ", ".join(f"{n} {w}" for w, n in rep["left"].items())
    if rep.get("nina"):
        t += f" [N.I.N.A. computers: {rep['nina']}]"
    return t


def describe_html(rep) -> str:
    """The last run for the Organize window: one thing per line, and the ASIAIR advice once, not after each one."""
    from html import escape
    if rep.get("error"):
        return escape(f"{rep['time']}: not run - {rep['error']}")
    head = f"{rep['time']}: {rep['filed']:,} file(s) filed"
    for k, what in (("other", "other file(s) deleted"), ("empty", "empty frame(s) deleted"),
                    ("jpgs", ".jpg preview(s) deleted"), ("dups", "exact duplicate(s) deleted")):
        if rep.get(k):
            head += f", {rep[k]:,} {what}"
    lines = [escape(head)]
    hint = ""
    for a in (rep.get("asiair_lines") or ([rep["asiair"]] if rep.get("asiair") else [])):
        cut = a.find(" - is the ASIAIR on")
        if cut > 0:
            hint, a = a[cut + 3:], a[:cut]
        lines.append("&nbsp;&nbsp;ASIAIR " + escape(a))
    if hint:
        lines.append("&nbsp;&nbsp;" + escape("Not reached? I" + hint[1:]))
    if rep.get("left"):
        lines.append(escape("Left in the incoming folder: " + ", ".join(f"{n:,} {w}" for w, n in rep["left"].items())))
    if rep.get("nina"):
        lines.append(escape(f"N.I.N.A. computers: {rep['nina']}"))
    return "<br>".join(lines)


def _finish(rep, s, base):
    text = describe(rep)
    try:
        s.setValue("auto_org/last", text)
        s.setValue("auto_org/last_time", rep.get("time", ""))
        s.setValue("auto_org/last_html", describe_html(rep))
        s.sync()
    except Exception:  # noqa: BLE001
        pass
    try:                                              # no longer running - unless it's another run's line
        st = json.loads(_store(s).value("auto_org/now", "") or "null")
        if isinstance(st, dict) and st.get("pid") == os.getpid() and st.get("host") == _host():
            _write_status(s, None)
    except (TypeError, ValueError):
        pass
    if base is not None:
        try:
            with open(base / O.LOG_DIR / "auto_organize_history.txt", "a", encoding="utf-8") as f:
                f.write(text + "\n")
        except OSError:
            pass


# ------------------------------------------------------------------ the computer's scheduler
def _command() -> list:
    """How the scheduler starts SubZeroAstro: the installed app, or Python + the script when run from source."""
    if getattr(sys, "frozen", False):
        return [sys.executable, "--auto-organize"]
    return [sys.executable, str(Path(__file__).resolve().parent / "subzeroastro.py"), "--auto-organize"]


def _hhmm(t) -> tuple:
    try:
        h, m = (int(x) for x in str(t).split(":")[:2])
        return max(0, min(23, h)), max(0, min(59, m))
    except ValueError:
        return 11, 0


def windows_args(cfg) -> list:
    cmd = _command()
    tr = " ".join(f'"{c}"' if " " in c or c.endswith(".exe") else c for c in cmd)
    h, m = _hhmm(cfg.get("time"))
    a = ["schtasks", "/Create", "/F", "/TN", TASK_NAME, "/TR", tr, "/ST", f"{h:02d}:{m:02d}"]
    days = _DAYS.get(cfg.get("freq"))
    a += ["/SC", "WEEKLY", "/D", ",".join(days)] if days else ["/SC", "DAILY"]
    return a


def mac_plist(cfg) -> dict:
    h, m = _hhmm(cfg.get("time"))
    days = _DAYS.get(cfg.get("freq"))
    when = ([{"Hour": h, "Minute": m, "Weekday": _MAC_DAY[d]} for d in days] if days else [{"Hour": h, "Minute": m}])
    return {"Label": MAC_LABEL, "ProgramArguments": _command(), "StartCalendarInterval": when,
            "RunAtLoad": False}


def _mac_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{MAC_LABEL}.plist"


def _run(args) -> tuple:
    try:
        kw = {"capture_output": True, "text": True, "timeout": 60}
        if sys.platform == "win32":
            kw["creationflags"] = 0x08000000           # no console window flashing up
        r = subprocess.run(args, **kw)
        return r.returncode == 0, (r.stderr or r.stdout or "").strip()
    except (OSError, subprocess.SubprocessError) as e:
        return False, str(e)


def schedule(cfg) -> tuple:
    """Create (or replace) the scheduled task. Returns (ok, message)."""
    if sys.platform == "win32":
        ok, msg = _run(windows_args(cfg))
        return ok, (f"Windows Task Scheduler: '{TASK_NAME}' - {FREQS.get(cfg.get('freq'), '')} at "
                    f"{cfg.get('time')}" if ok else f"Windows Task Scheduler said: {msg}")
    if sys.platform == "darwin":
        p = _mac_path()
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            _run(["launchctl", "unload", str(p)])
            with open(p, "wb") as f:
                plistlib.dump(mac_plist(cfg), f)
        except OSError as e:
            return False, str(e)
        ok, msg = _run(["launchctl", "load", str(p)])
        return ok, (f"macOS: {FREQS.get(cfg.get('freq'), '')} at {cfg.get('time')}" if ok else msg)
    return False, "automatic runs are set up on Windows and macOS only"


def start_background() -> tuple:
    """Run Now: start one automatic run as its own process (no window), exactly like a scheduled run."""
    try:
        kw = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL,
              "close_fds": True}
        if sys.platform == "win32":
            kw["creationflags"] = 0x00000008 | 0x08000000 | 0x00000200   # detached, no window, new group
        else:
            kw["start_new_session"] = True
        subprocess.Popen(_command(), **kw)
        return True, ""
    except OSError as e:
        return False, str(e)


def command_text() -> str:
    return " ".join(_command())


def keep_schedule_current(settings=None) -> str:
    """Automatic organizing starts SubZeroAstro from the folder it was in when it was switched on. A new copy
    unzipped somewhere else (Downloads\\SubZeroAstro-Windows (4)...) would leave the morning run starting the old
    copy - or nothing, once that folder is deleted. At each start: on here, and the app has moved since? Point the
    schedule at this copy. Returns what was done ("" = nothing needed)."""
    s = settings or _settings()
    cfg = load_config(s)
    if not cfg["on"] or sys.platform not in ("win32", "darwin"):
        return ""
    now_cmd = command_text()
    if (s.value("auto_org/exe", "") or "") == now_cmd:
        return ""
    ok, msg = schedule(cfg)
    if ok:
        s.setValue("auto_org/exe", now_cmd)
        s.sync()
        _note(f"automatic organizing now starts {now_cmd}")
        return "moved"
    return f"failed: {msg}"


def unfinished(settings, now=None) -> str:
    """When the last automatic run that started, but never finished, began ("" = none): it was stopped part way -
    SubZeroAstro closed or updated, or the computer switched off - so it never wrote its 'Last automatic run'."""
    try:
        st = json.loads(settings.value("auto_org/now", "") or "null")
    except (TypeError, ValueError):
        return ""
    if not isinstance(st, dict) or running_status(settings, now) is not None:
        return ""
    began = str(st.get("started", ""))
    last = str(settings.value("auto_org/last_time", "") or "") or str(settings.value("auto_org/last", "") or "")[:16]
    return began if began and began > last else ""


def unschedule() -> tuple:
    if sys.platform == "win32":
        ok, msg = _run(["schtasks", "/Delete", "/F", "/TN", TASK_NAME])
        return True, ("removed" if ok else msg)
    if sys.platform == "darwin":
        p = _mac_path()
        _run(["launchctl", "unload", str(p)])
        try:
            p.unlink()
        except OSError:
            pass
        return True, "removed"
    return True, ""


def main_auto() -> int:
    """subzeroastro --auto-organize: one run, no window."""
    try:
        import session_log
        session_log.start("(automatic organize run)")
        session_log.write("organizer", "automatic run started")
    except Exception:  # noqa: BLE001
        pass
    rep = run()
    try:
        import session_log
        session_log.write("organizer", "automatic run: " + describe(rep))
    except Exception:  # noqa: BLE001
        pass
    return 1 if rep.get("error") else 0
