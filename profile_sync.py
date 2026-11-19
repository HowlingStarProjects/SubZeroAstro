"""
profile_sync.py - My Profile: the same settings on every computer, kept in one small file on the NAS.

Settings › My Profile… › Use a Profile on My NAS. From then on each computer copies its shared settings (My Rigs,
location, Organize choices, the ASIAIR list, the N.I.N.A. computers, Target Picker and weather, processing
defaults) to "SubZeroAstro Profile.json" in the chosen NAS folder, and takes what the other computers changed - at
start, every minute while SubZeroAstro is open, when its window comes to the front and when it closes. A setting
changed on two computers at once: the last change wins, setting by setting (a change of the rigs on one computer
and of the location on the other both survive).

What stays on each computer: where the helper programs are (PixInsight, ASTAP, GraXpert, RC-Astro), window sizes,
the crash-recovery folder, and everything about the automatic jobs (Organize Automatically, the overnight ASIAIR
fetch, the automatic N.I.N.A. clean-up) - those run on one computer only (one_computer.py).

Windows and Mac together: a NAS folder is \\\\astronas\\Astro\\... (or Z:\\... for a mapped drive) on Windows and
/Volumes/Astro/... on a Mac. The profile stores such places in a form both understand (smb://astronas/Astro/...)
and each computer writes them back its own way. A folder on one computer's own disk (C:\\Astro, /Users/brian/Astro)
means nothing to the other kind of computer: there the computer keeps its own choice.

Export My Settings / Import My Settings: the same settings plus this computer's own (tool locations, schedules) in a
file, for a new computer or a backup. An import never switches an automatic job on.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

PROFILE_FILE = "SubZeroAstro Profile.json"
FORMAT = 1
K_FOLDER = "profile/folder"            # the NAS folder holding the profile, as this computer sees it ("" = off)
K_BASE_LOCAL = "profile/base_local"    # the shared settings here after the last sync (to see what changed here)
K_BASE_REMOTE = "profile/base_remote"  # the profile's settings at the last sync (to see what changed elsewhere)
K_LAST = "profile/last"                # {"at", "ok", "msg", "pulled", "pushed"} of the last sync

# shared: a key equal to an entry, or starting with an entry that ends in "/"
SHARED = ("organize/", "stack/", "picker/", "af/", "astrobin/", "finish/", "prep/", "nina/", "weather/",
          "asiair/list", "asiair/incoming", "asiair/free", "nina_clean/list",
          "load/subfolders", "measure_mode", "chart_metric", "advance", "checkpoints/days")
# ... except these, which belong to one computer
LOCAL = ("prep/astap", "prep/graxpert", "prep/rcastro", "prep/device", "finish/pi_dir", "finish/export_dir_last",
         "nina/profile_files", "organize/last_done", "organize/moved_before")
# Export My Settings also carries these of this computer's own (never the automatic jobs' on-switches)
EXPORT_EXTRA = ("prep/astap", "prep/graxpert", "prep/rcastro", "finish/pi_dir",
                "nina/profile_files", "checkpoints/dir", "ui/splash", "update/auto_check",
                "auto_org/src", "auto_org/dst", "auto_org/layout", "auto_org/freq", "auto_org/time", "asiair/every")
TOOL_KEYS = ("prep/astap", "prep/graxpert", "prep/rcastro", "finish/pi_dir")
NEVER_IMPORT = ("auto_org/on", "asiair/night", "asiair/with_auto", "nina_clean/with_auto")


def _match(key, entries):
    return any(key == e or (e.endswith("/") and key.startswith(e)) for e in entries)


def is_shared(key: str) -> bool:
    return _match(key, SHARED) and not _match(key, LOCAL)


def host() -> str:
    try:
        return socket.gethostname().split(".")[0]
    except OSError:
        return ""


def _now_iso():
    return _dt.datetime.now().astimezone().isoformat(timespec="seconds")


# ------------------------------------------------------------------ NAS places, Windows <-> Mac

_WIN_ABS = re.compile(r"^(?:[A-Za-z]:[\\/]|\\\\)")
_NEUTRAL = re.compile(r"^smb://([^/]*)/([^/]+)/?(.*)$", re.S)


def _clean_server(s: str) -> str:
    s = (s or "").split("@")[-1]
    s = re.sub(r"\._(smb|afpovertcp|nfs)\._tcp\.local\.?$", "", s, flags=re.I)
    s = re.sub(r"\.local\.?$", "", s, flags=re.I)
    return s.split(":")[0]


class Places:
    """How this computer reaches network shares. plat 'win' or 'posix' (Mac); drives {'Z': (server, share)};
    mounts [(mount point, server, share)]."""

    def __init__(self, plat=None, drives=None, mounts=None):
        self.plat = plat or ("win" if sys.platform.startswith("win") else "posix")
        self.drives = {k.upper(): v for k, v in (drives or {}).items()}
        self.mounts = list(mounts or [])

    @classmethod
    def here(cls) -> "Places":
        if sys.platform.startswith("win"):
            return cls("win", drives=_win_drives())
        return cls("posix", mounts=_posix_mounts())

    def is_path(self, s) -> bool:
        return isinstance(s, str) and bool(_WIN_ABS.match(s) or s.startswith("/"))

    def to_neutral(self, s: str) -> str:
        """A path as stored in the profile: network places as smb://server/share/rest, others unchanged."""
        if not isinstance(s, str) or not s:
            return s
        if self.plat == "win":
            m = re.match(r"^\\\\([^\\/]+)[\\/]+([^\\/]+)[\\/]*(.*)$", s, re.S)
            if m:
                return f"smb://{m.group(1)}/{m.group(2)}/" + m.group(3).replace("\\", "/")
            m = re.match(r"^([A-Za-z]):[\\/]*(.*)$", s, re.S)
            if m and m.group(1).upper() in self.drives:
                server, share = self.drives[m.group(1).upper()]
                return f"smb://{server}/{share}/" + m.group(2).replace("\\", "/")
            return s
        best = None
        for mp, server, share in self.mounts:
            mp = mp.rstrip("/")
            if (s == mp or s.startswith(mp + "/")) and (best is None or len(mp) > len(best[0])):
                best = (mp, server, share)
        if best:
            return f"smb://{best[1]}/{best[2]}/" + s[len(best[0]):].lstrip("/")
        return s

    def from_neutral(self, s: str):
        """A profile value as this computer writes it, or None for a place on the other kind of computer's own
        disk (that one keeps its own choice)."""
        if not isinstance(s, str) or not s:
            return s
        m = _NEUTRAL.match(s)
        if m:
            server, share, rest = m.group(1), m.group(2), m.group(3).rstrip("/")
            if self.plat == "win":
                cands = [(d, sv) for d, (sv, sh) in sorted(self.drives.items()) if sh.lower() == share.lower()]
                pick = [d for d, sv in cands if _clean_server(sv).lower() == _clean_server(server).lower()] or \
                    [d for d, _sv in cands]
                if pick:
                    return f"{pick[0]}:\\" + rest.replace("/", "\\")
                if not server:
                    return None
                return f"\\\\{server}\\{share}" + ("\\" + rest.replace("/", "\\") if rest else "")
            cands = [(mp, sv) for mp, sv, sh in self.mounts if sh.lower() == share.lower()]
            pick = [mp for mp, sv in cands if _clean_server(sv).lower() == _clean_server(server).lower()] or \
                [mp for mp, _sv in cands]
            base = pick[0].rstrip("/") if pick else f"/Volumes/{share}"
            return base + ("/" + rest if rest else "")
        if self.plat == "win" and s.startswith("/"):
            return None
        if self.plat != "win" and _WIN_ABS.match(s):
            return None
        return s


def _win_drives() -> dict:
    """Mapped network drives: {'Z': ('astronas', 'Astro')}."""
    out = {}
    try:
        import ctypes
        from ctypes import wintypes
        mpr = ctypes.WinDLL("mpr")
        mask = ctypes.windll.kernel32.GetLogicalDrives()
        for i in range(26):
            if not mask & (1 << i):
                continue
            d = chr(65 + i)
            buf = ctypes.create_unicode_buffer(1024)
            n = wintypes.DWORD(1024)
            if mpr.WNetGetConnectionW(f"{d}:", buf, ctypes.byref(n)) == 0:
                m = re.match(r"^\\\\([^\\]+)\\([^\\]+)", buf.value)
                if m:
                    out[d] = (m.group(1), m.group(2))
    except Exception:  # noqa: BLE001 - no mapped drives known: UNC paths still work
        pass
    return out


def parse_mount_output(text: str) -> list:
    """`mount` lines like //brian@astronas._smb._tcp.local/Astro on /Volumes/Astro (smbfs, ...)."""
    out = []
    for line in (text or "").splitlines():
        m = re.match(r"^//([^/]+)/(.+?) on (/.+?) \((smbfs|afpfs|nfs|webdav|cifs)", line) or \
            re.match(r"^([^:/\s]+):/(.+?) on (/.+?) (?:type )?\(?(nfs)", line)
        if m:
            share = m.group(2).split("/")[-1]
            out.append((m.group(3), _clean_server(m.group(1)), _unquote(share)))
    return out


def _unquote(s):
    try:
        from urllib.parse import unquote
        return unquote(s)
    except Exception:  # noqa: BLE001
        return s


def _posix_mounts() -> list:
    try:
        r = subprocess.run(["mount"], capture_output=True, text=True, timeout=5)
        return parse_mount_output(r.stdout)
    except Exception:  # noqa: BLE001
        return []


def _walk(v, fn):
    """fn over every path-like string in a value (also inside JSON kept as text). Returns (value, ok); ok False
    when the value itself is a place this computer can't use."""
    if isinstance(v, str):
        t = v.strip()
        if t[:1] in "[{":
            try:
                inner = json.loads(t)
            except ValueError:
                inner = None
            if isinstance(inner, (list, dict)):
                return json.dumps(_walk_inner(inner, fn)), True
        r = fn(v)
        return (v, False) if r is None else (r, True)
    if isinstance(v, list):
        return _walk_inner(v, fn), True
    return v, True


def _walk_inner(v, fn):
    if isinstance(v, str):
        r = fn(v)
        return v if r is None else r
    if isinstance(v, list):
        return [_walk_inner(x, fn) for x in v]
    if isinstance(v, dict):
        return {k: _walk_inner(x, fn) for k, x in v.items()}
    return v


def _norm(v):
    """A settings value as plain JSON (Windows keeps true/false as text)."""
    if isinstance(v, str) and v in ("true", "false"):
        return v == "true"
    if isinstance(v, (bool, int, float, str)) or v is None:
        return v
    if isinstance(v, (list, tuple)):
        out = [_norm(x) for x in v]
        return out if all(isinstance(x, (bool, int, float, str)) or x is None for x in out) else _SKIP
    return _SKIP


_SKIP = object()


def neutral_values(settings, places: Places, keys=None) -> dict:
    """This computer's settings (shared ones unless keys are given), in profile form."""
    out = {}
    for k in settings.allKeys():
        if (k not in keys) if keys is not None else not is_shared(k):
            continue
        v = _norm(settings.value(k))
        if v is _SKIP:
            continue
        out[k] = _walk(v, places.to_neutral)[0]
    return out


def apply_values(settings, values: dict, places: Places) -> list:
    """Write profile values here (translated). Returns the keys written."""
    done = []
    for k, v in values.items():
        loc, ok = _walk(v, places.from_neutral)
        if not ok:
            continue
        settings.setValue(k, loc)
        done.append(k)
    return done


# ------------------------------------------------------------------ the profile file

def profile_path(folder) -> Path:
    return Path(folder) / PROFILE_FILE


def read_file(path) -> dict | None:
    try:
        d = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(d, dict) or d.get("app") != "SubZeroAstro" or not isinstance(d.get("settings"), dict):
        return None
    return d


def write_file(path, values: dict, kind="profile", extra=None, version=""):
    path = Path(path)
    d = {"app": "SubZeroAstro", "kind": kind, "format": FORMAT, "saved": _now_iso(), "saved_by": host(),
         "saved_with": version, "settings": dict(sorted(values.items()))}
    if extra:
        d.update(extra)
    tmp = path.with_name(path.name + f".{host() or 'tmp'}.{os.getpid()}.part")
    tmp.write_text(json.dumps(d, indent=1, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def _jload(settings, key):
    try:
        v = json.loads(settings.value(key, "") or "{}")
        return v if isinstance(v, dict) else {}
    except (TypeError, ValueError):
        return {}


def folder(settings) -> str:
    return (settings.value(K_FOLDER, "") or "").strip()


def last_status(settings) -> dict:
    return _jload(settings, K_LAST)


def _set_last(settings, ok, msg, pulled=(), pushed=()):
    settings.setValue(K_LAST, json.dumps({"at": time.time(), "ok": ok, "msg": msg, "pulled": list(pulled),
                                          "pushed": list(pushed), "host": host()}))


def sync(settings, places: Places | None = None, version="") -> dict:
    """One round: take what changed in the profile, give what changed here. Per setting, three-way: the last
    values seen here and in the profile tell which side changed; both changed - this computer's change wins."""
    places = places or Places.here()
    fol = folder(settings)
    if not fol:
        return {"ok": False, "msg": "no profile in use"}
    path = profile_path(fol)
    if not Path(fol).is_dir():
        _set_last(settings, False, f"can't reach the profile folder {fol}")
        return {"ok": False, "msg": f"can't reach {fol}"}
    prof = read_file(path)
    remote = dict(prof["settings"]) if prof else {}
    base_l, base_r = _jload(settings, K_BASE_LOCAL), _jload(settings, K_BASE_REMOTE)
    local = neutral_values(settings, places)
    pulled, pushed, merged = [], [], dict(remote)
    for k in set(local) | set(remote) | set(base_l) | set(base_r):
        here_changed = local.get(k, _SKIP) != base_l.get(k, _SKIP)
        there_changed = remote.get(k, _SKIP) != base_r.get(k, _SKIP)
        if here_changed:
            if k in local:
                if remote.get(k, _SKIP) != local[k]:
                    merged[k] = local[k]
                    pushed.append(k)
            elif k in remote:
                merged.pop(k, None)
                pushed.append(k)
        elif there_changed:
            if k in remote:
                if apply_values(settings, {k: remote[k]}, places):
                    pulled.append(k)
            elif k in local:
                settings.remove(k)
                pulled.append(k)
    if pushed or prof is None:
        try:
            write_file(path, merged, version=version)
        except OSError as e:
            _set_last(settings, False, f"couldn't save the profile: {e}")
            return {"ok": False, "msg": str(e)}
    settings.setValue(K_BASE_LOCAL, json.dumps(neutral_values(settings, places)))
    settings.setValue(K_BASE_REMOTE, json.dumps(merged))
    _set_last(settings, True, "", pulled, pushed)
    try:
        settings.sync()
    except Exception:  # noqa: BLE001
        pass
    return {"ok": True, "pulled": sorted(pulled), "pushed": sorted(pushed)}


def backup_dir() -> Path:
    if sys.platform.startswith("win"):
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "SubZeroAstro"
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / "SubZeroAstro"
    else:
        base = Path.home() / ".local" / "share" / "SubZeroAstro"
    return base / "Settings backups"


def start_using(settings, fol, places: Places | None = None, version="", backup=True) -> dict:
    """Use the profile in fol: a new one from this computer's settings, or join the one there (its settings
    replace this computer's shared ones; a backup of this computer's settings is saved first)."""
    places = places or Places.here()
    fol = str(fol)
    path = profile_path(fol)
    prof = read_file(path)
    if path.exists() and prof is None:
        return {"ok": False, "msg": f"{path.name} in that folder can't be read - is it a SubZeroAstro profile?"}
    saved = None
    if backup:
        try:
            saved = export_settings(settings, backup_dir() / f"Before profile {_dt.date.today()} "
                                                              f"{time.strftime('%H%M%S')}.json", places, version)
        except OSError:
            saved = None
    settings.setValue(K_FOLDER, fol)
    # a new profile starts from everything here; joining one takes everything there
    settings.setValue(K_BASE_LOCAL, "{}" if prof is None else json.dumps(neutral_values(settings, places)))
    settings.setValue(K_BASE_REMOTE, "{}")
    r = sync(settings, places, version)
    r["new"] = prof is None
    r["backup"] = str(saved) if saved else ""
    return r


def stop_using(settings):
    """This computer keeps the settings it has now and stops following the profile."""
    for k in (K_FOLDER, K_BASE_LOCAL, K_BASE_REMOTE, K_LAST):
        settings.remove(k)


# ------------------------------------------------------------------ Export / Import My Settings

def export_settings(settings, path, places: Places | None = None, version="") -> Path:
    places = places or Places.here()
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    vals = neutral_values(settings, places)
    vals.update(neutral_values(settings, places, keys=set(EXPORT_EXTRA)))
    write_file(path, vals, kind="export", version=version,
               extra={"computer": host(), "platform": places.plat})
    return path


def import_settings(settings, path, places: Places | None = None) -> dict:
    """Settings from an exported file. Tool locations only if the program is there on this computer; an
    automatic job is never switched on."""
    places = places or Places.here()
    d = read_file(path)
    if d is None:
        return {"ok": False, "msg": "That file isn't a SubZeroAstro settings file."}
    vals, skipped = {}, []
    for k, v in d["settings"].items():
        if k in NEVER_IMPORT or k.startswith("profile/") or not (is_shared(k) or k in EXPORT_EXTRA):
            continue
        if k in TOOL_KEYS:
            loc, ok = _walk(v, places.from_neutral)
            if not ok or not loc or not Path(str(loc)).exists():
                skipped.append(k)
                continue
        vals[k] = v
    done = apply_values(settings, vals, places)
    skipped += [k for k in vals if k not in done]
    try:
        settings.sync()
    except Exception:  # noqa: BLE001
        pass
    return {"ok": True, "imported": sorted(done), "skipped": sorted(skipped), "from": d.get("computer") or
            d.get("saved_by") or "", "saved": d.get("saved", "")}


# ------------------------------------------------------------------ running in the app

class Syncer:
    """Keeps this computer and the profile in step while SubZeroAstro is open. The file work runs in a thread
    (a NAS that has gone to sleep must not freeze the window), on its own settings object."""

    EVERY_S = 60
    FRONT_MIN_S = 15

    def __init__(self, store_factory, version=""):
        self.store_factory = store_factory
        self.version = version
        self.thread = None
        self.last_try = 0.0
        self.result = None
        self.listeners = []

    def active(self) -> bool:
        try:
            return bool(folder(self.store_factory()))
        except Exception:  # noqa: BLE001
            return False

    def start(self, force=False):
        if self.thread is not None and self.thread.is_alive():
            return self.thread
        if not force and time.time() - self.last_try < self.FRONT_MIN_S:
            return None
        if not self.active():
            return None
        self.last_try = time.time()
        self.thread = threading.Thread(target=self._run, daemon=True, name="profile-sync")
        self.thread.start()
        return self.thread

    def _run(self):
        try:
            self.result = sync(self.store_factory(), version=self.version)
        except Exception as e:  # noqa: BLE001 - a sync problem never stops the app
            self.result = {"ok": False, "msg": str(e)}
        try:
            import session_log
            r = self.result or {}
            if r.get("pulled") or r.get("pushed") or not r.get("ok"):
                session_log.write("profile", f"sync: {r}")
        except Exception:  # noqa: BLE001
            pass

    def wait(self, seconds) -> bool:
        t = self.thread
        if t is None:
            return True
        t.join(seconds)
        return not t.is_alive()


def describe_last(settings) -> str:
    st = last_status(settings)
    if not st:
        return "not synced yet"
    when = time.strftime("%d %b %H:%M", time.localtime(st.get("at", 0)))
    if not st.get("ok"):
        return f"{when}: {st.get('msg') or 'not reached'}"
    n_in, n_out = len(st.get("pulled") or []), len(st.get("pushed") or [])
    bits = []
    if n_in:
        bits.append(f"took {n_in} change{'s' if n_in != 1 else ''} from your other computers")
    if n_out:
        bits.append(f"saved {n_out} change{'s' if n_out != 1 else ''} made here")
    return f"{when}: in step" + (" - " + ", ".join(bits) if bits else "")
