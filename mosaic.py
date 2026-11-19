"""
mosaic.py - join the panels of a mosaic into one picture (Stack › Stitch a Mosaic…).

A mosaic is a target too big for one frame, photographed in overlapping pieces (panels) - ASIAIR's Mosaic plan and
N.I.N.A.'s Framing Assistant shoot each panel as its own target. Every panel is stacked as usual; this joins the
panel masters:

1. where each panel sits on the sky: its plate solution (from the header, or ASTAP solves it now); without one, the
   stars it shares with a panel already placed;
2. a fine-tune from the stars in every overlap, so the joins don't show doubled stars, and masters of the same
   panel taken through different filters land exactly on top of each other;
3. one big canvas (a flat - gnomonic - projection of the sky around the mosaic's centre, at the panels' pixel size);
4. brightness evened out: each panel gets a scale and a gentle tilted-plane offset so the sky and the stars agree
   with its neighbours in the overlaps (the sky glow differs from panel to panel, and from night to night);
5. the panels blended with soft edges (each fades out over its last few percent), one master per filter, all on
   the same canvas - so Astro Image Processing and Finishing Touches treat them like masters of any target.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

import fits_core as core
import prepare as P
import stacking as st

POLY_ORDER = 3               # canvas <-> panel mapping (takes in the sky's curvature across a wide mosaic)
LOW_SIDE = 1400              # the brightness matching works on a small copy of the canvas, this many pixels across
FEATHER = 0.07               # a panel fades out over this fraction of its shorter side


class Cancelled(Exception):
    pass


class MosaicError(Exception):
    pass


# ==========================================================================
# Panels
# ==========================================================================

@dataclass
class Panel:
    master: P.Master
    label: str = ""
    wcs: dict | None = None                  # stacking.wcs_of form, or None
    how: str = ""                            # how it was placed: plate solution / stars
    fwd: dict | None = None                  # poly panel px -> canvas px
    bbox: tuple | None = None                # x0, y0, x1, y1 on the canvas (exclusive end)
    inv: dict | None = None                  # poly canvas px (relative to bbox) -> panel px
    stars: np.ndarray | None = None          # panel px
    scale: list = field(default_factory=list)       # per channel
    plane: list = field(default_factory=list)       # per channel (o, gx, gy) in normalised canvas coordinates

    @property
    def shape(self):
        return self.master.shape

    @property
    def filter(self):
        return self.master.filter


_PANEL_WORD = re.compile(r"[\s_\-]*(?:panel|pane|tile|part|p)[\s_\-]*\d{1,3}[\s_\-]*$", re.I)
_TRAIL_NUM = re.compile(r"[\s_\-]+\d{1,2}[\s_\-]*$")


def panel_base(name: str) -> str:
    """'M31 Panel 2' / 'M31-Panel2' / 'M31_Tile_3' / 'M31-2' -> 'M31'."""
    s = str(name or "").strip()
    b = _PANEL_WORD.sub("", s)
    if b != s and b:
        return b.strip(" _-")
    b = _TRAIL_NUM.sub("", s)
    if b != s and b and re.search(r"\d", b):       # 'M31-2', not 'NGC 7000' or 'Sh2-129'
        return b.strip(" _-")
    return s


def mosaic_name(objects) -> str:
    """The mosaic's target name from its panels' OBJECT names."""
    names = [str(o) for o in objects if o]
    if not names:
        return "Mosaic"
    bases = {panel_base(n) for n in names}
    if len(bases) == 1:
        return bases.pop() or "Mosaic"
    pre = names[0]
    for n in names[1:]:
        k = 0
        while k < min(len(pre), len(n)) and pre[k].lower() == n[k].lower():
            k += 1
        pre = pre[:k]
    pre = panel_base(pre.strip(" _-")) if pre.strip(" _-") else ""
    return pre.strip(" _-") or panel_base(names[0]) or "Mosaic"


def depth(m) -> float:
    """How much exposure a master holds (seconds, else frames) - the deepest panel is the reference."""
    for k in ("EXPTOTAL", "NCOMBINE"):
        v = P._num(m.get(k) if m.get(k) is not None else m.look.get(k))
        if v:
            return float(v)
    return 0.0


def sexa(v, hours=False):
    return P._sexa(v, hours=hours)


def pointing(look: dict):
    """(ra, dec) in degrees where the frame points: its plate solution, else the mount's RA / DEC."""
    w = st.wcs_of(look or {})
    if w is not None:
        return float(np.degrees(w["crval"][0])), float(np.degrees(w["crval"][1]))
    ra, dec = P._num(look.get("RA")), P._num(look.get("DEC"))
    if ra is None or dec is None:
        ra, dec = sexa(look.get("OBJCTRA"), hours=True), sexa(look.get("OBJCTDEC"))
    if ra is None or dec is None:
        return None
    return float(ra), float(dec)


def _unit(ra, dec):
    r, d = np.radians(ra), np.radians(dec)
    return np.array([np.cos(d) * np.cos(r), np.cos(d) * np.sin(r), np.sin(d)])


def separation(a, b) -> float:
    """Degrees between two (ra, dec) pointings."""
    c = float(np.clip(_unit(*a) @ _unit(*b), -1.0, 1.0))
    return math.degrees(math.acos(c))


def load_panels(paths, log=None) -> list:
    lg = log or (lambda t: None)
    out = []
    for p in paths:
        m = P.load_master(p)
        if m.autocrop:                       # the ragged stacking edges would show as a dark frame in the mosaic
            P.crop(m, m.autocrop)
        pan = Panel(m, label=f"{m.look.get('OBJECT') or Path(p).stem} ({m.filter})")
        pan.wcs = st.wcs_of(core.cards_to_dict(m.cards))
        out.append(pan)
    lg(f"{len(out)} panel master(s): " + ", ".join(p.label for p in out))
    return out


# ==========================================================================
# Plate solving the panels that have no solution yet
# ==========================================================================

def solve_panels(panels, astap_exe, work: Path, log=None, progress=None, cancel=None):
    import tools
    lg = log or (lambda t: None)
    todo = [p for p in panels if p.wcs is None]
    if not todo:
        return 0
    if not astap_exe:
        lg(f"{len(todo)} panel(s) not plate solved and ASTAP was not found - placing them by their stars instead")
        return 0
    astap = tools.ASTAP(astap_exe)
    memo, n = {}, 0
    for i, pan in enumerate(todo):
        if cancel and cancel():
            raise Cancelled()
        m = pan.master
        if progress:
            progress(f"Plate solving {pan.label}", i / len(todo))
        ra, dec, fov = P.solve_hints(m)
        tmp = work / f"mosaic_solve_{i}.fits"
        P._write_temp(m, tmp, mono=True)
        try:
            cards, note = P.solve_with_retries(astap, tmp, ra, dec, fov, 30.0, cancel, None, memo)
            if note:
                lg(note)
            P.apply_wcs(m, cards)
            pan.wcs = st.wcs_of(core.cards_to_dict(m.cards))
            n += pan.wcs is not None
            lg(f"Plate solved {pan.label}")
        except tools.ToolCancelled:
            raise Cancelled()
        except tools.ToolError as e:
            lg(f"Could not plate solve {pan.label} ({str(e).split(' - ')[0]}) - it is placed by its stars instead")
        finally:
            try:
                tmp.unlink()
            except OSError:
                pass
    return n


# ==========================================================================
# Placing the panels on one canvas
# ==========================================================================

def _grid(h, w, n=13):
    gx, gy = np.meshgrid(np.linspace(0, w - 1, n), np.linspace(0, h - 1, n))
    return np.c_[gx.ravel(), gy.ravel()]


def _lum_stars(m: P.Master, max_stars=600):
    try:
        pts, _ = st.find_stars(P._binned_lum(m), 2, max_stars=max_stars)
    except Exception:  # noqa: BLE001
        pts = np.empty((0, 2))
    return np.asarray(pts, float).reshape(-1, 2)


def canvas_wcs(panels) -> dict:
    """A flat projection around the centre of the solved panels, at the reference panel's pixel size and angle."""
    solved = [p for p in panels if p.wcs is not None]
    ref = solved[0]
    v = np.zeros(3)
    for p in solved:
        h, w = p.shape
        ra, dec = st._wcs_sky(p.wcs, np.array([(w - 1) / 2.0]), np.array([(h - 1) / 2.0]))
        v += _unit(np.degrees(ra[0]), np.degrees(dec[0]))
    v /= np.linalg.norm(v)
    ra0 = math.atan2(v[1], v[0]) % (2 * math.pi)
    dec0 = math.asin(float(np.clip(v[2], -1, 1)))
    return {"crval": np.array([ra0, dec0]), "crpix": np.array([1.0, 1.0]), "cd": np.array(ref.wcs["cd"], float)}


def _fit_maps(pan: Panel, to_canvas):
    """pan.fwd (panel -> canvas) from a function that maps panel points to the canvas."""
    h, w = pan.shape
    src = _grid(h, w)
    dst = to_canvas(src)
    pan.fwd = st.fit_poly(src, dst, POLY_ORDER, (h, w))


def _to_canvas(pan: Panel, pts):
    return st.poly_apply(pan.fwd, np.asarray(pts, float).reshape(-1, 2))


def place(panels, log=None, progress=None, cancel=None):
    """Work out every panel's mapping onto a common canvas. Returns the canvas WCS (None when no panel is
    solved: the canvas is then the reference panel's own pixel grid, extended)."""
    lg = log or (lambda t: None)
    for i, pan in enumerate(panels):
        if cancel and cancel():
            raise Cancelled()
        if progress:
            progress(f"Finding stars in {pan.label}", i / max(len(panels), 1))
        pan.stars = _lum_stars(pan.master)
    cw = None
    placed = []
    if any(p.wcs is not None for p in panels):
        cw = canvas_wcs(panels)
        for pan in panels:
            if pan.wcs is None:
                continue
            def tc(pts, pan=pan):
                ra, dec = st._wcs_sky(pan.wcs, pts[:, 0], pts[:, 1])
                x, y = st._wcs_pix(cw, ra, dec)
                return np.c_[x, y]
            _fit_maps(pan, tc)
            pan.how = "plate solution"
            placed.append(pan)
    else:
        ref = panels[0]
        _fit_maps(ref, lambda pts: pts.copy())
        ref.how = "reference"
        placed.append(ref)
    # the rest by their stars, against any panel already placed (most stars in common first)
    left = [p for p in panels if p not in placed]
    while left:
        best = None
        for pan in left:
            for other in placed:
                try:
                    T, n, rms, ok, j = st.match_frames(other.stars, pan.stars, tol=4.0)
                except ValueError:
                    continue
                T = _pair_transform(T, n, ok, j, other.stars, pan.stars)
                if best is None or n > best[2]:
                    best = (pan, other, n, rms, T)
        if best is None:
            names = ", ".join(p.label for p in left)
            raise MosaicError(f"Could not place {names}: no plate solution, and not enough stars in common with the "
                              "other panels. Plate solving finds it - set ASTAP up under Settings › Your Tools - or "
                              "check that the panels overlap (about 15-20% is usual)")
        pan, other, n, rms, T = best
        _fit_maps(pan, lambda pts, T=T, other=other: _to_canvas(other, st._apply(T, pts)))
        pan.how = f"stars ({n} shared with {other.label})"
        lg(f"Placed {pan.label} by {n} stars it shares with {other.label} (accuracy {rms:.2f} px)")
        placed.append(pan)
        left.remove(pan)
    return cw


def _footprint(pan: Panel):
    h, w = pan.shape
    e = np.linspace(0, 1, 17)
    edge = np.r_[np.c_[e * (w - 1), 0 * e], np.c_[e * (w - 1), 0 * e + h - 1],
                 np.c_[0 * e, e * (h - 1)], np.c_[0 * e + w - 1, e * (h - 1)]]
    return _to_canvas(pan, edge)


def _inside(pan: Panel, canvas_pts, margin=4.0):
    """Which canvas points fall inside the panel (through the inverse mapping)."""
    if pan.inv is None:
        return np.zeros(len(canvas_pts), bool)
    x0, y0 = pan.bbox[0], pan.bbox[1]
    p = st.poly_apply(pan.inv, np.asarray(canvas_pts, float) - [x0, y0])
    h, w = pan.shape
    return (p[:, 0] >= margin) & (p[:, 1] >= margin) & (p[:, 0] <= w - 1 - margin) & (p[:, 1] <= h - 1 - margin)


def _fit_inverse(pan: Panel, canvas_shape=None):
    h, w = pan.shape
    fp = _footprint(pan)
    x0, y0 = int(math.floor(fp[:, 0].min())), int(math.floor(fp[:, 1].min()))
    x1, y1 = int(math.ceil(fp[:, 0].max())) + 1, int(math.ceil(fp[:, 1].max())) + 1
    if canvas_shape is not None:
        H, W = canvas_shape
        x0, y0, x1, y1 = max(0, x0), max(0, y0), min(W, x1), min(H, y1)
    pan.bbox = (x0, y0, x1, y1)
    src = _grid(h, w, 17)
    dst = _to_canvas(pan, src) - [x0, y0]
    pan.inv = st.fit_poly(dst, src, POLY_ORDER, (max(y1 - y0, 2), max(x1 - x0, 2)))


def _fit_rigid(src, dst):
    """Turn + shift (no resizing: the panels share one pixel size on the canvas) taking src onto dst; a shift
    alone when there are few pairs or they lie almost on a line."""
    t = np.median(dst - src, axis=0)
    C = np.array([[1.0, 0.0, t[0]], [0.0, 1.0, t[1]]])
    if len(src) < 12 or min(np.ptp(src[:, 0]), np.ptp(src[:, 1])) < 30 and max(np.ptp(src[:, 0]),
                                                                               np.ptp(src[:, 1])) < 200:
        return C
    return _rigid(src, dst, limit=0.01)               # a fine-tune: never more than about half a degree


def _rigid(src, dst, limit=None, scale=1.0):
    """Least-squares turn (+ a fixed scale) and shift taking src onto dst."""
    ms, md = src.mean(0), dst.mean(0)
    a, b = src - ms, dst - md
    ang = math.atan2(float(np.sum(a[:, 0] * b[:, 1] - a[:, 1] * b[:, 0])), float(np.sum(a * b)))
    if limit is not None:
        ang = max(-limit, min(limit, ang))
    c, s_ = math.cos(ang), math.sin(ang)
    R = scale * np.array([[c, -s_], [s_, c]])
    return np.c_[R, md - R @ ms]


def _pair_transform(T, n, ok, j, ref_pts, tgt_pts):
    """match_frames gives a free affine fit; across a narrow overlap that can stretch the panel a little. Two
    panels of one rig have the same pixel size, so refit as turn + shift (+ mirror) unless the sizes differ."""
    A = np.asarray(T)[:, :2]
    det = float(np.linalg.det(A))
    sc = math.sqrt(abs(det))
    src, dst = tgt_pts[ok], ref_pts[j[ok]]
    if len(src) < 3:
        return T
    flip = det < 0
    if flip:
        src = src * [-1.0, 1.0]
    R = _rigid(src, dst, scale=1.0 if abs(sc - 1.0) < 0.03 else sc)
    if flip:
        R = R @ np.array([[-1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]])
    return R


def refine(panels, log=None, rounds=4):
    """Nudge each panel so its stars land on its neighbours' stars (the reference panel stays put)."""
    lg = log or (lambda t: None)
    for pan in panels:
        _fit_inverse(pan)
    worst = 0.0
    for rnd in range(rounds):
        worst = 0.0
        for k, pan in enumerate(panels):
            if k == 0 or pan.stars is None or len(pan.stars) < 6:
                continue
            mine = _to_canvas(pan, pan.stars)
            src, dst = [], []
            for other in panels:
                if other is pan or other.stars is None or len(other.stars) < 3:
                    continue
                theirs = _to_canvas(other, other.stars)
                a = mine[_inside(other, mine)]
                b = theirs[_inside(pan, theirs)]
                if len(a) < 3 or len(b) < 3:
                    continue
                d = np.hypot(a[:, None, 0] - b[None, :, 0], a[:, None, 1] - b[None, :, 1])
                j = d.argmin(1)
                dd = d[np.arange(len(a)), j]
                tol = 6.0 if rnd == 0 else 3.0
                ok = dd < tol
                if ok.sum() < 3:
                    continue
                src.append(a[ok])
                dst.append(b[j[ok]])
            if not src:
                continue
            src, dst = np.vstack(src), np.vstack(dst)
            C = _fit_rigid(src, dst)
            for _it in range(2):                       # without the pairs that disagree (chance neighbours)
                r = np.hypot(*(st._apply(C, src) - dst).T)
                good = r < max(3 * 1.4826 * float(np.median(r)), 0.5)
                if good.sum() < 3:
                    break
                C = _fit_rigid(src[good], dst[good])
            moved = float(np.max(np.hypot(*(st._apply(C, src) - src).T)))
            worst = max(worst, moved)
            fwd = pan.fwd
            _fit_maps(pan, lambda pts, fwd=fwd, C=C: st._apply(C, st.poly_apply(fwd, pts)))
            _fit_inverse(pan)
        if worst < 0.05:
            break
    rms, n = overlap_star_rms(panels)
    lg("Fine-tuned the panels on the stars in their overlaps" +
       (f": {n} stars shared, they agree to {rms:.2f} pixel" if n else ""))
    return rms, n


def overlap_star_rms(panels, tol=2.0):
    """How well the panels' stars agree where panels overlap: (RMS distance in canvas pixels, number of pairs)."""
    canv = [_to_canvas(p, p.stars) if p.stars is not None and len(p.stars) else np.empty((0, 2)) for p in panels]
    d2 = []
    for i in range(len(panels)):
        for j in range(i + 1, len(panels)):
            a = canv[i][_inside(panels[j], canv[i])] if len(canv[i]) else canv[i]
            b = canv[j]
            if len(a) < 3 or len(b) < 3:
                continue
            d = np.hypot(a[:, None, 0] - b[None, :, 0], a[:, None, 1] - b[None, :, 1]).min(1)
            d2 += list(d[d < tol] ** 2)
    if not d2:
        return 0.0, 0
    return float(math.sqrt(np.mean(d2))), len(d2)


def canvas_layout(panels, cw):
    """Shift everything so the canvas starts at 0, 0; returns (height, width) and the shifted canvas WCS."""
    pts = np.vstack([_footprint(p) for p in panels])
    x0, y0 = math.floor(pts[:, 0].min()), math.floor(pts[:, 1].min())
    W = int(math.ceil(pts[:, 0].max()) - x0 + 1)
    H = int(math.ceil(pts[:, 1].max()) - y0 + 1)
    if W * H > 400e6:
        raise MosaicError(f"The mosaic would be {W} x {H} pixels - too big. Are all the panels of the same target? "
                          "(A panel far from the others usually means a wrong plate solution or a stray master)")
    for pan in panels:
        fwd = pan.fwd
        _fit_maps(pan, lambda pts, fwd=fwd: st.poly_apply(fwd, pts) - [x0, y0])
        _fit_inverse(pan, (H, W))
    if cw is not None:
        cw = dict(cw)
        cw["crpix"] = np.array([1.0 - x0, 1.0 - y0])
    return (H, W), cw


# ==========================================================================
# Brightness: scale and tilted plane per panel, from the overlaps
# ==========================================================================

def _feather(h, w, frac=FEATHER):
    f = max(8.0, frac * min(h, w))
    x = np.minimum(np.arange(w), np.arange(w)[::-1]).astype(np.float32)
    y = np.minimum(np.arange(h), np.arange(h)[::-1]).astype(np.float32)
    d = np.minimum(y[:, None], x[None, :]) / np.float32(f)
    d = np.clip(d, 0, 1)
    return (d * d * (3 - 2 * d)).astype(np.float32)          # smooth step


def _low_model(pan: Panel, k: int):
    """canvas-low px -> panel-low px."""
    x0, y0, x1, y1 = pan.bbox
    lx0, ly0 = x0 // k, y0 // k
    lx1, ly1 = -(-x1 // k), -(-y1 // k)
    gx, gy = np.meshgrid(np.linspace(lx0, lx1, 15), np.linspace(ly0, ly1, 15))
    low = np.c_[gx.ravel(), gy.ravel()]
    full = low * k + (k - 1) / 2.0
    p = st.poly_apply(pan.inv, full - [x0, y0])
    p = (p - (k - 1) / 2.0) / k
    rel = low - [lx0, ly0]
    return (lx0, ly0, lx1, ly1), st.fit_poly(rel, p, POLY_ORDER, (max(ly1 - ly0, 2), max(lx1 - lx0, 2)))


def _low_warp(pan: Panel, k: int, low_shape):
    """The panel (all channels) and its feather on the small canvas: (planes, weight), NaN outside."""
    (lx0, ly0, lx1, ly1), mdl = _low_model(pan, k)
    Hl, Wl = low_shape
    lx1, ly1 = min(lx1, Wl), min(ly1, Hl)
    d = pan.master.data
    planes = [st._bin(np.nan_to_num(c, nan=0.0), k) for c in d]
    hh, ww = planes[0].shape
    wts = _feather(hh, ww)
    outs = st.warp_poly(planes + [wts], mdl, (ly1 - ly0, lx1 - lx0))
    full = [np.full(low_shape, np.nan, np.float32) for _ in outs]
    for f, o in zip(full, outs):
        f[ly0:ly1, lx0:lx1] = o
    return full[:-1], full[-1]


def _detail(img, valid, r=3):
    """Fine detail (stars) of a small-canvas plane: minus its local average, NaN-safe."""
    v = valid & np.isfinite(img)
    a = np.where(v, img, 0).astype(np.float32)
    num = st._box(a, r)
    den = st._box(v.astype(np.float32), r)
    out = a - num / np.maximum(den, 1e-6)
    out[~v] = 0
    return out


def _phot(plane, pts, r=4):
    """Star brightness (sum above the local sky) at each point, NaN where it can't be measured."""
    H, W = plane.shape
    out = np.full(len(pts), np.nan)
    R = 2 * r + 2
    for n, (x, y) in enumerate(pts):
        ix, iy = int(round(x)), int(round(y))
        if ix < R or iy < R or ix >= W - R or iy >= H - R:
            continue
        box = plane[iy - R:iy + R + 1, ix - R:ix + R + 1].astype(np.float64)
        core_ = box[R - r:R + r + 1, R - r:R + r + 1]
        if not np.all(np.isfinite(box)) or core_.max() > 60000:          # cut off by an edge, or saturated
            continue
        ring = np.r_[box[:2].ravel(), box[-2:].ravel(), box[2:-2, :2].ravel(), box[2:-2, -2:].ravel()]
        out[n] = float(core_.sum() - np.median(ring) * core_.size)
    return out


def star_ratios(group, c):
    """For each overlapping pair (i, j) of one filter: (i, j, log(brightness_i / brightness_j), stars) from the
    stars both panels show - the most exact way to see that one panel came out brighter (thin cloud, a lower sky,
    another exposure time)."""
    canv = [_to_canvas(p, p.stars) if p.stars is not None and len(p.stars) else np.empty((0, 2)) for p in group]
    out = []
    for i in range(len(group)):
        for j in range(i + 1, len(group)):
            a, b = canv[i], canv[j]
            if len(a) < 3 or len(b) < 3:
                continue
            d = np.hypot(a[:, None, 0] - b[None, :, 0], a[:, None, 1] - b[None, :, 1])
            k = d.argmin(1)
            ok = d[np.arange(len(a)), k] < 1.5
            if ok.sum() < 5:
                continue
            ia, ib = np.nonzero(ok)[0], k[ok]
            fa = _phot(group[i].master.data[c], group[i].stars[ia])
            fb = _phot(group[j].master.data[c], group[j].stars[ib])
            good = np.isfinite(fa) & np.isfinite(fb) & (fa > 0) & (fb > 0)
            if good.sum() < 5:
                continue
            lr = np.log(fa[good] / fb[good])
            med = float(np.median(lr))
            keep = np.abs(lr - med) < max(3 * 1.4826 * float(np.median(np.abs(lr - med))), 0.02)
            out.append((i, j, float(np.median(lr[keep])), int(keep.sum())))
    return out


def _norm_xy(xs, ys, shape):
    H, W = shape
    s = max(H, W) / 2.0
    return (xs - W / 2.0) / s, (ys - H / 2.0) / s


def match_brightness(panels, canvas_shape, log=None, cancel=None):
    """Per panel and channel: scale s and plane (o, gx, gy) so that s*value + plane agrees in every overlap.
    Each filter is matched on its own; the deepest panel of each keeps its own levels."""
    lg = log or (lambda t: None)
    H, W = canvas_shape
    k = max(1, int(math.ceil(max(H, W) / LOW_SIDE)))
    low_shape = (-(-H // k), -(-W // k))
    by_filter = {}
    for pan in panels:
        by_filter.setdefault((pan.filter, pan.master.data.shape[0]), []).append(pan)
    rng = np.random.default_rng(0)
    quality = {}
    for (filt, nch), group in by_filter.items():
        for pan in group:
            pan.scale = [1.0] * nch
            pan.plane = [(0.0, 0.0, 0.0)] * nch
        if len(group) < 2:
            continue
        lows = []
        for pan in group:
            if cancel and cancel():
                raise Cancelled()
            lows.append(_low_warp(pan, k, low_shape))
        ref = max(range(len(group)), key=lambda i: depth(group[i].master))
        ys, xs = np.mgrid[0:low_shape[0], 0:low_shape[1]]
        nx, ny = _norm_xy(xs * k + (k - 1) / 2.0, ys * k + (k - 1) / 2.0, canvas_shape)
        pairs = []
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                both = (lows[i][1] > 0.3) & (lows[j][1] > 0.3)
                for c in range(nch):
                    both &= np.isfinite(lows[i][0][c]) & np.isfinite(lows[j][0][c])
                if both.sum() >= 30:
                    pairs.append((i, j, both))
        if not pairs:
            lg(f"{filt}: the panels don't overlap enough to even out their brightness - joined as they are")
            continue
        for c in range(nch):
            # scale: from the brightness of the stars both panels show (each measured in its own master); where
            # an overlap has too few, from the fine detail of the small canvas copies
            rows, rhs, wts = [], [], []
            done = set()
            for i, j, lr, n in star_ratios(group, c):
                if abs(lr) > math.log(4.0):
                    continue
                row = np.zeros(len(group))
                row[j], row[i] = 1.0, -1.0              # value_i = r * value_j  ->  s_j = r * s_i
                rows.append(row)
                rhs.append(lr)
                wts.append(math.sqrt(n))
                done.add((i, j))
            hp = [_detail(lw[0][c], lw[1] > 0.3) for lw in lows]
            for i, j, both in pairs:
                if (i, j) in done:
                    continue
                a, b = hp[i][both], hp[j][both]
                na = 1.4826 * float(np.median(np.abs(a - np.median(a)))) + 1e-9
                nb = 1.4826 * float(np.median(np.abs(b - np.median(b)))) + 1e-9
                sel = (a > 5 * na) & (b > 5 * nb)
                if sel.sum() < 10:                     # no stars in common here: nothing to measure a scale on
                    continue
                sa, sb = float(np.sum(a[sel])), float(np.sum(b[sel]))
                r = sa / sb
                if not (0.25 <= r <= 4.0):
                    continue
                row = np.zeros(len(group))
                row[j], row[i] = 1.0, -1.0
                rows.append(row)
                rhs.append(math.log(r))
                wts.append(0.5 * math.sqrt(float(sel.sum())))
            logs = np.zeros(len(group))
            if rows:
                A = np.array(rows) * np.array(wts)[:, None]
                bvec = np.array(rhs) * np.array(wts)
                keep = [q for q in range(len(group)) if q != ref]
                sol, *_ = np.linalg.lstsq(A[:, keep], bvec, rcond=None)
                logs[keep] = sol
            s = np.exp(np.clip(logs, math.log(0.25), math.log(4.0)))
            # planes: s_i*a + P_i(x, y) = s_j*b + P_j(x, y), the reference panel's plane is 0
            keep = [q for q in range(len(group)) if q != ref]
            col = {q: n for n, q in enumerate(keep)}
            A_rows, b_rows = [], []
            for i, j, both in pairs:
                a, b = lows[i][0][c][both], lows[j][0][c][both]
                X, Y = nx[both], ny[both]
                va, vb = s[i] * a, s[j] * b
                sky = (va < np.percentile(va, 80)) & (vb < np.percentile(vb, 80))   # stars and bright cores out
                idx = np.nonzero(sky)[0]
                if len(idx) > 3000:
                    idx = rng.choice(idx, 3000, replace=False)
                M = np.zeros((len(idx), 3 * len(keep)))
                terms = np.c_[np.ones(len(idx)), X[idx], Y[idx]]
                if i in col:
                    M[:, 3 * col[i]:3 * col[i] + 3] += terms
                if j in col:
                    M[:, 3 * col[j]:3 * col[j] + 3] -= terms
                A_rows.append(M)
                b_rows.append(vb[idx] - va[idx])
            if not keep:
                continue
            A = np.vstack(A_rows)
            bvec = np.concatenate(b_rows)
            lam = math.sqrt(0.002 * len(bvec))
            R = np.zeros((2 * len(keep), A.shape[1]))
            for n in range(len(keep)):
                R[2 * n, 3 * n + 1] = lam
                R[2 * n + 1, 3 * n + 2] = lam
            sol = np.zeros(A.shape[1])
            for _it in range(2):                       # once more without the samples that disagree badly
                sol, *_ = np.linalg.lstsq(np.vstack([A, R]), np.r_[bvec, np.zeros(len(R))], rcond=None)
                res = A @ sol - bvec
                mad = float(np.median(np.abs(res))) * 1.4826 + 1e-9
                good = np.abs(res) < 4 * mad
                A, bvec = A[good], bvec[good]
            for q in range(len(group)):
                group[q].scale[c] = float(s[q])
                group[q].plane[c] = tuple(float(v) for v in sol[3 * col[q]:3 * col[q] + 3]) if q in col \
                    else (0.0, 0.0, 0.0)
        for pan in group:
            lg(f"{pan.label}: brightness x{np.mean(pan.scale):.3f}, sky "
               f"{'+' if np.mean([p[0] for p in pan.plane]) >= 0 else ''}{np.mean([p[0] for p in pan.plane]):.1f} ADU")
        before, after = [], []
        for i, j, both in pairs:                     # how well the overlaps agree now, on their sky
            for c in range(nch):
                a, b = lows[i][0][c][both], lows[j][0][c][both]
                X, Y = nx[both], ny[both]
                fa = group[i].scale[c] * a + _plane_at(group[i].plane[c], X, Y)
                fb = group[j].scale[c] * b + _plane_at(group[j].plane[c], X, Y)
                sky = (a < np.percentile(a, 80)) & (b < np.percentile(b, 80))
                before.append(float(np.median(np.abs(a[sky] - b[sky]))))
                after.append(float(np.median(np.abs(fa[sky] - fb[sky]))))
        if before:
            quality[filt] = (float(np.median(before)), float(np.median(after)))
            lg(f"{filt}: brightness difference in the overlaps {quality[filt][0]:.0f} ADU before, "
               f"{quality[filt][1]:.0f} ADU after evening out")
    return quality


def _plane_at(pl, X, Y):
    o, gx, gy = pl
    return o + gx * X + gy * Y


# ==========================================================================
# Blending and saving
# ==========================================================================

def blend(group, canvas_shape, cancel=None, progress=None):
    """One filter's panels on the canvas: feathered, brightness-matched average. Returns (data (c, H, W), covered)."""
    H, W = canvas_shape
    nch = group[0].master.data.shape[0]
    acc = np.zeros((nch, H, W), np.float32)
    wacc = np.zeros((H, W), np.float32)
    for n, pan in enumerate(group):
        if cancel and cancel():
            raise Cancelled()
        if progress:
            progress(f"Blending {pan.label}", n / len(group))
        x0, y0, x1, y1 = pan.bbox
        if x1 <= x0 or y1 <= y0:
            continue
        h, w = pan.shape
        planes = [np.nan_to_num(c, nan=0.0) for c in pan.master.data] + [_feather(h, w)]
        outs = st.warp_poly(planes, pan.inv, (y1 - y0, x1 - x0))
        wt = np.nan_to_num(outs[-1], nan=0.0)
        wt[~np.isfinite(outs[0])] = 0.0
        ys = np.arange(y0, y1, dtype=np.float64)
        xs = np.arange(x0, x1, dtype=np.float64)
        nxv, _ = _norm_xy(xs, np.zeros_like(xs), canvas_shape)
        _, nyv = _norm_xy(np.zeros_like(ys), ys, canvas_shape)
        for c in range(nch):
            o, gx, gy = pan.plane[c] if pan.plane else (0.0, 0.0, 0.0)
            s = pan.scale[c] if pan.scale else 1.0
            v = np.nan_to_num(outs[c], nan=0.0) * np.float32(s)
            v += np.float32(o) + (np.float32(gy) * nyv.astype(np.float32))[:, None] \
                + (np.float32(gx) * nxv.astype(np.float32))[None, :]
            acc[c, y0:y1, x0:x1] += v * wt
        wacc[y0:y1, x0:x1] += wt
        del outs
    covered = wacc > 1e-6
    np.maximum(wacc, np.float32(1e-6), out=wacc)
    out = acc                                    # in place: a mosaic is big
    for c in range(nch):
        out[c] /= wacc
        out[c][~covered] = 0.0
    del wacc
    samp = out[:, covered[::7, ::7].nonzero()[0] * 7, covered[::7, ::7].nonzero()[1] * 7] if covered.any() else None
    lo = float(np.percentile(samp, 0.1)) if samp is not None and samp.size else 0.0
    if lo < 0:                                   # a plane can push the faintest sky just under 0
        for c in range(nch):
            out[c][covered] -= np.float32(lo)
    np.clip(out, 0, None, out=out)
    return out, covered


def count_panels(panels) -> int:
    """Distinct places on the sky (masters of one panel through several filters count once)."""
    centres = []
    for pan in panels:
        h, w = pan.shape
        c = _to_canvas(pan, [[(w - 1) / 2.0, (h - 1) / 2.0]])[0]
        if all(np.hypot(*(c - q)) > 0.1 * min(h, w) for q in centres):
            centres.append(c)
    return len(centres)


def covered_rect(covered: np.ndarray):
    """The largest rectangle fully inside the mosaic (x0, y0, x1, y1), or None."""
    H, W = covered.shape
    step = max(4, int(math.ceil(max(H, W) / 600)))
    small = covered[::step, ::step]
    r = st.largest_rectangle(small)
    if r is None:
        return None
    r0, c0, r1, c1 = r
    x0 = min(W, (c0 + 1) * step) if c0 > 0 else 0
    y0 = min(H, (r0 + 1) * step) if r0 > 0 else 0
    x1 = max(x0, c1 * step) if c1 < small.shape[1] - 1 else W
    y1 = max(y0, r1 * step) if r1 < small.shape[0] - 1 else H
    if (x1 - x0) * (y1 - y0) < 0.2 * H * W:
        return None
    return int(x0), int(y0), int(x1), int(y1)


def wcs_cards(cw) -> list:
    if cw is None:
        return []
    cd = cw["cd"]
    return [("CTYPE1", "RA---TAN", ""), ("CTYPE2", "DEC--TAN", ""), ("EQUINOX", 2000.0, ""),
            ("CRVAL1", float(np.degrees(cw["crval"][0])), "[deg] mosaic centre"),
            ("CRVAL2", float(np.degrees(cw["crval"][1])), "[deg] mosaic centre"),
            ("CRPIX1", float(cw["crpix"][0]), ""), ("CRPIX2", float(cw["crpix"][1]), ""),
            ("CD1_1", float(cd[0, 0]), ""), ("CD1_2", float(cd[0, 1]), ""),
            ("CD2_1", float(cd[1, 0]), ""), ("CD2_2", float(cd[1, 1]), ""),
            ("PLTSOLVD", True, "from the panels' plate solutions (SubZeroAstro mosaic)")]


_DROP = re.compile(r"^(CTYPE\d|CRVAL\d|CRPIX\d|CD\d_\d|CDELT\d|CROTA\d|PC\d_\d|CUNIT\d|PLTSOLVD|[AB]P?_\d_\d|"
                   r"[AB]P?_ORDER|EQUINOX|RADESYS|LONPOLE|LATPOLE|AUTOCROP|ALIGNREF|OBJECT|OBJCTRA|OBJCTDEC|RA|DEC|"
                   r"NAXIS\d?|HISTORY|COMMENT|PIXRANGE)$")


@dataclass
class MosaicResult:
    name: str
    files: list = field(default_factory=list)
    shape: tuple = (0, 0)
    log: list = field(default_factory=list)
    panels: int = 0
    filters: list = field(default_factory=list)
    quality: dict = field(default_factory=dict)      # filter -> (overlap difference before, after) in ADU
    star_rms: float = 0.0                             # how well the stars in the overlaps agree (pixels)
    star_pairs: int = 0
    placed: dict = field(default_factory=dict)        # panel -> how it was placed


def stitch(paths, out_dir=None, astap_exe=None, log=None, progress=None, cancel=None,
           formats=("fits",)) -> MosaicResult:
    """Join panel masters (any number of filters) into one master per filter, all on one canvas."""
    lines = []

    def lg(t):
        lines.append(t)
        if log:
            log(t)

    def pr(t, f):
        if progress:
            progress(t, max(0.0, min(1.0, f)))

    can = cancel or (lambda: False)
    paths = [Path(p) for p in paths]
    if len(paths) < 2:
        raise MosaicError("Pick the master lights of at least two panels")
    pr("Reading the panel masters", 0.0)
    panels = load_panels(paths, lg)
    import tempfile
    with tempfile.TemporaryDirectory(prefix="sza_mosaic_") as work:
        solve_panels(panels, astap_exe, Path(work), lg, lambda t, f: pr(t, 0.02 + 0.2 * f), can)
    # the deepest master goes first: the reference panel keeps its position and levels
    panels.sort(key=lambda p: -depth(p.master))
    cw = place(panels, lg, lambda t, f: pr(t, 0.22 + 0.08 * f), can)
    if can():
        raise Cancelled()
    pr("Fine-tuning on the stars in the overlaps", 0.32)
    star_rms, star_n = refine(panels, lg)
    shape, cw = canvas_layout(panels, cw)
    lg(f"Mosaic canvas {shape[1]} x {shape[0]} pixels" + ("" if cw is None else
                                                            ", plate-solved (centre and scale known)"))
    pr("Evening out the brightness of the panels", 0.4)
    quality = match_brightness(panels, shape, lg, can)
    name = mosaic_name([p.master.look.get("OBJECT") for p in panels])
    res = MosaicResult(name=name, shape=shape, panels=count_panels(panels), quality=quality, star_rms=star_rms,
                       star_pairs=star_n, placed={p.label: p.how for p in panels})
    by_filter = {}
    for pan in panels:
        by_filter.setdefault((pan.filter, pan.master.data.shape[0]), []).append(pan)
    out_dir = Path(out_dir) if out_dir else paths[0].parent.parent / st.safe_name(f"{name}_Mosaic")
    out_dir.mkdir(parents=True, exist_ok=True)
    for n, ((filt, _nch), group) in enumerate(by_filter.items()):
        f0 = 0.45 + 0.5 * n / len(by_filter)
        data, covered = blend(group, shape, can, lambda t, f: pr(t, f0 + 0.5 / len(by_filter) * f))
        ref = group[0].master
        rect = covered_rect(covered)
        header = [(k, v, c) for k, v, c in ref.cards if not _DROP.match(str(k))]
        header = [h for h in header if h[0] not in ("IMAGETYP", "EXPTOTAL", "NCOMBINE")]
        tot = [P._num(core.cards_to_dict(g.master.cards).get("EXPTOTAL")) for g in group]
        header += [("IMAGETYP", "Master Light", ""), ("OBJECT", name, "mosaic"),
                   ("MOSAIC", len(group), "panels joined (SubZeroAstro)"),
                   ("EXPTOTAL", round(sum(t for t in tot if t), 1) if any(tot) else None,
                    "[s] all panels together"),
                   ("ALIGNREF", f"{st.safe_name(name)}_mosaic", "all masters of this mosaic share this grid")]
        if rect:
            header.append(("AUTOCROP", " ".join(str(v) for v in rect), "x0 y0 x1 y1 inside every edge"))
        header += wcs_cards(cw)
        for i, pan in enumerate(group):
            header.append(("HISTORY", f"Panel {i + 1}: {Path(pan.master.path).name} - placed by {pan.how}", ""))
        fl = st.safe_name(core._ascii(filt)) if filt and filt != "No filter" else ""
        base_name = "_".join(x for x in (st.safe_name(name), fl, f"Mosaic{len(group)}", "Master") if x)
        base = st._unique_base(out_dir / base_name, formats)
        img = data[0] if data.shape[0] == 1 else data
        res.files += st.save_master_file(base, img, header, formats, "MasterLight")
        res.filters.append(filt)
        lg(f"Saved the {filt} mosaic ({len(group)} panels)")
        del data
    log_path = out_dir / f"{st.safe_name(name)}_mosaic_log.txt"
    log_path.write_text("\n".join([f"{name} mosaic", ""] + lines) + "\n", encoding="utf-8")
    res.log = lines
    pr("Done", 1.0)
    return res
