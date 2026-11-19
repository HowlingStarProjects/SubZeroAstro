"""
squeezer.py - the Fitz'r Squeezer: shrink raw subs on disk without losing a single bit, and put them back.

Squeezing rewrites a FITS file with FITS's own lossless compression (Rice tile compression - the same format
fpack, N.I.N.A. and Siril write). The file keeps its name, so PixInsight, Siril, N.I.N.A., ASTAP and
SubZeroAstro still open it like any other FITS file; it just takes about half the room or less.

Every file is checked before the original goes: the squeezed copy is unpacked in memory and must give back the
original file byte for byte, it is written next to the original, read back from the disk and compared, and only
then does it take the original's place (one atomic rename - a power cut leaves either the old file or the new
one, never half of one). The file's date stays as it was.

Unsqueezing does the same the other way. A squeezed file remembers its original size and fingerprint
(SZAOSIZE / SZAQHASH in its header), so Organize and Clean Up My N.I.N.A. Computers still recognise it as the
same frame as an unsqueezed copy elsewhere.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import time
from pathlib import Path

import fits_tiles as ft

NAME = "Fitz'r Squeezer"
SQUEEZE_EXTS = (".fits", ".fit", ".fts")
UNSQUEEZE_EXTS = SQUEEZE_EXTS + (".fz",)
TMP_SUFFIX = ".squeezing"
MIN_GAIN = 0.05            # a file that would shrink by less than this is left alone
ORIG_SIZE, ORIG_HASH = "SZAOSIZE", "SZAQHASH"
HASH_HEAD, HASH_TAIL = 128 << 10, 32 << 10          # the same fingerprint as Organize's (organizer._quick_hash)

_SKIP_DIRS = {"_subzeroastro_organize"}


# --------------------------------------------------------------------------
# What a file is
# --------------------------------------------------------------------------

def fingerprint(size: int, head: bytes, tail: bytes) -> str:
    """Organize's quick fingerprint of a file from its size, first 128 KB and last 32 KB."""
    h = hashlib.sha1()
    h.update(str(size).encode())
    h.update(head[:HASH_HEAD])
    if size > HASH_HEAD + HASH_TAIL:
        h.update(tail[-HASH_TAIL:])
    return h.hexdigest()


def fingerprint_bytes(buf: bytes) -> str:
    return fingerprint(len(buf), buf[:HASH_HEAD], buf[-HASH_TAIL:])


def _header_values(cards) -> dict:
    import fits_core as core
    return core.cards_to_dict([core.parse_card(c) for c in cards])


def info(path) -> dict | None:
    """What the header says, from a small read: {"squeezed": bool, "bitpix", "shape", "osize", "qhash"} - or None
    when it isn't a FITS file. osize / qhash: a file squeezed here remembers the original's size and fingerprint."""
    try:
        with open(path, "rb") as f:
            head = f.read(2880 * 12)
    except OSError:
        return None
    if not head.startswith(b"SIMPLE  ="):
        return None
    try:
        cards, at, _r = ft.raw_cards(head)
    except ValueError:
        return None
    look = _header_values(cards)
    out = {"squeezed": False, "bitpix": look.get("BITPIX"), "osize": None, "qhash": None,
           "shape": tuple(look.get(f"NAXIS{i}") for i in range(1, int(look.get("NAXIS", 0) or 0) + 1))}
    if int(look.get("NAXIS", 0) or 0) == 0 and len(head) > at:
        try:
            ext, _q, _r = ft.raw_cards(head, at)
        except ValueError:
            return out
        el = _header_values(ext)
        if el.get("ZIMAGE") and str(el.get("XTENSION", "")).strip().upper() == "BINTABLE":
            nax = int(el.get("ZNAXIS", 0) or 0)
            out.update(squeezed=True, bitpix=el.get("ZBITPIX"), osize=el.get(ORIG_SIZE), qhash=el.get(ORIG_HASH),
                       shape=tuple(el.get(f"ZNAXIS{i}") for i in range(1, nax + 1)), cmp=el.get("ZCMPTYPE"))
    return out


def is_squeezed(path) -> bool:
    i = info(path)
    return bool(i and i["squeezed"])


def original_id(path, size=None):
    """(size, fingerprint) of the file as it was captured: a file squeezed here gives its original's; any other
    file its own (the fingerprint read only when asked for)."""
    p = Path(path)
    size = p.stat().st_size if size is None else size
    i = info(p) if p.suffix.lower() in UNSQUEEZE_EXTS else None
    if i and i["squeezed"] and i.get("osize") and i.get("qhash"):
        return int(i["osize"]), str(i["qhash"])
    return size, None


# --------------------------------------------------------------------------
# One file
# --------------------------------------------------------------------------

class Skip(Exception):
    """The file is left as it is (the message says why, in plain words)."""


def squeeze_bytes(buf: bytes) -> bytes:
    """A FITS file's bytes -> its squeezed bytes, checked to unpack to exactly the same bytes."""
    extra = [ft.make_card(ORIG_SIZE, len(buf), "SubZeroAstro: size before squeezing"),
             ft.make_card(ORIG_HASH, fingerprint_bytes(buf), "SubZeroAstro: fingerprint before squeezing")]
    try:
        out = ft.compress_file(buf, extra)
    except ft.NotSqueezable as e:
        raise Skip(str(e))
    if ft.expand_file(out) != buf:
        raise Skip("the squeezed copy didn't unpack to exactly the original (left as it was)")
    return out


def _write_checked(path: Path, data: bytes, st) -> None:
    """Write `data` next to `path`, read it back, then let it take path's place - only if path is unchanged since
    `st` (another job may have moved or rewritten it meanwhile). Keeps the file's dates."""
    tmp = path.with_name(f"{path.name}.{os.getpid()}{TMP_SUFFIX}")
    try:
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        with open(tmp, "rb") as f:
            if f.read() != data:
                raise Skip("the copy written to the disk didn't read back the same (disk or network trouble) - "
                           "the original is untouched")
        try:
            now = path.stat()
        except OSError:
            raise Skip("it was moved or deleted while it was being squeezed (another job?) - nothing changed")
        if now.st_size != st.st_size or now.st_mtime_ns != st.st_mtime_ns:
            raise Skip("it changed while it was being squeezed (another job?) - nothing changed")
        os.replace(tmp, path)
        try:
            os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns))
        except OSError:
            pass
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass


def squeeze_file(path) -> tuple[int, int]:
    """Squeeze one file in place. Returns (bytes before, bytes after). Raises Skip when it's left alone."""
    path = Path(path)
    st = path.stat()
    i = info(path)
    if i is None:
        raise Skip("not a FITS file")
    if i["squeezed"]:
        raise Skip("already squeezed")
    with open(path, "rb") as f:
        buf = f.read()
    if len(buf) != st.st_size:
        raise Skip("it changed while it was being read - nothing changed")
    out = squeeze_bytes(buf)
    if len(out) > len(buf) * (1 - MIN_GAIN):
        raise Skip("it would hardly get smaller (very noisy or already small)")
    _write_checked(path, out, st)
    return len(buf), len(out)


def unsqueeze_target(path: Path) -> Path:
    """Where an unsqueezed file goes: the same name - 'x.fits.fz' becomes 'x.fits'."""
    if path.suffix.lower() == ".fz":
        stem = path.name[:-3]
        if Path(stem).suffix.lower() not in SQUEEZE_EXTS:
            stem += ".fits"
        return path.with_name(stem)
    return path


def unsqueeze_file(path) -> tuple[int, int]:
    """Put one squeezed file back as a plain FITS file. Returns (bytes before, bytes after)."""
    path = Path(path)
    st = path.stat()
    i = info(path)
    if i is None:
        raise Skip("not a FITS file")
    if not i["squeezed"]:
        raise Skip("not squeezed")
    with open(path, "rb") as f:
        buf = f.read()
    if len(buf) != st.st_size:
        raise Skip("it changed while it was being read - nothing changed")
    try:
        out = ft.expand_file(buf)
    except ValueError as e:
        raise Skip(str(e))
    if i.get("osize") and i.get("qhash"):          # squeezed here: it must come back exactly as it was
        if len(out) != int(i["osize"]) or fingerprint_bytes(out) != str(i["qhash"]):
            raise Skip("it didn't unpack to exactly the original - left squeezed (it still opens fine)")
    free = _free(path.parent)
    if free is not None and free < len(out) - len(buf) + (64 << 20):
        raise Skip("the drive is too full to unsqueeze it")
    dst = unsqueeze_target(path)
    if dst != path:
        if dst.exists():
            raise Skip(f"{dst.name} is already there")
        tmp = dst.with_name(f"{dst.name}.{os.getpid()}{TMP_SUFFIX}")
        try:
            with open(tmp, "wb") as f:
                f.write(out)
                f.flush()
                os.fsync(f.fileno())
            with open(tmp, "rb") as f:
                if f.read() != out:
                    raise Skip("the copy written to the disk didn't read back the same - the original is untouched")
            os.replace(tmp, dst)
            os.utime(dst, ns=(st.st_atime_ns, st.st_mtime_ns))
        finally:
            if tmp.exists():
                tmp.unlink()
        path.unlink()
    else:
        _write_checked(path, out, st)
    return len(buf), len(out)


def _free(folder):
    try:
        return shutil.disk_usage(str(folder)).free
    except OSError:
        return None


# --------------------------------------------------------------------------
# Folders
# --------------------------------------------------------------------------

def _skip_dir(name: str) -> bool:
    import organizer as O
    n = name.lower()
    return name.startswith(".") or n in _SKIP_DIRS or O._nas_dir(name) or n.endswith((".szproj", ".saving"))


def files_in(folders, exts, stop=None) -> list:
    """Every file with one of `exts` in the folders (and the folders inside them), each once."""
    seen, out = set(), []
    for top in folders:
        top = Path(top)
        if top.is_file():
            walk = [(str(top.parent), [], [top.name])]
        else:
            walk = os.walk(top)
        for dp, dns, fns in walk:
            if stop and stop():
                return out
            dns[:] = sorted(d for d in dns if not _skip_dir(d))
            for fn in sorted(fns):
                if fn.startswith(".") or fn.endswith(TMP_SUFFIX):
                    continue
                if os.path.splitext(fn)[1].lower() in exts:
                    p = Path(dp) / fn
                    k = os.path.normcase(str(p))
                    if k not in seen:
                        seen.add(k)
                        out.append(p)
    return out


def survey(folders, mode="squeeze", progress=None, stop=None, samples=3) -> dict:
    """Look through the folders (headers only) and work out what squeezing (or unsqueezing) would do.
    {"todo": [paths], "todo_bytes", "after_bytes" (estimate), "done": n already in the wanted state, "done_bytes",
     "skipped": {why: n}, "ratio": estimated squeezed size / original, "sampled": n}"""
    exts = SQUEEZE_EXTS if mode == "squeeze" else UNSQUEEZE_EXTS
    files = files_in(folders, exts, stop)
    rep = {"mode": mode, "todo": [], "todo_bytes": 0, "after_bytes": 0, "done": 0, "done_bytes": 0, "skipped": {},
           "ratio": None, "sampled": 0, "files": len(files)}
    expand_bytes = 0
    for k, p in enumerate(files):
        if stop and stop():
            break
        if progress and k % 20 == 0:
            progress(f"Reading headers: {k + 1:,} of {len(files):,}", k, len(files))
        try:
            size = p.stat().st_size
        except OSError:
            continue
        i = info(p)
        if i is None:
            rep["skipped"]["not a FITS file"] = rep["skipped"].get("not a FITS file", 0) + 1
            continue
        if mode == "squeeze":
            if i["squeezed"]:
                rep["done"] += 1
                rep["done_bytes"] += size
                continue
            bp = i.get("bitpix")
            if isinstance(bp, int) and bp < 0:
                why = "stacks and processed pictures (decimal numbers - they hardly shrink)"
                rep["skipped"][why] = rep["skipped"].get(why, 0) + 1
                continue
            if not i["shape"] or len(i["shape"]) not in (2, 3):
                why = "not a single picture"
                rep["skipped"][why] = rep["skipped"].get(why, 0) + 1
                continue
            rep["todo"].append(p)
            rep["todo_bytes"] += size
        else:
            if not i["squeezed"]:
                rep["done"] += 1
                rep["done_bytes"] += size
                continue
            rep["todo"].append(p)
            rep["todo_bytes"] += size
            if i.get("osize"):
                expand_bytes += int(i["osize"])
            else:
                shp = [int(x or 0) for x in i["shape"]]
                n = 1
                for x in shp:
                    n *= x
                expand_bytes += (abs(int(i.get("bitpix") or 16)) // 8) * n + 2880 * 4
    if mode == "squeeze":
        rep["ratio"], rep["sampled"] = _sample_ratio(rep["todo"], samples, progress, stop)
        rep["after_bytes"] = int(rep["todo_bytes"] * rep["ratio"]) if rep["ratio"] else rep["todo_bytes"]
    else:
        rep["after_bytes"] = expand_bytes
    return rep


def _sample_ratio(todo, samples, progress=None, stop=None):
    """Squeeze a few files in memory (nothing is written) to see how much this kind of data shrinks."""
    if not todo:
        return None, 0
    picks = [todo[int(i * (len(todo) - 1) / max(samples - 1, 1))] for i in range(min(samples, len(todo)))]
    picks = list(dict.fromkeys(picks))
    before = after = 0
    n = 0
    for k, p in enumerate(picks):
        if stop and stop():
            break
        if progress:
            progress(f"Trying a sample ({k + 1} of {len(picks)}): {p.name}", k, len(picks))
        try:
            buf = p.read_bytes()
            out = ft.compress_file(buf)
        except (OSError, ValueError):
            continue
        before += len(buf)
        after += min(len(out), len(buf))
        n += 1
    return (after / before if before else None), n


WORKERS = 2          # two files at a time: the number crunching of one overlaps the reading and writing of the other


def run(paths, mode="squeeze", progress=None, stop=None, wait=None, log=None, workers=WORKERS) -> dict:
    """Squeeze (or unsqueeze) the files. wait(): called before each file - returns a reason to hold back (another
    job is busy with the library) or None. Returns {"done", "before", "after", "skipped": [(path, why)],
    "failed": [(path, why)], "stopped"}."""
    from concurrent.futures import ThreadPoolExecutor
    job = squeeze_file if mode == "squeeze" else unsqueeze_file
    rep = {"mode": mode, "done": 0, "before": 0, "after": 0, "skipped": [], "failed": [], "stopped": False,
           "started": time.strftime("%Y-%m-%d %H:%M")}
    t0 = time.time()
    paths = list(paths)
    verb = "Squeezing" if mode == "squeeze" else "Unsqueezing"

    def may_start(k):
        while wait:
            why = wait()
            if not why or (stop and stop()):
                break
            if progress:
                progress(f"Waiting: {why}", k, len(paths))
            time.sleep(5)
        return not (stop and stop())

    def finish(p, fut):
        try:
            b, a = fut.result()
            rep["done"] += 1
            rep["before"] += b
            rep["after"] += a
            if log:
                log(f"{'squeezed' if mode == 'squeeze' else 'unsqueezed'} {p} ({b:,} -> {a:,} bytes)")
        except Skip as e:
            rep["skipped"].append((p, str(e)))
            if log:
                log(f"left {p}: {e}")
        except (OSError, MemoryError, ValueError) as e:
            rep["failed"].append((p, str(e)))
            if log:
                log(f"couldn't do {p}: {e}")

    pending = []
    with ThreadPoolExecutor(max(1, workers)) as ex:
        for k, p in enumerate(paths):
            if not may_start(k):
                rep["stopped"] = True
                break
            if progress:
                left = ""
                if k >= 4:
                    mins = (time.time() - t0) / k * (len(paths) - k) / 60
                    left = f" - about {mins:.0f} min left" if mins >= 1.5 else " - nearly done"
                progress(f"{verb} {k + 1:,} of {len(paths):,}: {Path(p).name}{left}", k, len(paths))
            pending.append((p, ex.submit(job, p)))
            while len(pending) >= max(1, workers):
                finish(*pending.pop(0))
        for item in pending:
            finish(*item)
    rep["seconds"] = time.time() - t0
    return rep


def gb(n) -> str:
    """Bytes in plain words: '1.4 GB', '820 MB'."""
    n = float(n or 0)
    if n >= 1e12:
        return f"{n / 1e12:.2f} TB"
    if n >= 1e9:
        return f"{n / 1e9:.1f} GB"
    return f"{n / 1e6:.0f} MB"


def leftovers(folders) -> list:
    """Half-written copies (a crash or a power cut mid-file) - safe to delete: the original was never touched."""
    out = []
    for top in folders:
        for dp, dns, fns in os.walk(top):
            dns[:] = [d for d in dns if not _skip_dir(d)]
            out += [Path(dp) / f for f in fns if f.endswith(TMP_SUFFIX)]
    return out
