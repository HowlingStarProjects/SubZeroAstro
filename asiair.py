"""
asiair.py - fetch the night's subs from your ASIAIRs over the network, no USB stick.

An ASIAIR shares its storage on the network as Windows folders (\\\\<its IP>\\EMMC Images, \\Udisk Images …; its IP
is in the ASIAIR app's Wi-Fi settings). Fetch copies every FITS file that hasn't been fetched before into your
incoming folder, in a folder named after that ASIAIR's rig - so Organize files it under the right rig even when two
rigs have the same scope and camera. The .jpg previews are left behind. Each copy is checked (size) before it
counts; only then, and only if you ask, is the sub deleted from the ASIAIR to free its space. What was fetched is
remembered, so organizing (which empties the incoming folder) never makes a sub come over twice.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

SETTLE_S = 120                       # a sub changed in the last 2 minutes may still be being written
GUESS_SHARES = ("EMMC Images", "Udisk Images", "TF Images", "SD Images")
STALL_S = 90                         # no data at all from the ASIAIR for this long: it has stopped answering
CHUNK = 1 << 20                      # copied a megabyte at a time, so the status can say how far it has got
SAY_EVERY_S = 2.0                    # how often a long copy or look-through says where it is
_open = open                         # (the self-test swaps in a slow file to try out "stopped answering")


class Stalled(OSError):
    """The ASIAIR stopped sending in the middle of a sub (its Wi-Fi dropped, it was switched off or it hung)."""


class _Stopped(Exception):
    """Stop was pressed in the middle of a sub."""


def size_text(b) -> str:
    """1234567890 -> '1.2 GB'; 52000000 -> '52 MB'."""
    b = max(0, float(b or 0))
    if b >= 1e9:
        return f"{b / 1e9:.1f} GB"
    if b >= 1e6:
        return f"{b / 1e6:.0f} MB"
    return f"{b / 1e3:.0f} KB"


def time_text(s) -> str:
    """Seconds -> 'under a minute', 'about 12 min', 'about 1 h 20 min'."""
    s = max(0, float(s or 0))
    if s < 60:
        return "under a minute"
    m = int(round(s / 60))
    return f"about {m} min" if m < 60 else f"about {m // 60} h {m % 60:02d} min"


def _exts():
    import fits_core as core
    return {e.lower() for e in core.LIGHT_EXTS}


def safe_name(name) -> str:
    n = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', " ", str(name or "")).strip().rstrip(".")
    return n or "ASIAIR"


def is_ip(address) -> bool:
    return bool(re.fullmatch(r"\s*\d{1,3}(\.\d{1,3}){3}\s*", str(address or "")))


def unc(address) -> str:
    """'192.168.1.50' -> '\\\\192.168.1.50'; anything else (a share or folder path) unchanged."""
    a = str(address or "").strip()
    return "\\\\" + a if is_ip(a) else a


def parse_net_view(text) -> list:
    """Disk share names from Windows' 'net view \\\\host' output."""
    out, table = [], False
    for ln in str(text or "").splitlines():
        if ln.startswith("---"):
            table = True
            continue
        if not table or not ln.strip():
            continue
        parts = re.split(r"\s{2,}", ln.strip())
        if len(parts) >= 2 and parts[1].lower() in ("disk", "datenträger", "disque", "disco"):
            out.append(parts[0])
    return out


def shares(address) -> list:
    """The folders to look through for an ASIAIR: its image shares for an IP address, else the folder given."""
    a = str(address or "").strip()
    m = re.fullmatch(r"[\\/]{2}(\d{1,3}(?:\.\d{1,3}){3})[\\/]?", a)          # \\192.168.0.111 = the whole ASIAIR
    if m:
        a = m.group(1)
    if not a:
        return []
    if not is_ip(a):
        try:                                           # a folder: only if it's there (a typo is "can't reach")
            return [Path(a)] if Path(a).is_dir() else []
        except OSError:
            return []
    host = unc(a)
    names = []
    if sys.platform.startswith("win"):
        try:
            r = subprocess.run(["net", "view", host], capture_output=True, text=True, timeout=25,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            names = [n for n in parse_net_view(r.stdout) if "image" in n.lower()] or parse_net_view(r.stdout)
        except (OSError, subprocess.SubprocessError):
            names = []
    found = []
    for n in names or GUESS_SHARES:
        p = Path(host + "\\" + n)
        try:
            if p.is_dir():
                found.append(p)
        except OSError:
            pass
    return found


def scan(roots, now=None, stop=None, progress=None) -> list:
    """[(file, share root, relative path, size, mtime)] for the subs on the ASIAIR (FITS / XISF, not the .jpgs);
    a sub written in the last couple of minutes is left for next time. Each file's size and date come with the
    folder listing (one trip over the Wi-Fi per folder, not one more per sub - an ASIAIR holding months of subs
    took the best part of an hour to look through). progress(files looked at so far) every couple of seconds."""
    now = time.time() if now is None else now
    exts = _exts()
    out = []
    looked, said = 0, time.time()
    for root in roots:
        root = Path(root)
        stack = [root]
        while stack:
            if stop and stop():
                return out
            d = stack.pop()
            try:
                with os.scandir(d) as it:
                    ents = sorted(it, key=lambda e: e.name)
            except OSError:
                continue
            subdirs = []
            for e in ents:
                try:
                    if e.is_dir(follow_symlinks=False):
                        if not e.name.startswith((".", "$")):
                            subdirs.append(Path(e.path))
                        continue
                except OSError:
                    continue
                looked += 1
                if os.path.splitext(e.name)[1].lower() not in exts:
                    continue
                try:
                    st = e.stat()
                except OSError:
                    continue
                if now - st.st_mtime < SETTLE_S:
                    continue
                p = Path(e.path)
                out.append((p, root, p.relative_to(root).as_posix(), st.st_size, st.st_mtime))
            stack.extend(reversed(subdirs))
            if progress and time.time() - said >= SAY_EVERY_S:
                said = time.time()
                progress(looked)
    return out


def manifest_path(name) -> Path:
    import fits_core as core
    d = core.cache_dir() / "asiair"
    d.mkdir(parents=True, exist_ok=True)
    return d / (re.sub(r"[^A-Za-z0-9_.-]+", "_", safe_name(name)) + ".json")


def load_manifest(name) -> dict:
    try:
        return json.loads(manifest_path(name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_manifest(name, data):
    p = manifest_path(name)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    tmp.replace(p)


def _tail(p) -> str:
    r"""A sub's place on its ASIAIR, the same however the row points at it (the IP, \\IP\EMMC Images or
    \\IP\EMMC Images\Plan): 'EMMC Images/Plan/M31/x.fit'."""
    parts = Path(p).parts
    for i, part in enumerate(parts):
        if part.lower().rstrip("\\/").endswith("images"):
            return "/".join(x.strip("\\/") for x in parts[i:])
    t = str(p).replace("\\", "/").lstrip("/")
    return t.split("/", 1)[1] if re.match(r"^\d{1,3}(\.\d{1,3}){3}/", t) else t


def _mkey(root, rel, size, p=None) -> str:
    if p is not None:
        return f"{_tail(p)}|{size}"
    return f"{Path(root).name}/{rel}|{size}"


def _seen(seen, root, rel, size, p) -> bool:
    """Fetched before - under today's key, or the key of an earlier version (the row's last folder + path)."""
    return _mkey(root, rel, size, p) in seen or _mkey(root, rel, size) in seen


def _drop_previews(sub: Path) -> int:
    """The ASIAIR's .jpg previews of a sub just freed from it (same name, and its _thn thumbnail)."""
    n = 0
    for name in (sub.stem + ".jpg", sub.stem + ".jpeg", sub.stem + "_thn.jpg", sub.stem + "_thn.jpeg",
                 sub.stem.upper() + ".JPG"):
        q = sub.with_name(name)
        try:
            if q.is_file():
                q.unlink()
                n += 1
        except OSError:
            pass
    return n


def _wait_if_paused(pause, stop):
    """Pause button: hold here (between subs - never halfway through one) until resumed or stopped."""
    while pause and pause() and not (stop and stop()):
        time.sleep(0.4)


def _copy(src, dst, on_bytes=None, stop=None, stall_s=None):
    """Copy one sub a megabyte at a time, telling on_bytes(bytes so far) as it goes. If the ASIAIR sends nothing
    for stall_s seconds, give up on it (Stalled) instead of waiting - a copy from an ASIAIR whose Wi-Fi dropped
    could otherwise sit there for an hour or more, and the whole morning run with it."""
    import threading
    stall_s = STALL_S if stall_s is None else stall_s
    st = {"done": 0, "err": None, "fin": False, "t": time.time(), "quit": False}

    def run():
        try:
            with _open(src, "rb") as fi, open(dst, "wb") as fo:
                while not st["quit"]:
                    b = fi.read(CHUNK)
                    if not b or st["quit"]:          # given up on while it waited: write nothing more
                        break
                    fo.write(b)
                    st["done"] += len(b)
                    st["t"] = time.time()
            if not st["quit"]:
                try:
                    shutil.copystat(src, dst)
                except OSError:
                    try:
                        m = os.stat(src).st_mtime
                        os.utime(dst, (m, m))
                    except OSError:
                        pass
        except BaseException as e:  # noqa: BLE001 - handed to the caller below
            st["err"] = e
        finally:
            st["fin"] = True
        if st["quit"]:                               # left behind after a stall: tidy up the part-copy
            try:
                Path(dst).unlink()
            except OSError:
                pass
    th = threading.Thread(target=run, daemon=True, name="asiair-copy")
    th.start()
    while True:
        th.join(0.25)
        if st["fin"]:
            break
        if on_bytes:
            on_bytes(st["done"])
        if stop and stop():
            st["quit"] = True
            th.join(2)
            raise _Stopped()
        if time.time() - st["t"] > stall_s:
            st["quit"] = True                      # the reader may never come back; it is left behind
            raise Stalled(f"no data for {int(stall_s)} s")
    if st["err"] is not None:
        raise st["err"]
    if on_bytes:
        on_bytes(st["done"])


def fetch(unit: dict, incoming, progress=None, stop=None, free=False, now=None, roots=None, pause=None,
          deadline=None, stall_s=None) -> dict:
    """Copy one ASIAIR's new subs into incoming/<rig>/<share>/…  unit = {name, address, rig}.
    progress(text, i, n): text is '<name>: <what it is doing, in plain words>'.
    deadline (a time.time()): no new sub is started after it - the rest come over next time.
    Returns {name, address, copied, already, new, failed: [(file, why)], deleted, bytes, error, stalled, left,
    timeup}."""
    name = safe_name(unit.get("name") or unit.get("address"))
    rep = {"name": name, "address": str(unit.get("address") or ""), "copied": 0, "already": 0, "new": 0,
           "failed": [], "deleted": 0, "jpgs": 0, "bytes": 0, "error": "", "stalled": "", "left": 0,
           "timeup": False}

    def say(phrase, i=0, n=0):
        if progress:
            progress(f"{name}: {phrase}", i, n)
    if roots is None:
        say("connecting…")
        roots = shares(unit.get("address"))
    if not roots:
        rep["error"] = (f"can't reach {unit.get('address') or '(no address)'} - is the ASIAIR on and on the same "
                        "network? (Its IP is in the ASIAIR app's Wi-Fi settings.)")
        return rep
    dest_top = Path(incoming) / safe_name(unit.get("rig") or name)
    seen = load_manifest(name)
    say("looking through its folders for new subs…")
    subs = scan(roots, now=now, stop=stop,
                progress=lambda k: say(f"looking through its folders for new subs - {k:,} files so far"))
    todo = [s for s in subs if not _seen(seen, s[1], s[2], s[3], s[0])]
    rep["already"] = len(subs) - len(todo)
    rep["new"] = len(todo)
    if free:                                     # fetched (and checked) on an earlier run: free them now
        olds = [s for s in subs if _seen(seen, s[1], s[2], s[3], s[0])]
        for k, (p, root, rel, size, mt) in enumerate(olds):
            if stop and stop():
                break
            if k % 25 == 0:
                say(f"freeing space - deleting subs fetched before ({k:,} of {len(olds):,})")
            try:
                p.unlink()
                rep["deleted"] += 1
                rep["jpgs"] += _drop_previews(p)
            except OSError:
                pass
    total = sum(s[3] for s in todo)
    done_b = 0                                   # of the new subs, bytes copied or found already there
    sent_b, t0 = 0, None                         # bytes really sent over the Wi-Fi this run, for the speed
    last_say = [0.0]
    for i, (p, root, rel, size, mt) in enumerate(todo):
        _wait_if_paused(pause, stop)
        if stop and stop():
            break
        if deadline is not None and time.time() > deadline:
            rep["timeup"] = True
            rep["left"] = len(todo) - i
            break
        head = f"copying {i + 1} of {len(todo)}"

        def where(b, _head=head):
            got = sent_b + b
            t = f"{_head} - {size_text(total - done_b - b)} left"
            secs = time.time() - t0 if t0 else 0
            if secs > 5 and got > 0:
                speed = got / secs
                t += f", {time_text((total - done_b - b) / speed)} at {speed / 1e6:.1f} MB/s"
            elif secs > 20:
                t += " - nothing has come over yet; the ASIAIR is very slow to answer"
            return t

        def on_bytes(b, _i=i):
            if time.time() - last_say[0] < SAY_EVERY_S:
                return
            last_say[0] = time.time()
            say(where(b), _i, len(todo))
        last_say[0] = time.time()
        say(where(0), i, len(todo))
        dst = dest_top / _tail(p)
        tmp = dst.with_name(dst.name + ".fetching")
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            if not (dst.exists() and dst.stat().st_size == size):
                if t0 is None:
                    t0 = time.time()
                try:
                    _copy(p, tmp, on_bytes=on_bytes, stop=stop, stall_s=stall_s)
                finally:
                    sent_b += size
                if tmp.stat().st_size != size:
                    tmp.unlink()
                    raise OSError("the copy came out a different size")
                tmp.replace(dst)
                rep["copied"] += 1
                rep["bytes"] += size
            done_b += size
            seen[_mkey(root, rel, size, p)] = mt
            if free:
                try:
                    p.unlink()
                    rep["deleted"] += 1
                    rep["jpgs"] += _drop_previews(p)
                except OSError as e:
                    rep["failed"].append((rel, f"copied, but couldn't delete it from the ASIAIR: {e}"))
        except _Stopped:
            _quiet_unlink(tmp)
            break
        except Stalled:
            _quiet_unlink(tmp)
            rep["stalled"] = (f"it stopped answering partway through ({rep['copied']} of {len(todo)} new subs "
                              f"copied) - the rest come over next time")
            rep["left"] = len(todo) - i
            break
        except Exception as e:  # noqa: BLE001
            _quiet_unlink(tmp)
            rep["failed"].append((rel, str(e)))
        if i % 25 == 0:
            save_manifest(name, seen)
    save_manifest(name, seen)
    return rep


def _quiet_unlink(p):
    try:
        Path(p).unlink()
    except OSError:
        pass


def short_text(rep) -> str:
    """One ASIAIR's result in a few plain words, for the status line while the others are still going."""
    if rep.get("error"):
        return f"can't reach {rep.get('address') or 'it'}"
    n, c = rep.get("new", 0), rep.get("copied", 0)
    if rep.get("stalled"):
        t = f"stopped answering - {c} of {n} copied, the rest next time"
    elif rep.get("timeup"):
        t = f"{c} of {n} copied, the other {rep.get('left', 0)} next time (time was up)"
    elif c:
        t = f"done - {c} new sub{'s' if c != 1 else ''} ({size_text(rep.get('bytes', 0))})"
    elif n and rep.get("failed"):
        return f"couldn't copy its {n} new sub{'s' if n != 1 else ''} ({rep['failed'][0][1]})"
    elif n:
        t = "done - its new subs were already in the incoming folder"
    else:
        t = "nothing new"
    if rep.get("failed"):
        t += f", {len(rep['failed'])} couldn't be copied"
    return t


def fetch_all(units, incoming, progress=None, stop=None, free=False, together=True, pause=None,
              deadline=None, stall_s=None) -> list:
    """Every ASIAIR's new subs. together: all of them at the same time - each ASIAIR has its own Wi-Fi radio,
    and that radio (not the network) is what's slow, so four ASIAIRs come over in about the time of one.
    The progress line names every ASIAIR with what it is doing - looking, copying (how much is left and how long
    it should take), nothing new, can't reach - including the ones that have finished."""
    units = [u for u in units if u.get("address")]
    if not together or len(units) < 2:
        return [fetch(u, incoming, progress, stop, free, pause=pause, deadline=deadline, stall_s=stall_s)
                for u in units]
    import threading
    from concurrent.futures import ThreadPoolExecutor
    lock = threading.Lock()
    # rows of the same ASIAIR (same name - e.g. its Plan and Autorun folders as two rows) go one after the other:
    # they share its radio and its record of what's been fetched
    groups = {}
    for u in units:
        groups.setdefault(safe_name(u.get("name") or u.get("address")), []).append(u)
    state = {n: ("waiting…", 0, 0) for n in groups}
    finished = set()

    def show():
        if progress is None:
            return
        busy = len(groups) - len(finished)
        head = (f"Fetching from your {len(groups)} ASIAIRs ({busy} still going)" if busy else
                f"Fetched from your {len(groups)} ASIAIRs")
        progress(head + " - " + "; ".join(f"{k}: {t}" for k, (t, _a, _b) in state.items()),
                 sum(a for _t, a, _b in state.values()), sum(b for _t, _a, b in state.values()))

    def prog_for(name):
        def p(text, i, n):
            with lock:
                state[name] = (text.split(": ", 1)[1] if text.startswith(name + ": ") else text, i, n)
                show()
        return p

    def run_group(name, us):
        reps = [fetch(u, incoming, prog_for(name), stop, free, pause=pause, deadline=deadline, stall_s=stall_s)
                for u in us]
        with lock:
            ok = [r for r in reps if not r["error"]] or reps
            words = "; ".join(short_text(r) for r in ok)
            n = sum(r.get("new", 0) for r in reps)
            state[name] = (words, n, n)
            finished.add(name)
            show()
        return reps
    with lock:
        show()
    with ThreadPoolExecutor(max_workers=min(8, len(groups))) as pool:
        futs = [pool.submit(run_group, n, us) for n, us in groups.items()]
        return [r for f in futs for r in f.result()]


def describe(reps) -> str:
    out = []
    for r in reps:
        if r["error"]:
            out.append(f"{r['name']}: not fetched - {r['error']}")
            continue
        t = f"{r['name']}: {r['copied']} new sub(s) ({r['bytes'] / 1e9:.1f} GB)"
        if r["deleted"]:
            t += f", {r['deleted']} freed on the ASIAIR" + (f" (+{r['jpgs']} .jpg)" if r.get("jpgs") else "")
        if r["failed"]:
            t += f", {len(r['failed'])} failed ({r['failed'][0][1]})"
        if r.get("stalled"):
            t += f" - {r['stalled']}"
        elif r.get("timeup"):
            t += f" - time was up, the other {r.get('left', 0)} come over next time"
        out.append(t)
    return "\n".join(out) if out else "No ASIAIRs set up."


def check(units, stop=None) -> list:
    """Refresh: is each ASIAIR reachable right now, and how many of its subs aren't fetched yet? Reads only.
    [{name, reachable, subs, new}]"""
    out = []
    for u in units:
        if stop and stop():
            break
        name = u.get("name") or u.get("address") or ""
        rep = {"name": name, "reachable": False, "subs": 0, "new": 0}
        try:
            roots = shares(u.get("address") or "") if (u.get("address") or "").strip() else []
            if roots:
                subs = scan(roots, stop=stop)
                seen = load_manifest(safe_name(name))
                rep.update(reachable=True, subs=len(subs),
                           new=sum(1 for s in subs if not _seen(seen, s[1], s[2], s[3], s[0])))
        except OSError:
            pass
        out.append(rep)
    return out


def now_text(rep) -> str:
    """What the Right now column shows after Refresh."""
    if not rep.get("reachable"):
        return "can't reach it"
    n = rep.get("new", 0)
    return f"reachable - {n} new sub{'s' if n != 1 else ''} waiting" if n else "reachable - nothing new"


def load_units(settings) -> list:
    try:
        v = json.loads(settings.value("asiair/list", "[]") or "[]")
        return [u for u in v if isinstance(u, dict)]
    except (ValueError, TypeError):
        return []


def save_units(settings, units):
    settings.setValue("asiair/list", json.dumps(units))


def last_text(rep, stamp) -> str:
    """What the Last fetch column shows for one ASIAIR after a fetch round."""
    if rep.get("error"):
        return f"{stamp}: not reached"
    t = f"{stamp}: {rep.get('copied', 0)} new"
    if rep.get("stalled") or rep.get("timeup"):
        t += f", {rep.get('left', 0)} left for next time"
    return t


def record_last(settings, reps, stamp=None) -> list:
    """Every fetch - Fetch Now, the overnight rounds, before Organize Automatically - writes each ASIAIR's Last fetch
    (the overnight rounds once left the column at the last Fetch Now, days old). Returns the saved units."""
    import time as _t
    stamp = stamp or _t.strftime("%Y-%m-%d %H:%M")
    by = {r.get("name"): r for r in reps or []}
    units = load_units(settings)
    for u in units:
        r = by.get(safe_name(u.get("name") or u.get("address") or ""))
        if r is not None:
            u["last"] = last_text(r, stamp)
    save_units(settings, units)
    return units


JPG_EXTS = (".jpg", ".jpeg")


def jpgs(roots, stop=None) -> list:
    """The .jpg pictures on the ASIAIR (its previews, saved next to every sub) - [(path, size)]."""
    out = []
    for root in roots:
        for dp, dns, fns in os.walk(Path(root)):
            if stop and stop():
                return out
            dns[:] = [d for d in dns if not d.startswith((".", "$"))]
            for n in fns:
                if n.lower().endswith(JPG_EXTS):
                    p = Path(dp) / n
                    try:
                        out.append((p, p.stat().st_size))
                    except OSError:
                        pass
    return out


def jpg_summary(found, roots) -> str:
    """'812 .jpg (1.4 GB): Autorun 790, Live 22'."""
    import collections
    c = collections.Counter()
    for p, _s in found:
        top = ""
        for r in roots:
            try:
                top = p.relative_to(r).parts[0] if len(p.relative_to(r).parts) > 1 else "(top folder)"
                break
            except ValueError:
                continue
        c[top] += 1
    size = sum(s for _p, s in found)
    return f"{len(found)} .jpg ({size / 1e9:.2f} GB)" + (": " + ", ".join(f"{k} {n}" for k, n in c.most_common())
                                                        if c else "")


def delete_jpgs(found, progress=None, stop=None) -> tuple:
    """Delete these .jpgs from the ASIAIR -> (deleted, failed)."""
    done = failed = 0
    for i, (p, _s) in enumerate(found):
        if stop and stop():
            break
        if progress and i % 25 == 0:
            progress(f"Deleting .jpgs on the ASIAIR… {i} of {len(found)}", i, len(found))
        try:
            p.unlink()
            done += 1
        except OSError:
            failed += 1
    return done, failed

