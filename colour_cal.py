"""
colour_cal.py - star colours from a star catalogue (what PixInsight's PCC / SPCC do).

The picture must be plate solved (Prepare's "Plate solve" step writes the solution into the masters). From the
solution we know where each catalogue star falls in the picture. The Gaia star catalogue gives each star's true
colour (BP-RP: blue light against red light). We measure every matched star's red, green and blue light, fit how
the picture's colour follows the true colour, and pick the colour balance that makes a sun-like star (BP-RP 0.82)
pure white - the usual white reference of astrophotography.

The catalogue comes from VizieR (CDS, Strasbourg) the first time a field is calibrated and is kept on this computer,
so the same field works offline afterwards.
"""
from __future__ import annotations

import json
import math
import urllib.parse
import urllib.request
from pathlib import Path

import numpy as np

SUN_BP_RP = 0.82                    # Gaia BP-RP of a G2V star like the Sun
VIZIER = ("https://vizier.cds.unistra.fr/viz-bin/asu-tsv", "https://vizier.cfa.harvard.edu/viz-bin/asu-tsv")
CREDIT = ("This work has made use of data from the European Space Agency (ESA) mission Gaia, processed by the Gaia "
          "Data Processing and Analysis Consortium (DPAC), via the VizieR catalogue access tool, CDS, Strasbourg.")
MIN_STARS = 12

# tests replace this: callable(url) -> text. None = the internet.
fetch_hook = None


class CalError(Exception):
    """Why the catalogue colour balance can't be made, in plain words."""


# --------------------------------------------------------------------------
# Where the picture points
# --------------------------------------------------------------------------

def wcs_for(sources) -> "tuple | None":
    """(wcs, width, height) from the first plate-solved master, or None."""
    import framing
    for p in sources or ():
        try:
            h, w_, h_ = framing.read(p)
        except Exception:  # noqa: BLE001 - a missing or unreadable master just isn't used
            continue
        w = framing.wcs_of(h)
        if w is not None:
            return w, w_, h_
    return None


def sky_to_pixel(w: dict, ra, dec):
    """(RA, Dec) degrees -> FITS pixel (1-based x, y), gnomonic (TAN) projection; arrays in, arrays out."""
    ra = np.radians(np.asarray(ra, np.float64))
    dec = np.radians(np.asarray(dec, np.float64))
    ra0, dec0 = math.radians(w["crval1"]), math.radians(w["crval2"])
    dra = ra - ra0
    cosc = math.sin(dec0) * np.sin(dec) + math.cos(dec0) * np.cos(dec) * np.cos(dra)
    xi = np.degrees(np.cos(dec) * np.sin(dra) / cosc)
    eta = np.degrees((math.cos(dec0) * np.sin(dec) - math.sin(dec0) * np.cos(dec) * np.cos(dra)) / cosc)
    a, b, c, d = w["cd"]
    det = a * d - b * c
    dx = (d * xi - b * eta) / det
    dy = (-c * xi + a * eta) / det
    return dx + w["crpix1"], dy + w["crpix2"]


def field_of(w: dict, width: int, height: int) -> tuple:
    """(RA°, Dec°, radius°) of a circle round the whole picture."""
    import framing
    ra, dec = framing.pixel_to_sky(w, (width + 1) / 2.0, (height + 1) / 2.0)
    scale = framing.pixel_scale(w) / 3600.0
    return ra, dec, 0.5 * math.hypot(width, height) * scale * 1.03


# --------------------------------------------------------------------------
# The catalogue
# --------------------------------------------------------------------------

def cache_dir() -> Path:
    import checkpoints as cp
    d = cp.default_root().parent / "star_catalogue"
    d.mkdir(parents=True, exist_ok=True)
    return d


def parse_tsv(text: str) -> np.ndarray:
    """VizieR tab-separated reply -> (n, 4) array: RA°, Dec°, G magnitude, BP-RP (stars without BP-RP left out)."""
    rows, names, data = [], None, False
    for line in text.splitlines():
        if not line.strip() or line.startswith("#"):
            if data and not line.strip():
                break
            continue
        if names is None:
            names = [x.strip() for x in line.split("\t")]
            continue
        if not data:
            if line.lstrip().startswith("-"):
                data = True
            continue
        parts = line.split("\t")
        try:
            vals = [float(parts[names.index(k)]) for k in ("RA_ICRS", "DE_ICRS", "Gmag", "BP-RP")]
        except (ValueError, IndexError):
            continue
        rows.append(vals)
    if names is None:
        raise CalError("the star catalogue's answer could not be read")
    return np.array(rows, np.float64).reshape(-1, 4)


def _http_get(url: str, timeout=15) -> str:
    import target_info
    req = urllib.request.Request(url, headers={"User-Agent": target_info.UA})
    with urllib.request.urlopen(req, timeout=timeout, context=target_info.ssl_context()) as r:
        return r.read().decode("utf-8", "replace")


def catalogue_stars(ra: float, dec: float, radius: float, gmax: float = 15.5, limit: int = 4000) -> np.ndarray:
    """Gaia DR3 stars in a circle (RA°, Dec°, G, BP-RP), from this computer's copy or else from VizieR."""
    key = f"gaia_{ra:.3f}_{dec:+.3f}_{radius:.3f}_{gmax:.1f}_{limit}.json"
    path = None
    try:
        if fetch_hook is not None:
            raise OSError("a test's catalogue is never kept")
        path = cache_dir() / key
        if path.exists():
            return np.array(json.loads(path.read_text(encoding="utf-8")), np.float64).reshape(-1, 4)
    except (OSError, ValueError):
        path = None
    q = urllib.parse.urlencode({"-source": "I/355/gaiadr3", "-c": f"{ra:.5f} {dec:+.5f}", "-c.rd": f"{radius:.4f}",
                                "-out": "RA_ICRS,DE_ICRS,Gmag,BP-RP", "Gmag": f"<{gmax:g}",
                                "-out.max": str(limit), "-sort": "Gmag"})
    get = fetch_hook or _http_get
    problems = []
    for base in VIZIER:
        try:
            cat = parse_tsv(get(base + "?" + q))
        except CalError as e:
            problems.append(str(e))
            continue
        except Exception as e:  # noqa: BLE001 - try the other mirror, then explain
            import target_info
            problems.append(target_info.explain(e))
            continue
        if path is not None and len(cat):
            try:
                path.write_text(json.dumps(np.round(cat, 7).tolist()), encoding="utf-8")
            except OSError:
                pass
        return cat
    raise CalError("the star catalogue could not be reached (" + (problems[-1] if problems else "no answer") +
                   ") - it needs an internet connection the first time for each target")


# --------------------------------------------------------------------------
# Matching and measuring
# --------------------------------------------------------------------------

def _offset(px, py, dx_, dy_, search: float):
    """The shift (dx, dy) that lines the predicted catalogue positions up with the stars found, by vote, and how many
    agree. Small shifts happen when edges were trimmed after the picture was solved."""
    best = (0, 0.0, 0.0)
    if not len(px) or not len(dx_):
        return best
    ddx = dx_[None, :] - px[:, None]
    ddy = dy_[None, :] - py[:, None]
    ok = (np.abs(ddx) < search) & (np.abs(ddy) < search)
    if not ok.any():
        return best
    bx, by = ddx[ok], ddy[ok]
    nb = int(2 * search // 2) + 1
    hist, ex, ey = np.histogram2d(bx, by, bins=nb, range=[[-search, search], [-search, search]])
    # sum each 2x2 block so a shift on a bin edge still wins
    h2 = hist[:-1, :-1] + hist[1:, :-1] + hist[:-1, 1:] + hist[1:, 1:] if nb > 1 else hist
    i, j = np.unravel_index(int(np.argmax(h2)), h2.shape)
    cx, cy = ex[i + 1], ey[j + 1]
    near = (np.abs(bx - cx) < 3) & (np.abs(by - cy) < 3)
    if not near.any():
        return best
    mx, my = float(np.median(bx[near])), float(np.median(by[near]))
    votes = int(((np.abs(bx - mx) < 2) & (np.abs(by - my) < 2)).sum())
    return votes, mx, my


def _photometry(img: np.ndarray, x: float, y: float, r_in: float, r1: float, r2: float, sat: float):
    """Background-subtracted light of one star in each colour, or None (edge, saturated, too faint)."""
    h, w = img.shape[1:]
    R = int(math.ceil(r2)) + 1
    xi, yi = int(round(x)), int(round(y))
    if not (R <= xi < w - R and R <= yi < h - R):
        return None
    box = img[:, yi - R:yi + R + 1, xi - R:xi + R + 1]
    yy, xx = np.mgrid[-R:R + 1, -R:R + 1]
    rr = np.hypot(yy - (y - yi), xx - (x - xi))
    inner, ring = rr <= r_in, (rr >= r1) & (rr <= r2)
    if box[:, inner].max() > sat:
        return None
    f = []
    for c in box:
        bg = np.median(c[ring])
        noise = 1.4826 * np.median(np.abs(c[ring] - bg)) + 1e-9
        v = float((c[inner] - bg).sum())
        if v <= 5 * noise * math.sqrt(inner.sum()):
            return None
        f.append(v)
    return f


def _fit(c, v):
    """Robust straight line v = a + b c: (a, b, kept mask)."""
    keep = np.ones(len(c), bool)
    a = b = 0.0
    for _ in range(4):
        if keep.sum() < 4:
            break
        A = np.stack([np.ones(keep.sum()), c[keep]], 1)
        (a, b), *_ = np.linalg.lstsq(A, v[keep], rcond=None)
        res = v - (a + b * c)
        s = 1.4826 * np.median(np.abs(res[keep])) + 1e-6
        keep = np.abs(res) < 3 * s
    return float(a), float(b), keep


def calibrate(rgb: np.ndarray, sources, stars: np.ndarray | None = None, log=None) -> dict:
    """{"white": [r, 1, b], "n": stars used, "note": plain words} - the colour balance that makes sun-like stars
    white, from the Gaia catalogue. Raises CalError with the reason in plain words."""
    if rgb is None or rgb.ndim != 3 or rgb.shape[0] != 3:
        raise CalError("it needs a colour picture")
    img = rgb if stars is None or stars.shape != rgb.shape else rgb + stars
    sol = wcs_for(sources)
    if sol is None:
        raise CalError("your masters are not plate solved - tick Plate solve in Prepare (page 3) first")
    w, sw, sh = sol
    h, wd = img.shape[1:]
    if abs(sw / max(wd, 1) - 1) > 0.15 or abs(sh / max(h, 1) - 1) > 0.15:
        raise CalError("the picture's size changed since it was plate solved")
    ra, dec, rad = field_of(w, sw, sh)
    cat = catalogue_stars(ra, dec, rad)
    if len(cat) < MIN_STARS:
        raise CalError(f"the star catalogue has only {len(cat)} stars with colours here")
    px, py = sky_to_pixel(w, cat[:, 0], cat[:, 1])
    px, py = px - 1.0, py - 1.0                               # 0-based, as the picture's rows and columns

    import stacking as st
    lum = img.mean(axis=0)
    try:
        det, _pk = st.find_stars((lum / max(float(np.percentile(lum, 99.99)), 1e-9) * 60000.0).astype(np.float32),
                                 1, max_stars=800)
    except Exception:  # noqa: BLE001
        det = np.empty((0, 2))
    if len(det) < MIN_STARS:
        raise CalError("too few stars were found in the picture")
    search = max(30.0, 0.03 * max(h, wd))
    best = None
    for flip in (False, True):                                 # FITS rows may be stored bottom-up
        qy = (sh - 1 - py) if flip else py
        inside = (px > -search) & (px < wd + search) & (qy > -search) & (qy < h + search)
        idx = np.nonzero(inside)[0][:500]
        votes, mx, my = _offset(px[idx], qy[idx], det[:, 0], det[:, 1], search)
        if best is None or votes > best[0]:
            best = (votes, mx, my, flip)
    votes, mx, my, flip = best
    if votes < max(8, 0.08 * min(len(det), 500)):
        raise CalError("the catalogue stars could not be lined up with the stars in the picture")
    qx = px + mx
    qy = ((sh - 1 - py) if flip else py) + my

    # each found star -> the catalogue star at its place (and no other catalogue star close by: a blend)
    order = np.argsort(cat[:, 2])
    qx, qy, cat = qx[order], qy[order], cat[order]
    ins = (qx > 0) & (qx < wd) & (qy > 0) & (qy < h)
    qx, qy, cat = qx[ins], qy[ins], cat[ins]
    pairs = []
    for x, y in det:
        d2 = (qx - x) ** 2 + (qy - y) ** 2
        j = int(np.argmin(d2))
        if d2[j] > 2.5 ** 2:
            continue
        others = d2 < 8.0 ** 2
        others[j] = False
        if others.any() and float(cat[others, 2].min()) < cat[j, 2] + 2.5:
            continue                                    # a blend: another star almost as bright right next to it
        pairs.append((x, y, cat[j, 3]))
    sat = 0.9 * max(1.0, float(np.max(img)))
    fwhm = 3.0
    try:
        import metrics
        r_ = metrics.measure(np.ascontiguousarray(lum[:min(h, 1536), :min(wd, 1536)] * 60000.0, np.float32))
        fwhm = float(r_.get("fwhm") or 3.0) if isinstance(r_, dict) else 3.0
    except Exception:  # noqa: BLE001 - only sets the aperture
        pass
    r_in = min(12.0, max(3.5, 1.5 * fwhm))
    cs, lr, lb = [], [], []
    for x, y, c in pairs:
        if not 0.0 < c < 2.5:
            continue
        f = _photometry(img, x, y, r_in, r_in + 3, r_in + 7, sat)
        if f is None:
            continue
        cs.append(c)
        lr.append(math.log(f[0] / f[1]))
        lb.append(math.log(f[2] / f[1]))
    if len(cs) < MIN_STARS:
        raise CalError(f"only {len(cs)} catalogue stars could be measured cleanly (at least {MIN_STARS} are needed)")
    cs, lr, lb = np.array(cs), np.array(lr), np.array(lb)
    ar, br, kr = _fit(cs, lr)
    ab, bb, kb = _fit(cs, lb)
    n = int((kr & kb).sum())
    white = [math.exp(-(ar + br * SUN_BP_RP)), 1.0, math.exp(-(ab + bb * SUN_BP_RP))]
    if not all(0.2 < x < 5.0 for x in white):
        raise CalError("the measured star colours do not follow the catalogue (the result would be far off)")
    if log:
        log(f"catalogue colour: {len(pairs)} stars matched (shift {mx:+.1f}, {my:+.1f} px), {n} used; "
            f"red/green slope {br:+.2f}, blue/green slope {bb:+.2f}; white R {white[0]:.3f} B {white[2]:.3f}")
    return {"white": white, "n": n, "slopes": (br, bb),
            "note": f"{n} stars matched to the Gaia star catalogue; sun-like stars made white"}
