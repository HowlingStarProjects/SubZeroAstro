"""Filter Identifier: which filter was in the drawer? Works out the filter of a colour-camera sub from the picture
itself, not its FILTER header - for when the ASIAIR or N.I.N.A. didn't know (a filter drawer) or it was forgotten.

Every filter leaves a fingerprint in a colour camera's raw (Bayer) data:
  - the sky's colour: red / green and blue / green of the background (light pollution and Moon seen through it)
  - the stars' colour: red / green and blue / green of hundreds of stars (narrowband filters pass a sliver of
    their light, a broadband filter nearly all of it)
  - how bright the sky is per square arcsecond and second (a narrowband filter cuts it 20-70x)
  - where the focuser was (ASIAIR / N.I.N.A. write FOCUSPOS): each filter's glass moves the focus - and with no
    filter at all it moves a lot, which tells 'no filter' from a clear (L / UV-IR cut) filter
The reference fingerprints were measured on NGC 281 with an ASI2600MC Air on an Askar SQA55, every filter on the
same nights with an autofocus run before each (2026-11-16/17, Tomball TX). Frames whose FILTER header is known can
teach it more (learned references, per camera and rig). R, G, B and single-line (Ha, OIII, SII) filters are told
by their colour alone - one set of pixels lights up.
"""
from __future__ import annotations

import json
import math
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

# --------------------------------------------------------------------------- references
# filter -> (sky R/G, sky B/G, star R/G, star B/G, sky brightness e-/s/arcsec^2 (green), focus offset from no filter)
# measured with filter_id.measure() on the reference subs (see the module text); built in by build_references().
REFERENCES = {}              # filled at the bottom of this file (built-in reference fingerprints)
REF_CAMERA = "ZWO ASI2600MC Air"
REF_RIG = "Askar SQA55 (265 mm)"
TESTED_SENSORS = ("2600MC", "2600 MC", "6200MC", "6200 MC", "IMX571", "IMX455", "268C", "QHY268", "QHY600",
                  "POSEIDON-C", "POSEIDON C", "ATR2600C", "ATR3CMOS26000")
# filters that look alike: reported together, with the best guess named
GROUPS = (("No filter", "L-CCD"), ("L-Para", "IDAS NBZ II"), ("Askar D1", "Askar D2", "L-eXtreme"))
# how far a feature may wander (log scale) before it counts against a filter: colour is steady from night to night,
# brightness much less so (Moon, haze, altitude, another telescope's focal ratio)
SIGMA = {"sky_rg": 0.07, "sky_bg": 0.06, "star_rg": 0.07, "star_bg": 0.07, "bright": 0.40}
FEATURES = tuple(SIGMA)
MIN_STARS = 30
CLOSE = 6.0                  # filters scoring within this of the best are named as possibilities too


@dataclass
class Measure:
    path: str
    ok: bool = False
    why: str = ""                     # when not ok: why it can't be measured
    camera: str = ""
    colour: bool = True
    tested_sensor: bool = False
    exptime: float = 0.0
    focal: float | None = None
    focus: int | None = None
    header_filter: str = ""
    stars: int = 0
    sky: tuple = (0.0, 0.0, 0.0)      # R, G, B sky in ADU per second (bias removed)
    feats: dict = field(default_factory=dict)

    def rig_key(self) -> str:
        return f"{self.camera}|{int(round(self.focal)) if self.focal else '?'}"


@dataclass
class Result:
    name: str                          # the filter (or group) to show
    best: str                          # the single best filter name (written into FILTER)
    sure: str                          # "high", "medium", "low", "none"
    why: str
    scores: list = field(default_factory=list)      # [(filter, score)] best first


# --------------------------------------------------------------------------- measuring
def _planes(data, pattern):
    pat = (pattern or "RGGB").strip().upper()[:4]
    if len(pat) != 4 or set(pat) - set("RGB"):
        pat = "RGGB"
    s = {}
    for idx, (dy, dx) in enumerate(((0, 0), (0, 1), (1, 0), (1, 1))):
        s.setdefault(pat[idx], []).append(data[dy::2, dx::2])
    r, b = s["R"][0], s["B"][0]
    g = (s["G"][0] + s["G"][1]) / 2.0 if len(s.get("G", [])) == 2 else s["G"][0]
    h, w = min(r.shape[0], g.shape[0], b.shape[0]), min(r.shape[1], g.shape[1], b.shape[1])
    return r[:h, :w].astype(np.float32), g[:h, :w].astype(np.float32), b[:h, :w].astype(np.float32)


def _bias(look, planes) -> float:
    """The camera's zero level. ZWO cameras: about 10 ADU per OFFSET step (16-bit); else from the darkest pixels."""
    try:
        off = float(look.get("OFFSET"))
        inst = str(look.get("INSTRUME") or "").upper()
        if "ZWO" in inst or "ASI" in inst:
            return off * 10.0
    except (TypeError, ValueError):
        pass
    lows = [float(np.percentile(p[::4, ::4], 0.05)) for p in planes]
    return max(0.0, min(lows) - 5.0)


def _box3(a):
    """3 x 3 mean (edges as they are)."""
    out = a.copy()
    out[1:-1, 1:-1] = (a[:-2, :-2] + a[:-2, 1:-1] + a[:-2, 2:] + a[1:-1, :-2] + a[1:-1, 1:-1] + a[1:-1, 2:]
                       + a[2:, :-2] + a[2:, 1:-1] + a[2:, 2:]) / 9.0
    return out


def _maxf(a, size):
    """size x size running maximum (separable, plain NumPy)."""
    r = size // 2
    h, w = a.shape
    p = np.pad(a, r, mode="edge")
    t = p[:, 0:w].copy()
    for k in range(1, 2 * r + 1):
        np.maximum(t, p[:, k:k + w], out=t)
    o = t[0:h].copy()
    for k in range(1, 2 * r + 1):
        np.maximum(o, t[k:k + h], out=o)
    return o


def _stars(r, g, b, bias, sat):
    """Median R/G and B/G of the unsaturated stars (box photometry, local background). (n, rg, bg)"""
    lum = r + g + b
    small = lum[::2, ::2]
    med = float(np.median(small))
    sd = 1.4826 * float(np.median(np.abs(small - med))) + 1e-6
    sm = _box3(lum)
    mx = _maxf(sm, 11)
    ys, xs = np.nonzero((sm == mx) & (sm > med + 15 * sd))
    h, w = lum.shape
    ok = (ys > 16) & (ys < h - 16) & (xs > 16) & (xs < w - 16)
    ys, xs = ys[ok], xs[ok]
    if len(ys) > 3000:                               # the brightest 3000 are plenty
        o = np.argsort(-sm[ys, xs])[:3000]
        ys, xs = ys[o], xs[o]
    rg, bg = [], []
    for y, x in zip(ys, xs):
        box = (slice(y - 4, y + 5), slice(x - 4, x + 5))
        if max(r[box].max(), g[box].max(), b[box].max()) > sat:
            continue
        fl = []
        for p in (r, g, b):
            ring = np.concatenate([p[y - 12:y + 13, x - 12:x - 7].ravel(), p[y - 12:y + 13, x + 8:x + 13].ravel()])
            fl.append(float(p[box].sum() - 81 * np.median(ring)))
        if fl[1] <= 0 or fl[1] < 20 * math.sqrt(81) * (sd / 3):
            continue
        rg.append(fl[0] / fl[1])
        bg.append(fl[2] / fl[1])
    if not rg:
        return 0, None, None
    rg, bg = np.array(rg), np.array(bg)
    keep = (rg > 0) & (bg > 0)
    if keep.sum() < 1:
        return 0, None, None
    return int(keep.sum()), float(np.median(rg[keep])), float(np.median(bg[keep]))


def measure(path, data=None, look=None) -> Measure:
    """The fingerprint of one sub (raw colour data, as it came off the camera)."""
    import fits_core as core
    m = Measure(path=str(path))
    try:
        if data is None or look is None:
            data, _cards, look = core.read_fits(path)
    except Exception as e:  # noqa: BLE001
        m.why = f"couldn't read it ({e})"
        return m
    look = look or {}
    m.camera = str(look.get("INSTRUME") or "").strip()
    m.header_filter = str(look.get("FILTER") or "").strip()
    up = m.camera.upper().replace(" ", "")
    m.tested_sensor = any(t.replace(" ", "") in up for t in TESTED_SENSORS)
    try:
        m.exptime = float(look.get("EXPTIME") or look.get("EXPOSURE") or 0)
    except (TypeError, ValueError):
        m.exptime = 0.0
    try:
        m.focal = float(look.get("FOCALLEN")) if look.get("FOCALLEN") not in (None, "") else None
    except (TypeError, ValueError):
        m.focal = None
    try:
        m.focus = int(float(look.get("FOCUSPOS"))) if look.get("FOCUSPOS") not in (None, "") else None
    except (TypeError, ValueError):
        m.focus = None
    data = np.asarray(data)
    if data.ndim == 3:
        m.colour = False
        m.why = "already debayered (a colour picture, not a raw sub) - the identifier needs the raw camera data"
        return m
    if not look.get("BAYERPAT") and "MC" not in up and "COLOR" not in up and "COLOUR" not in up:
        m.colour = False
        m.why = "a monochrome camera - there's no colour to tell the filter by"
        return m
    if m.exptime <= 0:
        m.why = "no exposure time in the header"
        return m
    r, g, b = _planes(data, look.get("BAYERPAT"))
    bias = _bias(look, (r, g, b))
    sky = [float(np.percentile(p[::4, ::4], 30)) - bias for p in (r, g, b)]
    m.sky = tuple(s / m.exptime for s in sky)
    if sky[1] <= 1.0:
        m.why = "the sky is too dark to measure (or the camera's zero level is unusual)"
        return m
    m.feats["sky_rg"] = max(sky[0], 0.5) / sky[1]
    m.feats["sky_bg"] = max(sky[2], 0.5) / sky[1]
    try:
        gain = float(look.get("EGAIN") or 0) or None
        pix = float(look.get("XPIXSZ") or 0) or None
    except (TypeError, ValueError):
        gain = pix = None
    if gain and pix and m.focal:
        scale = 206.265 * pix / m.focal * 2            # one Bayer plane pixel = 2 x 2 camera pixels
        m.feats["bright"] = m.sky[1] * gain / (scale * scale)
    sat = 0.9 * float(np.nanmax(data)) if np.nanmax(data) > 0 else 60000.0
    n, srg, sbg = _stars(r - bias, g - bias, b - bias, bias, sat - bias)
    m.stars = n
    if n >= MIN_STARS:
        m.feats["star_rg"], m.feats["star_bg"] = srg, sbg
    m.ok = True
    return m


# --------------------------------------------------------------------------- learned references
def learned_file() -> Path:
    import fits_core as core
    d = core.cache_dir() / "filter_id"
    d.mkdir(parents=True, exist_ok=True)
    return d / "learned.json"


def load_learned(path=None) -> list:
    try:
        v = json.loads(Path(path or learned_file()).read_text(encoding="utf-8"))
        return [x for x in v if isinstance(x, dict) and x.get("filter") and isinstance(x.get("feats"), dict)]
    except (OSError, ValueError):
        return []


def teach(measures, path=None) -> int:
    """Frames whose FILTER header is right become references (per camera and rig). Returns how many were added."""
    import fits_core as core
    path = Path(path or learned_file())
    have = load_learned(path)
    seen = {(x.get("file"), x.get("filter")) for x in have}
    n = 0
    for m in measures:
        name = core.normalize_filter(m.header_filter) if m.header_filter else ""
        if not (m.ok and name) or (Path(m.path).name, name) in seen:
            continue
        have.append({"filter": name, "file": Path(m.path).name, "camera": m.camera, "rig": m.rig_key(),
                     "focus": m.focus, "feats": m.feats, "when": time.strftime("%Y-%m-%d")})
        n += 1
    if n:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(have, indent=1), encoding="utf-8")
    return n


def forget_learned(path=None):
    try:
        Path(path or learned_file()).unlink()
    except OSError:
        pass


# --------------------------------------------------------------------------- deciding
def _score(feats, ref) -> tuple[float, int]:
    s, n = 0.0, 0
    for k in FEATURES:
        if k in feats and ref.get(k):
            d = math.log(max(feats[k], 1e-6) / ref[k]) / SIGMA[k]
            s += d * d
            n += 1
    return s, n


def _candidates(m: Measure, learned) -> dict:
    """filter -> reference feature dict: the built-in ones, plus the learned ones (averaged per filter, those from
    this camera first)."""
    cands = {k: dict(v) for k, v in REFERENCES.items()}
    by = {}
    for x in learned:
        if x.get("camera") and m.camera and x["camera"] != m.camera and m.camera:
            continue
        by.setdefault(x["filter"], []).append(x["feats"])
    for name, fs in by.items():
        avg = {}
        for k in FEATURES:
            vals = [f[k] for f in fs if f.get(k)]
            if vals:
                avg[k] = float(math.exp(np.mean(np.log(vals))))
        if avg:
            cands[name] = {**cands.get(name, {}), **avg}
    return cands


def _focus_says(m: Measure, learned, best_group):
    """'No filter' or 'a filter' from where the focuser was - when this rig's own frames with and without a filter
    were learned (or it is the reference rig). None when it can't tell."""
    if m.focus is None or "No filter" not in best_group:
        return None
    nof, filt = [], []
    for x in learned:
        if x.get("rig") == m.rig_key() and x.get("focus") is not None:
            (nof if x["filter"] == "No filter" else filt).append(int(x["focus"]))
    if not nof and m.camera.upper().replace(" ", "").endswith("2600MCAIR") and m.focal and abs(m.focal - 265) < 3:
        nof, filt = list(REF_FOCUS["No filter"]), list(REF_FOCUS["filter"])
    if not nof or not filt:
        return None
    a, b = float(np.median(nof)), float(np.median(filt))
    if abs(a - b) < 150:
        return None                                  # this rig's filters barely move the focus
    return "No filter" if abs(m.focus - a) < abs(m.focus - b) else "a filter"


def _by_colour_alone(m: Measure):
    """R, G, B and single-line filters: one set of pixels lights up. None when it isn't one of those."""
    rg, bg = m.feats.get("sky_rg", 1), m.feats.get("sky_bg", 1)
    if rg > 3.0 and bg < 0.6:
        return Result("R (or a red line filter: Hα / SII)", "R", "medium",
                      f"almost only the red pixels see the sky (red ÷ green {rg:.1f}) - told by colour alone")
    if rg < 0.25 and bg < 0.25:
        return Result("G", "G", "medium", "almost only the green pixels see the sky - told by colour alone")
    if bg > 3.0 and rg < 0.6:
        return Result("B", "B", "medium", f"almost only the blue pixels see the sky (blue ÷ green {bg:.1f}) - "
                      "told by colour alone")
    if rg < 0.12 and 0.4 < bg < 1.5:
        return Result("OIII (single line)", "OIII", "medium", "green and blue pixels see the sky but red almost "
                      "nothing - an OIII line filter, told by colour alone")
    return None


def identify(m: Measure, learned=None) -> Result:
    """The filter this sub was most likely taken through, how sure, and why."""
    if not m.ok:
        return Result("—", "", "none", m.why or "couldn't be measured")
    learned = load_learned() if learned is None else learned
    alone = _by_colour_alone(m)
    if alone:
        return alone
    cands = _candidates(m, learned)
    scored = []
    for name, ref in cands.items():
        s, n = _score(m.feats, ref)
        if n >= 2:
            scored.append((name, s / n * 3, n))       # per feature, scaled to ~3 features
    if not scored:
        return Result("—", "", "none", "not enough to compare (too few stars and no sky colour)")
    scored.sort(key=lambda t: t[1])
    best, bs, _n = scored[0]
    group = next((g for g in GROUPS if best in g), (best,))
    focus = _focus_says(m, learned, group)
    chosen = best
    if "No filter" in group and focus:              # the focus position tells no filter from a clear filter
        pick = "No filter" if focus == "No filter" else None
        if pick is None:
            pick = next((n_ for n_, _s, _k in scored if n_ in group and n_ != "No filter"), best)
        chosen = pick
        bs = next(s_ for n_, s_, _k in scored if n_ == chosen)
        scored = [t for t in scored if t[0] == chosen or t[0] not in group]
        scored.sort(key=lambda t: (t[0] != chosen, t[1]))
    others = [(n_, s_) for n_, s_, _k in scored if n_ != chosen]
    close = [n_ for n_, s_ in others if s_ - bs < CLOSE]
    gap = (others[0][1] - bs) if others else 99.0
    name = chosen + (f" (could also be {', '.join(close[:3])})" if close else "")
    if bs > 40:
        sure = "low"
    elif bs < 12 and gap > 12:
        sure = "high"
    else:
        sure = "medium"
    f = m.feats
    bits = [f"sky red÷green {f['sky_rg']:.2f}, blue÷green {f['sky_bg']:.2f}"]
    if "star_rg" in f:
        bits.append(f"stars red÷green {f['star_rg']:.2f}, blue÷green {f['star_bg']:.2f} ({m.stars} stars)")
    else:
        bits.append(f"too few stars to use ({m.stars})")
    if "bright" in f:
        bits.append(f"sky {f['bright']:.2g} e⁻/s per arcsec²")
    if focus:
        bits.append(f"focus position {m.focus} says {focus.lower()}")
    why = "; ".join(bits)
    if bs > 40:
        why = "doesn't match any filter it knows well - " + why
    if not m.tested_sensor:
        why += f" - {m.camera or 'this camera'} isn't one of the tested sensors (IMX571 / IMX455): unverified"
        sure = {"high": "medium", "medium": "low"}.get(sure, sure)
    return Result(name, chosen, sure, why, [(n_, round(s_, 1)) for n_, s_, _ in scored[:5]])


SURE_TEXT = {"high": "Very likely", "medium": "Likely", "low": "Unsure", "none": "Can't tell"}


# --------------------------------------------------------------------------- built-in reference data
# measured by build_references() from the reference subs (3 per filter; NGC 281, 2026-11-16/17, ASI2600MC Air,
# Askar SQA55 at 265 mm, gain 100, 120 s, autofocus before each filter)
REF_FOCUS = {"No filter": (17462, 17513), "filter": (18094, 18103, 18119, 18135, 18139, 18143, 18145, 18154, 18158,
                                                     18173)}


def build_references(folders: dict) -> dict:
    """{filter: [paths]} -> {filter: features} (geometric mean over the subs). For the developer: the numbers
    below were made with it."""
    out = {}
    for name, paths in folders.items():
        ms = [measure(p) for p in paths]
        ms = [m for m in ms if m.ok]
        ref = {}
        for k in FEATURES:
            vals = [m.feats[k] for m in ms if m.feats.get(k)]
            if vals:
                ref[k] = round(float(math.exp(np.mean(np.log(vals)))), 4)
        out[name] = ref
    return out


REFERENCES.update({
 "No filter": {
  "sky_rg": 0.5012,
  "sky_bg": 0.7071,
  "star_rg": 0.7548,
  "star_bg": 0.5729,
  "bright": 0.4142
 },
 "L-CCD": {
  "sky_rg": 0.5053,
  "sky_bg": 0.6966,
  "star_rg": 0.746,
  "star_bg": 0.5364,
  "bright": 0.3504
 },
 "L-Pro": {
  "sky_rg": 0.3912,
  "sky_bg": 1.0614,
  "star_rg": 0.6843,
  "star_bg": 0.7358,
  "bright": 0.1948
 },
 "L-Quad Enhance": {
  "sky_rg": 0.2936,
  "sky_bg": 1.1503,
  "star_rg": 0.6795,
  "star_bg": 0.8325,
  "bright": 0.1894
 },
 "L-Para": {
  "sky_rg": 0.3428,
  "sky_bg": 0.5267,
  "star_rg": 0.7562,
  "star_bg": 0.5376,
  "bright": 0.0219
 },
 "IDAS NBZ II": {
  "sky_rg": 0.3839,
  "sky_bg": 0.5159,
  "star_rg": 0.8427,
  "star_bg": 0.5075,
  "bright": 0.0164
 },
 "Askar D1": {
  "sky_rg": 0.3738,
  "sky_bg": 0.5365,
  "star_rg": 0.7764,
  "star_bg": 0.5441,
  "bright": 0.013
 },
 "Askar D2": {
  "sky_rg": 0.2714,
  "sky_bg": 0.5467,
  "star_rg": 0.8369,
  "star_bg": 0.5618,
  "bright": 0.0102
 },
 "L-eXtreme": {
  "sky_rg": 0.4343,
  "sky_bg": 0.5256,
  "star_rg": 0.9133,
  "star_bg": 0.5223,
  "bright": 0.0123
 },
 "L-Ultimate": {
  "sky_rg": 0.362,
  "sky_bg": 0.5272,
  "star_rg": 0.7083,
  "star_bg": 0.5319,
  "bright": 0.0056
 }
})
