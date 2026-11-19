"""
fits_tiles.py - FITS's own lossless "tile compression" (the format fpack, N.I.N.A., Siril and PixInsight use;
files often named .fits.fz), in NumPy only.

Reading: Rice (RICE_1), GZIP_1, GZIP_2 and uncompressed tiles, any tile shape, integer pictures and decimal
pictures (lossless GZIP, or the usual quantized ones with or without dither).

Writing (the Fitz'r Squeezer): Rice, for whole-number pictures (raw subs). Rice is decoded tile by tile, and
every tile is independent, so the work runs over all tiles at once (one NumPy step per pixel position in a
tile, not per pixel): the squeezer writes tiles of about 1,000 pixels to keep that quick.

The picture data inside such a file is a binary table (one row per tile) whose cells point into a "heap" of
compressed bytes; the original header keywords travel with it (SIMPLE, BITPIX, NAXIS … renamed ZSIMPLE,
ZBITPIX, ZNAXIS …), so the original file can be put back exactly.
"""

from __future__ import annotations

import re
import zlib

import numpy as np

BLOCK, CARD = 2880, 80

# Rice parameters per bytes per pixel: (bits for the block code, largest split, bits of a raw value)
_RICE = {1: (3, 6, 8), 2: (4, 14, 16), 4: (5, 25, 32)}
RICE_BLOCK = 32

# keywords renamed between an image header and the compressed table that carries it
TO_Z = {"SIMPLE": "ZSIMPLE", "XTENSION": "ZTENSION", "BITPIX": "ZBITPIX", "NAXIS": "ZNAXIS", "EXTEND": "ZEXTEND",
        "BLOCKED": "ZBLOCKED", "PCOUNT": "ZPCOUNT", "GCOUNT": "ZGCOUNT", "CHECKSUM": "ZHECKSUM",
        "DATASUM": "ZDATASUM"}
FROM_Z = {v: k for k, v in TO_Z.items()}
_ZNAXIS_N = re.compile(r"^ZNAXIS(\d+)$")
_NAXIS_N = re.compile(r"^NAXIS(\d+)$")
# the compressed table's own keywords: never part of the picture's header
_TABLE_KEYS = re.compile(r"^(XTENSION|BITPIX|NAXIS\d*|PCOUNT|GCOUNT|TFIELDS|THEAP|CHECKSUM|DATASUM|EXTNAME|EXTVER|"
                         r"T(TYPE|FORM|UNIT|NULL|SCAL|ZERO|DISP|DIM)\d+|ZIMAGE|ZTILE\d+|ZCMPTYPE|ZNAME\d+|ZVAL\d+|"
                         r"ZQUANTIZ|ZDITHER0|ZMASKCMP|ZBLANK|ZSCALE|ZZERO|SZAOSIZE|SZAQHASH)$")


def is_table_key(key: str) -> bool:
    return bool(_TABLE_KEYS.match(key))


def picture_key(key: str) -> str:
    """The keyword's name in the picture's own header ('ZBITPIX' -> 'BITPIX', 'ZNAXIS2' -> 'NAXIS2')."""
    if key in FROM_Z:
        return FROM_Z[key]
    m = _ZNAXIS_N.match(key)
    return f"NAXIS{m.group(1)}" if m else key


def table_key(key: str) -> str:
    if key in TO_Z:
        return TO_Z[key]
    m = _NAXIS_N.match(key)
    return f"ZNAXIS{m.group(1)}" if m else key


# --------------------------------------------------------------------------
# Rice coding (the same bit stream as CFITSIO's rice.c)
# --------------------------------------------------------------------------

def _bitlen(x: np.ndarray) -> np.ndarray:
    """Number of bits needed for each non-negative integer (0 -> 0)."""
    x = np.asarray(x, dtype=np.float64)
    e = np.frexp(x)[1]
    return np.where(x > 0, e, 0).astype(np.int64)


def rice_encode(tiles: np.ndarray, bytepix: int, block: int = RICE_BLOCK) -> list:
    """Rice-code each row of `tiles` (stored whole numbers, shape (ntiles, n)). Returns one bytes per tile."""
    fsbits, fsmax, bbits = _RICE[bytepix]
    a = np.asarray(tiles, dtype=np.int64)
    if a.ndim != 2:
        raise ValueError("tiles must be 2-D")
    T, n = a.shape
    out = [None] * T
    # about a million pixels at a time keeps the memory small
    step = max(1, (1 << 20) // max(n, 1))
    for t0 in range(0, T, step):
        part = _rice_encode_part(a[t0:t0 + step], fsbits, fsmax, bbits, block)
        out[t0:t0 + len(part)] = part
    return out


def _rice_encode_part(a, fsbits, fsmax, bbits, block):
    T, n = a.shape
    full = 1 << bbits
    half = full >> 1
    first = a[:, 0] & (full - 1)
    d = np.diff(a, axis=1, prepend=a[:, :1])                       # d[:, 0] = 0, as in rice.c
    d = ((d + half) & (full - 1)) - half                            # wrap to the sample size
    v = np.where(d < 0, -2 * d - 1, 2 * d).astype(np.int64)         # zig-zag: 0, -1, 1, -2 … -> 0, 1, 2, 3 …
    nb = (n + block - 1) // block
    pad = nb * block - n
    vp = np.pad(v, ((0, 0), (0, pad))).reshape(T, nb, block)
    inside = np.ones(nb * block, bool)
    if pad:
        inside[n:] = False
    inside = inside.reshape(nb, block)
    bsize = inside.sum(axis=1)                                       # pixels in each block
    psum = vp.sum(axis=2, dtype=np.float64)
    dpsum = (psum - (bsize // 2) - 1) / bsize
    dpsum = np.maximum(dpsum, 0.0)
    fs = _bitlen(np.floor(dpsum).astype(np.int64) >> 1)              # the split for each block
    high = fs >= fsmax
    low = (fs == 0) & (psum == 0)
    code = np.where(high, fsmax + 1, np.where(low, 0, fs + 1))
    fsx = fs[:, :, None]
    top = vp >> np.minimum(fsx, 62)
    nbits = np.where(high[:, :, None], bbits, np.where(low[:, :, None], 0, top + 1 + fsx))
    nbits = np.where(inside[None], nbits, 0)
    # fields in order: [first value] then per block [code, pixel 0 … pixel 31]
    lens = np.empty((T, nb, block + 1), np.int64)
    lens[:, :, 0] = fsbits
    lens[:, :, 1:] = nbits
    lens = np.concatenate([np.full((T, 1), bbits, np.int64), lens.reshape(T, -1)], axis=1)
    ends = np.cumsum(lens, axis=1)
    starts = ends - lens
    total = ends[:, -1]
    nbytes = (total + 7) // 8
    tstart = np.concatenate([[0], np.cumsum(nbytes)[:-1]]) * 8      # each tile starts on a byte
    # what each field writes: value, its length, where (the zeros of a Rice code come first and are not written)
    pv = np.where(high[:, :, None], vp, (np.int64(1) << fsx) | (vp & ((np.int64(1) << fsx) - 1)))
    pl = np.where(high[:, :, None], bbits, fsx + 1)
    pl = np.where(nbits > 0, pl, 0)
    vals = np.empty((T, nb, block + 1), np.int64)
    vals[:, :, 0] = code
    vals[:, :, 1:] = pv
    vlen = np.empty((T, nb, block + 1), np.int64)
    vlen[:, :, 0] = fsbits
    vlen[:, :, 1:] = pl
    vals = np.concatenate([first[:, None], vals.reshape(T, -1)], axis=1)
    vlen = np.concatenate([np.full((T, 1), bbits, np.int64), vlen.reshape(T, -1)], axis=1)
    pos = starts + (lens - vlen) + tstart[:, None]
    keep = vlen > 0
    vals, vlen, pos = vals[keep], vlen[keep], pos[keep]
    buf = _write_bits(vals, vlen, pos, int(nbytes.sum()))
    offs = np.concatenate([[0], np.cumsum(nbytes)])
    return [buf[offs[i]:offs[i + 1]] for i in range(T)]


def _write_bits(vals, vlen, pos, nbytes):
    """OR each value (vlen bits, at most 32) into a big-endian bit stream at its bit position. The positions
    go up and the fields never overlap, so each 64-bit word is the OR of a run of fields (one pass with
    reduceat); a field across a word boundary gives its last bits to the next word."""
    u = np.uint64
    nwords = (nbytes + 7) // 8 + 1
    words = np.zeros(nwords, u)
    if len(pos) == 0:
        return b"\0" * nbytes
    v = vals.astype(u) & ((u(1) << vlen.astype(u)) - u(1))
    wi = pos >> 6
    shl = 64 - (pos & 63) - vlen                       # < 0: the field runs into the next word
    over = shl < 0
    a = np.where(over, v >> np.where(over, -shl, 0).astype(u), v << np.where(over, 0, shl).astype(u))
    _or_into(words, wi, a)
    if over.any():
        b = v[over] << (64 + shl[over]).astype(u)
        _or_into(words, wi[over] + 1, b)
    return words.astype(">u8").tobytes()[:nbytes]


def _or_into(words, idx, vals):
    """words[idx] |= vals, for idx sorted (repeats OR-ed together)."""
    first = np.concatenate([[True], idx[1:] != idx[:-1]])
    starts = np.nonzero(first)[0]
    words[idx[starts]] |= np.bitwise_or.reduceat(vals, starts)


def rice_decode(bufs, n: int, bytepix: int, block: int = RICE_BLOCK) -> np.ndarray:
    """Decode Rice-coded tiles, each n pixels long. Returns stored whole numbers, shape (len(bufs), n), int64."""
    fsbits, fsmax, bbits = _RICE[bytepix]
    T = len(bufs)
    if T == 0:
        return np.zeros((0, n), np.int64)
    lens = np.fromiter((len(b) for b in bufs), np.int64, T)
    starts = np.concatenate([[0], np.cumsum(lens)[:-1]])
    raw = np.frombuffer(b"".join(bytes(b) for b in bufs) + b"\0" * 16, np.uint8)
    # a 64-bit big-endian word starting at every byte (a view: nothing is copied)
    w8 = np.ndarray(shape=(len(raw) - 7,), dtype=">u8", buffer=raw.data, offset=0, strides=(1,))
    limit = (starts + lens) * 8
    u = np.uint64
    c = (starts * 8).astype(u)
    one, b32, b1054 = u(1), u(32), np.int64(1054)
    first = ((w8[c >> u(3)].astype(u) << (c & u(7))) >> u(64 - bbits)).astype(np.int64)
    c += u(bbits)
    v = np.zeros((n, T), u)                                      # pixel position first: whole rows are written
    nb = (n + block - 1) // block
    for b in range(nb):
        hdr = (w8[c >> u(3)].astype(u) << (c & u(7))) >> u(64 - fsbits)
        c += u(fsbits)
        low = hdr == 0
        high = hdr == u(fsmax + 1)
        plain = not (low.any() or high.any())
        norm = ~(low | high)
        fs = np.where(norm, hdr - one, u(0))
        mask = (one << fs) - one
        sh_low = u(63) - fs
        bs = min(block, n - b * block)
        if low.all():
            continue
        for k in range(bs):
            w = w8[c >> u(3)].astype(u) << (c & u(7))
            top = w >> b32
            # leading zeros of the top 32 bits = the Rice code's high part (from the float's exponent: exact)
            zeros = (b1054 - (top.astype(np.float64).view(np.int64) >> 52)).astype(u)
            lowbits = (((w << (zeros + one)) >> one) >> sh_low) & mask
            zero_top = top == 0
            if zero_top.any():
                special = zero_top & norm
                if special.any():                                    # a run of zeros longer than 32 bits
                    idx = np.nonzero(special)[0]
                    z = _long_zero_runs(raw, c[idx].astype(np.int64), limit[idx])
                    zeros[idx] = z.astype(u)
                    lowbits[idx] = _read_after(raw, c[idx].astype(np.int64) + z + 1, fs[idx].astype(np.int64)).astype(u)
            vn = (zeros << fs) | lowbits
            adv = zeros + one + fs
            if not plain:
                vn = np.where(norm, vn, np.where(high, w >> u(64 - bbits), u(0)))
                adv = np.where(norm, adv, np.where(high, u(bbits), u(0)))
            v[b * block + k] = vn
            c += adv
    if (c.astype(np.int64) > limit).any():
        raise ValueError("a compressed tile ended too soon (the file is damaged)")
    v = v.T.astype(np.int64)
    d = np.where(v & 1, -(v >> 1) - 1, v >> 1)
    full = np.int64(1) << bbits
    pix = (first[:, None] + np.cumsum(d, axis=1)) & (full - 1)
    return np.where(pix >= (full >> 1), pix - full, pix)


def _bit(raw, p):
    return (int(raw[p >> 3]) >> (7 - (p & 7))) & 1


def _long_zero_runs(raw, c, limit):
    """Rice codes whose run of zeros is longer than 32 bits (a hot pixel in a quiet block): counted bit by bit."""
    out = np.zeros(len(c), np.int64)
    for i in range(len(c)):
        p = int(c[i])
        z = 0
        while _bit(raw, p + z) == 0:
            z += 1
            if p + z >= limit[i]:
                raise ValueError("a compressed tile is damaged (a code runs past its end)")
        out[i] = z
    return out


def _read_after(raw, p, fs):
    out = np.zeros(len(p), np.int64)
    for i in range(len(p)):
        f = int(fs[i])
        if f <= 0:
            continue
        q = int(p[i])
        x = int.from_bytes(bytes(raw[q >> 3:(q >> 3) + 8]), "big")
        out[i] = (x >> (64 - (q & 7) - f)) & ((1 << f) - 1)
    return out


# --------------------------------------------------------------------------
# Quantized decimal pictures (CFITSIO's dither table)
# --------------------------------------------------------------------------

_RANDOMS = None


def _randoms():
    global _RANDOMS
    if _RANDOMS is None:
        a, m, seed = 16807.0, 2147483647.0, 1.0
        r = np.empty(10000, np.float32)
        for i in range(10000):
            temp = a * seed
            seed = temp - m * int(temp / m)
            r[i] = np.float32(seed / m)
        _RANDOMS = r
    return _RANDOMS


def _dither_sequence(row: int, dither0: int, n: int) -> np.ndarray:
    """The dither values for the pixels of tile `row` (1-based), as CFITSIO makes them."""
    r = _randoms()
    iseed = (row - 1 + dither0 - 1) % 10000
    nxt = int(r[iseed] * 500)
    out = np.empty(n, np.float64)
    i = 0
    while i < n:
        k = min(n - i, 10000 - nxt)
        out[i:i + k] = r[nxt:nxt + k]
        i += k
        nxt += k
        if nxt == 10000:
            iseed = (iseed + 1) % 10000
            nxt = int(r[iseed] * 500)
    return out


# --------------------------------------------------------------------------
# The compressed table: where each tile is
# --------------------------------------------------------------------------

_TFORM = re.compile(r"^\s*(\d*)\s*([LXBIJKAEDCMPQ])\s*([LXBIJKAEDCMPQ])?\s*(?:\((\d+)\))?")
_WIDTH = {"L": 1, "B": 1, "I": 2, "J": 4, "K": 8, "A": 1, "E": 4, "D": 8, "C": 8, "M": 16, "P": 8, "Q": 16}
_DTYPE = {"B": ">u1", "I": ">i2", "J": ">i4", "K": ">i8", "E": ">f4", "D": ">f8"}
_BITPIX_DTYPE = {8: ">u1", 16: ">i2", 32: ">i4", 64: ">i8", -32: ">f4", -64: ">f8"}


class CompInfo:
    """Everything needed to decode a tile-compressed picture."""

    def __init__(self, look: dict, table_offset: int):
        self.cmp = str(look.get("ZCMPTYPE", "")).strip().upper()
        if self.cmp not in ("RICE_1", "RICE_ONE", "GZIP_1", "GZIP_2", "NOCOMPRESS"):
            raise ValueError(f"This compressed FITS file uses {self.cmp or 'an unknown method'}, which SubZeroAstro "
                             "can't read (it reads Rice and GZIP) - open it in PixInsight or Siril and save it "
                             "again")
        self.zbitpix = int(look["ZBITPIX"])
        nax = int(look["ZNAXIS"])
        self.zshape = tuple(int(look[f"ZNAXIS{i}"]) for i in range(1, nax + 1))   # FITS order (x first)
        tile = []
        for i in range(1, nax + 1):
            dflt = self.zshape[0] if i == 1 else 1
            tile.append(int(look.get(f"ZTILE{i}", dflt) or dflt))
        self.ztile = tuple(tile)
        self.grid = tuple((s + t - 1) // t for s, t in zip(self.zshape, self.ztile))
        params = {}
        for i in range(1, 20):
            k = look.get(f"ZNAME{i}")
            if k is None:
                break
            params[str(k).strip().upper()] = look.get(f"ZVAL{i}")
        self.block = int(params.get("BLOCKSIZE", RICE_BLOCK) or RICE_BLOCK)
        self.quantiz = str(look.get("ZQUANTIZ", "") or "").strip().upper()
        self.dither0 = int(look.get("ZDITHER0", 1) or 1)
        self.zscale_key = look.get("ZSCALE")
        self.zzero_key = look.get("ZZERO")
        self.zblank_key = look.get("ZBLANK")
        # the table
        self.rowbytes = int(look["NAXIS1"])
        self.nrows = int(look["NAXIS2"])
        self.table_offset = table_offset
        self.heap_offset = table_offset + int(look.get("THEAP", self.rowbytes * self.nrows) or 0)
        self.cols = {}
        off = 0
        for i in range(1, int(look.get("TFIELDS", 0) or 0) + 1):
            name = str(look.get(f"TTYPE{i}", "")).strip().upper()
            m = _TFORM.match(str(look.get(f"TFORM{i}", "")))
            if not m:
                raise ValueError("compressed FITS: a table column can't be read")
            rep = int(m.group(1) or 1)
            t = m.group(2)
            width = (rep + 7) // 8 if t == "X" else rep * _WIDTH[t]
            self.cols[name] = (off, t, m.group(3), rep)
            off += width
        # decimals stored as rounded whole numbers (a scale per tile) - or else their own bytes, losslessly
        self.quantized = self.zbitpix < 0 and self.quantiz != "NONE" and (
            "ZSCALE" in self.cols or self.zscale_key is not None)
        dflt_bp = 4 if self.quantized else abs(self.zbitpix) // 8
        self.bytepix = int(params.get("BYTEPIX", dflt_bp) or dflt_bp)
        if "COMPRESSED_DATA" not in self.cols:
            raise ValueError("compressed FITS without a COMPRESSED_DATA column")
        self.table = None

    @property
    def shape(self):
        return tuple(reversed(self.zshape))

    def load_table(self, f):
        if self.table is None:
            f.seek(self.table_offset)
            raw = f.read(self.rowbytes * self.nrows)
            if len(raw) < self.rowbytes * self.nrows:
                raise ValueError("File is truncated (compressed table incomplete)")
            self.table = np.frombuffer(raw, np.uint8).reshape(self.nrows, self.rowbytes)

    def _desc(self, name):
        off, t, _e, _r = self.cols[name]
        dt = ">i4" if t == "P" else ">i8"
        w = 4 if t == "P" else 8
        cell = np.ascontiguousarray(self.table[:, off:off + 2 * w])
        pair = cell.view(dt).reshape(self.nrows, 2).astype(np.int64)
        return pair[:, 0], pair[:, 1], self.cols[name][2]

    def _scalar(self, name):
        if name not in self.cols:
            return None
        off, t, _e, _r = self.cols[name]
        cell = np.ascontiguousarray(self.table[:, off:off + _WIDTH[t]])
        return cell.view(_DTYPE[t]).reshape(self.nrows).astype(np.float64)

    def tile_ranges(self, tiles):
        """{column: (lengths, offsets)} for the given tile numbers (0-based)."""
        out = {}
        for name in ("COMPRESSED_DATA", "GZIP_COMPRESSED_DATA", "UNCOMPRESSED_DATA"):
            if name in self.cols:
                n, o, el = self._desc(name)
                out[name] = (n[tiles], o[tiles], el)
        return out

    def tile_box(self, t: int):
        """Start and size of tile t, FITS order."""
        idx = []
        for g in self.grid:
            idx.append(t % g)
            t //= g
        st = [i * z for i, z in zip(idx, self.ztile)]
        sz = [min(z, s - a) for z, s, a in zip(self.ztile, self.zshape, st)]
        return st, sz


def _elem_bytes(el):
    return _WIDTH.get(el or "B", 1)


def decode_tiles(f, ci: CompInfo, tiles) -> dict:
    """Decode the given tiles. Returns {tile: (start, size, values)}: values are the stored numbers (whole
    numbers, or decimals for lossless decimal pictures) in the tile's own shape (numpy order); quantized decimal
    pictures come back as decimals already."""
    ci.load_table(f)
    tiles = np.asarray(sorted(set(int(t) for t in tiles)), np.int64)
    if len(tiles) == 0:
        return {}
    rng = ci.tile_ranges(tiles)
    lo, hi = None, None
    for name, (n, o, el) in rng.items():
        nb = n * _elem_bytes(el)
        has = nb > 0
        if has.any():
            a, b = int(o[has].min()), int((o[has] + nb[has]).max())
            lo = a if lo is None else min(lo, a)
            hi = b if hi is None else max(hi, b)
    heap = b""
    if lo is not None:
        f.seek(ci.heap_offset + lo)
        heap = f.read(hi - lo)
        if len(heap) < hi - lo:
            raise ValueError("File is truncated (compressed picture incomplete)")
    lo = lo or 0
    zscale = ci._scalar("ZSCALE")
    zzero = ci._scalar("ZZERO")
    zblank = ci._scalar("ZBLANK")
    out = {}
    rice_groups = {}
    boxes = {}
    for k, t in enumerate(tiles):
        st, sz = ci.tile_box(int(t))
        npix = int(np.prod(sz))
        boxes[int(t)] = (st, sz)
        n, o, _el = rng["COMPRESSED_DATA"]
        if n[k] > 0:
            buf = heap[o[k] - lo:o[k] - lo + n[k]]
            if ci.cmp in ("RICE_1", "RICE_ONE"):
                rice_groups.setdefault(npix, []).append((int(t), buf))
                continue
            vals = _plain_tile(buf, ci, npix)
        elif "GZIP_COMPRESSED_DATA" in rng and rng["GZIP_COMPRESSED_DATA"][0][k] > 0:
            gn, go, gel = rng["GZIP_COMPRESSED_DATA"]
            raw = zlib.decompress(heap[go[k] - lo:go[k] - lo + gn[k]], 47)
            vals = np.frombuffer(raw, _BITPIX_DTYPE[ci.zbitpix])[:npix]
            out[int(t)] = (st, sz, vals.astype(np.float64 if ci.zbitpix < 0 else np.int64), True)
            continue
        elif "UNCOMPRESSED_DATA" in rng and rng["UNCOMPRESSED_DATA"][0][k] > 0:
            un, uo, uel = rng["UNCOMPRESSED_DATA"]
            nbytes = un[k] * _elem_bytes(uel)
            vals = np.frombuffer(heap[uo[k] - lo:uo[k] - lo + nbytes], _DTYPE.get(uel or "B", ">u1"))[:npix]
            out[int(t)] = (st, sz, vals.astype(np.float64 if ci.zbitpix < 0 else np.int64), True)
            continue
        else:
            vals = np.zeros(npix, np.int64)
        out[int(t)] = (st, sz, vals, False)
    for npix, items in rice_groups.items():
        step = max(1, (4 << 20) // max(npix, 1))           # about 4 million pixels at a time: little memory
        for i0 in range(0, len(items), step):
            part = items[i0:i0 + step]
            dec = rice_decode([b for _t, b in part], npix, ci.bytepix, ci.block)
            for (t, _b), row in zip(part, dec):
                st, sz = boxes[t]
                out[t] = (st, sz, row, False)
    res = {}
    row_of = {int(t): k for k, t in enumerate(tiles)}
    for t, (st, sz, vals, direct) in out.items():
        if ci.quantized and not direct:
            vals = _dequantize(vals, ci, t, zscale, zzero, zblank, row_of[t])
        res[t] = (st, sz, np.asarray(vals).reshape(tuple(reversed(sz))))
    return res


def _plain_tile(buf, ci: CompInfo, npix):
    bp = ci.bytepix if ci.quantized or ci.zbitpix > 0 else abs(ci.zbitpix) // 8
    if ci.quantized or ci.zbitpix > 0:
        dt = {1: ">u1", 2: ">i2", 4: ">i4", 8: ">i8"}[bp]
    else:
        dt = _BITPIX_DTYPE[ci.zbitpix]
    if ci.cmp == "NOCOMPRESS":
        raw = bytes(buf)
    else:
        raw = zlib.decompress(bytes(buf), 47)
        if ci.cmp == "GZIP_2" and bp > 1:
            raw = np.frombuffer(raw, np.uint8)[:npix * bp].reshape(bp, npix).T.tobytes()
    vals = np.frombuffer(raw, dt)[:npix]
    return vals.astype(np.float64 if (dt.startswith(">f")) else np.int64)


def _dequantize(vals, ci: CompInfo, t, zscale, zzero, zblank, k):
    s = zscale[t] if zscale is not None else float(ci.zscale_key if ci.zscale_key is not None else 1.0)
    z = zzero[t] if zzero is not None else float(ci.zzero_key or 0.0)
    q = np.asarray(vals, np.float64)
    if ci.quantiz in ("SUBTRACTIVE_DITHER_1", "SUBTRACTIVE_DITHER_2"):
        r = _dither_sequence(t + 1, ci.dither0, len(q))
        out = (q - r + 0.5) * s + z
    else:
        out = q * s + z
    blank = zblank[t] if zblank is not None else ci.zblank_key
    if blank is not None:
        out[np.asarray(vals) == int(blank)] = np.nan
    if ci.quantiz == "SUBTRACTIVE_DITHER_2":
        out[np.asarray(vals) == -2147483646] = 0.0
    return out


def read_region(f, ci: CompInfo, r0: int = 0, n: int | None = None) -> np.ndarray:
    """The stored numbers of rows r0 … r0+n-1 (all planes), numpy order: (rows, x) or (planes, rows, x). For
    whole-number pictures they are whole numbers (int64); for decimal pictures, decimals."""
    shp = ci.zshape
    ny = shp[1] if len(shp) > 1 else 1
    n = ny - r0 if n is None else n
    if len(shp) == 1:
        tiles = range(int(np.prod(ci.grid)))
    else:
        g1 = ci.grid[0]
        tr0, tr1 = r0 // ci.ztile[1], (r0 + n - 1) // ci.ztile[1]
        rest = int(np.prod(ci.grid[2:])) if len(ci.grid) > 2 else 1
        per_plane = g1 * ci.grid[1]
        tiles = [p * per_plane + tr * g1 + tx for p in range(rest) for tr in range(tr0, tr1 + 1) for tx in range(g1)]
    dec = decode_tiles(f, ci, tiles)
    float_out = ci.zbitpix < 0
    if len(shp) == 1:
        out = np.zeros(shp[0], np.float64 if float_out else np.int64)
        for st, sz, vals in dec.values():
            out[st[0]:st[0] + sz[0]] = vals
        return out
    planes = int(np.prod(shp[2:])) if len(shp) > 2 else 1
    out = np.zeros((planes, n, shp[0]), np.float64 if float_out else np.int64)
    for st, sz, vals in dec.values():
        v = vals.reshape(-1, sz[1], sz[0]) if len(sz) > 2 else vals.reshape(1, sz[1], sz[0])
        nz = v.shape[0]
        p0 = st[2] if len(st) > 2 else 0
        y0, y1 = max(st[1], r0), min(st[1] + sz[1], r0 + n)
        if y1 <= y0:
            continue
        out[p0:p0 + nz, y0 - r0:y1 - r0, st[0]:st[0] + sz[0]] = v[:, y0 - st[1]:y1 - st[1], :]
    if len(shp) == 2:
        return out[0]
    return out.reshape(tuple(reversed(shp[2:])) + (n, shp[0]))


# --------------------------------------------------------------------------
# Writing (squeeze) and expanding (unsqueeze) a whole file, exactly
# --------------------------------------------------------------------------

def raw_cards(buf: bytes, start: int = 0):
    """The 80-character cards of the header at `start` (up to END, without it) and where the header ends.
    Raises ValueError when there is no END."""
    cards = []
    p = start
    while True:
        blk = buf[p:p + BLOCK]
        if len(blk) < BLOCK:
            raise ValueError("no END in the header")
        p += BLOCK
        for i in range(0, BLOCK, CARD):
            c = blk[i:i + CARD].decode("ascii", "replace")
            if c[:8] == "END     ":
                return cards, p, blk[i + CARD:]
            cards.append(c)


def _key(card: str) -> str:
    return card[:8].strip()


def _val(card: str):
    import fits_core as core
    return core.parse_card(card)[1]


def make_card(key, value, comment="") -> str:
    if isinstance(value, bool):
        v = f"{'T' if value else 'F':>20}"
    elif isinstance(value, (int, np.integer)):
        v = f"{int(value):>20}"
    else:
        s = str(value).replace("'", "''")
        v = f"'{s:<8}'"
    line = f"{key:<8}= {v}"
    if comment:
        line += f" / {comment}"
    return f"{line:<80}"[:80]


def tile_width(nx: int) -> int:
    """Tiles of about 1,000 pixels along a row (one row high)."""
    k = max(1, -(-nx // 1024))
    return -(-nx // k)


class NotSqueezable(ValueError):
    """The file is left as it is; the message says why in plain words."""


def compress_file(buf: bytes, extra_cards=()) -> bytes:
    """A whole FITS file (bytes) -> the same picture and header as a Rice tile-compressed FITS file (bytes).
    Only a single whole-number picture in the main header is taken; anything else raises NotSqueezable."""
    if not buf.startswith(b"SIMPLE  ="):
        raise NotSqueezable("not a FITS file")
    try:
        cards, data_at, rest = raw_cards(buf)
    except ValueError:
        raise NotSqueezable("its header can't be read")
    if rest.strip(b" "):
        raise NotSqueezable("its header has text after END")
    keys = [_key(c) for c in cards]
    for k in keys:
        if k and is_table_key(k) and not k.startswith(("NAXIS", "BITPIX", "PCOUNT", "GCOUNT", "CHECKSUM",
                                                        "DATASUM", "XTENSION")):
            raise NotSqueezable(f"its header has a {k} keyword, which compressed files use themselves")
        if k.startswith("Z") and (k in FROM_Z or _ZNAXIS_N.match(k)):
            raise NotSqueezable(f"its header has a {k} keyword, which compressed files use themselves")
    try:
        bitpix = int(_val(cards[1])) if keys[1] == "BITPIX" else None
        naxis = int(_val(cards[2])) if keys[2] == "NAXIS" else None
    except (TypeError, ValueError, IndexError):
        raise NotSqueezable("its header isn't standard")
    if keys[0] != "SIMPLE" or bitpix is None or naxis is None:
        raise NotSqueezable("its header isn't standard")
    if bitpix < 0:
        raise NotSqueezable("it is a decimal picture (a stack or a processed picture) - those hardly shrink")
    if bitpix not in (8, 16, 32):
        raise NotSqueezable(f"it has {bitpix}-bit pixels")
    if naxis not in (2, 3) or keys[3:3 + naxis] != [f"NAXIS{i}" for i in range(1, naxis + 1)]:
        raise NotSqueezable("it isn't a single picture")
    shape_f = [int(_val(cards[3 + i])) for i in range(naxis)]
    if any(s <= 0 for s in shape_f) or shape_f[0] < 2:
        raise NotSqueezable("it has no picture in it")
    for c in cards:
        k = _key(c)
        if k in ("PCOUNT", "GCOUNT"):
            v = _val(c)
            if (k == "PCOUNT" and v not in (0, None)) or (k == "GCOUNT" and v not in (1, None)):
                raise NotSqueezable("it isn't a single picture")
    bp = bitpix // 8
    count = int(np.prod(shape_f))
    nbytes = count * bp
    padded = -(-nbytes // BLOCK) * BLOCK
    if len(buf) != data_at + padded:
        raise NotSqueezable("it has more in it than one picture (or it is cut short)")
    if buf[data_at + nbytes:].strip(b"\0"):
        raise NotSqueezable("it has more in it than one picture")
    dt = {1: ">u1", 2: ">i2", 4: ">i4"}[bp]
    pix = np.frombuffer(buf, dt, count, data_at).astype(np.int64)
    if bp == 1:
        pix = np.where(pix >= 128, pix - 256, pix)         # Rice works on the bits; signed or not doesn't matter
    nx = shape_f[0]
    tw = tile_width(nx)
    rows = pix.reshape(-1, nx)                            # every row of every plane
    nfull = nx // tw
    heap_parts = [None] * (rows.shape[0] * (-(-nx // tw)))
    per_row = -(-nx // tw)
    if nfull:
        enc = rice_encode(rows[:, :nfull * tw].reshape(-1, tw), bp)
        for i, b in enumerate(enc):
            r, t = divmod(i, nfull)
            heap_parts[r * per_row + t] = b
    if nx % tw:
        enc = rice_encode(rows[:, nfull * tw:], bp)
        for r, b in enumerate(enc):
            heap_parts[r * per_row + nfull] = b
    lens = np.fromiter((len(b) for b in heap_parts), np.int64, len(heap_parts))
    offs = np.concatenate([[0], np.cumsum(lens)[:-1]])
    heap = b"".join(heap_parts)
    if len(heap) >= 2 ** 31:
        raise NotSqueezable("it is too big")
    ntiles = len(heap_parts)
    table = np.empty((ntiles, 2), ">i4")
    table[:, 0] = lens
    table[:, 1] = offs
    hdr = [make_card("XTENSION", "BINTABLE", "binary table extension"),
           make_card("BITPIX", 8, "8-bit bytes"),
           make_card("NAXIS", 2, "2-dimensional binary table"),
           make_card("NAXIS1", 8, "width of table in bytes"),
           make_card("NAXIS2", ntiles, "number of rows in table"),
           make_card("PCOUNT", len(heap), "size of special data area"),
           make_card("GCOUNT", 1, "one data group (required keyword)"),
           make_card("TFIELDS", 1, "number of fields in each row"),
           make_card("TTYPE1", "COMPRESSED_DATA", "label for field   1"),
           make_card("TFORM1", f"1PB({int(lens.max()) if ntiles else 0})", "data format of field: variable length array"),
           make_card("ZIMAGE", True, "extension contains compressed image"),
           make_card("ZTILE1", tw, "size of tiles to be compressed"),
           make_card("ZTILE2", 1, "size of tiles to be compressed")]
    if naxis == 3:
        hdr.append(make_card("ZTILE3", 1, "size of tiles to be compressed"))
    hdr += [make_card("ZCMPTYPE", "RICE_1", "compression algorithm"),
            make_card("ZNAME1", "BLOCKSIZE", "compression block size"),
            make_card("ZVAL1", RICE_BLOCK, "pixels per block"),
            make_card("ZNAME2", "BYTEPIX", "bytes per pixel (1, 2, 4, or 8)"),
            make_card("ZVAL2", bp, "bytes per pixel (1, 2, 4, or 8)")]
    hdr += list(extra_cards)
    for c in cards:
        k = _key(c)
        z = table_key(k) if k else k
        hdr.append((f"{z:<8}" + c[8:]) if z != k else c)
    out = [_header_bytes(["SIMPLE  =                    T / file does conform to FITS standard".ljust(80),
                          make_card("BITPIX", 16, "number of bits per data pixel"),
                          make_card("NAXIS", 0, "number of data axes"),
                          make_card("EXTEND", True, "FITS dataset may contain extensions")]),
           _header_bytes(hdr)]
    body = table.tobytes() + heap
    out.append(body + b"\0" * (-len(body) % BLOCK))
    return b"".join(out)


def _header_bytes(cards) -> bytes:
    text = "".join(cards) + "END".ljust(CARD)
    text += " " * (-len(text) % BLOCK)
    return text.encode("ascii")


def find_compressed(buf: bytes):
    """(primary cards, compressed header cards, offset of its table) of the first compressed picture in a FITS
    file held in memory, or None when the file has no compressed picture."""
    import fits_core as core
    try:
        prim, p, _r = raw_cards(buf)
    except ValueError:
        return None
    look = core.cards_to_dict([core.parse_card(c) for c in prim])
    p += -(-core._data_nbytes(look) // BLOCK) * BLOCK
    while p < len(buf):
        try:
            cards, q, _r = raw_cards(buf, p)
        except ValueError:
            return None
        lk = core.cards_to_dict([core.parse_card(c) for c in cards])
        if str(lk.get("XTENSION", "")).strip().upper() == "BINTABLE" and lk.get("ZIMAGE"):
            return prim, cards, q
        p = q + -(-core._data_nbytes(lk) // BLOCK) * BLOCK
    return None


def expand_file(buf: bytes) -> bytes:
    """A tile-compressed FITS file (bytes) -> a plain FITS file (bytes) with the same picture and header. A file
    made by compress_file comes back exactly as it was."""
    import io
    import fits_core as core
    found = find_compressed(buf)
    if found is None:
        raise ValueError("this file isn't compressed")
    _prim, cards, table_at = found
    look = core.cards_to_dict([core.parse_card(c) for c in cards])
    ci = CompInfo(look, table_at)
    vals = read_region(io.BytesIO(buf), ci)
    kept = []
    for c in cards:
        k = _key(c)
        if k and is_table_key(k):
            continue
        z = picture_key(k) if k else k
        kept.append((f"{z:<8}" + c[8:]) if z != k else c)
    # the picture's own keywords first, in the standard order (a file from compress_file has them so already)
    naxis = len(ci.zshape)
    front_keys = ["SIMPLE", "BITPIX", "NAXIS"] + [f"NAXIS{i}" for i in range(1, naxis + 1)]
    front = {}
    rest = []
    for c in kept:
        k = _key(c)
        if k in front_keys and k not in front:
            front[k] = c
        elif k == "XTENSION":
            continue
        else:
            rest.append(c)
    front.setdefault("SIMPLE", make_card("SIMPLE", True, "file does conform to FITS standard"))
    front.setdefault("BITPIX", make_card("BITPIX", ci.zbitpix))
    front.setdefault("NAXIS", make_card("NAXIS", naxis))
    for i in range(1, naxis + 1):
        front.setdefault(f"NAXIS{i}", make_card(f"NAXIS{i}", ci.zshape[i - 1]))
    head = [front[k] for k in front_keys] + rest
    dt = _BITPIX_DTYPE[ci.zbitpix]
    arr = np.asarray(vals)
    if ci.zbitpix == 8:
        arr = arr & 255
    data = arr.astype(dt).tobytes()
    return _header_bytes(head) + data + b"\0" * (-len(data) % BLOCK)
