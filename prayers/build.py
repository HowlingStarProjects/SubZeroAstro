"""prayers/build.py - make the published Weather Prayers list (run by .github/workflows/publish-prayers.yml).

prayers/prayers.json holds Brian's own list and where prayers are sent ("send"). If it names "sheet_csv" (the
"Approved" tab of Brian's Google Sheet, published to the web as CSV: the prayer, name and place of every response
whose Approved box is ticked), those prayers are added after the ones in prayers.json. A ticked box shows up within
the hour, an unticked one disappears again.

    python3 prayers/build.py OUT.json        exit 0: OUT.json written; exit 3: the sheet couldn't be read (keep
                                              the published list as it is)
"""
import csv
import io
import json
import re
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _norm(t: str) -> str:
    return re.sub(r"\s+", " ", t or "").strip().lower()


def sheet_rows(url: str) -> list[dict]:
    req = urllib.request.Request(url, headers={"User-Agent": "SubZeroAstro prayers"})
    with urllib.request.urlopen(req, timeout=30) as r:
        text = r.read().decode("utf-8-sig", "replace")
    if text.lstrip().startswith("<"):
        raise ValueError("the sheet link gave a web page, not a CSV - is the Approved tab published as CSV?")
    rows = list(csv.reader(io.StringIO(text)))
    if not rows:
        return []
    head = [h.strip().lower() for h in rows[0]]
    print("sheet columns:", head, "-", len(rows) - 1, "approved rows")
    extra = [h for h in head if h and h not in ("prayer", "name", "place")]
    if extra or any("@" in c for row in rows[1:] for c in row[1:]):
        raise ValueError(f"the Approved tab shows more than prayer, name and place ({extra or 'an email'}) - "
                         "fix its formula so nothing private is published")
    col = {k: head.index(k) if k in head else i for i, k in enumerate(("prayer", "name", "place"))}
    out = []
    for row in rows[1:]:
        get = lambda k: (row[col[k]].strip() if col[k] < len(row) else "")   # noqa: E731
        text = get("prayer")
        if len(text) < 5:
            continue
        by = ", ".join(x for x in (get("name")[:60], get("place")[:60]) if x)
        out.append({"text": text[:1600], "by": by})
    return out


def build() -> dict:
    data = json.loads((HERE / "prayers.json").read_text(encoding="utf-8"))
    out = {k: v for k, v in data.items() if k != "sheet_csv"}
    prayers = list(data.get("prayers") or [])
    if data.get("sheet_csv"):
        seen = {_norm(p.get("text", "")) for p in prayers}
        for p in sheet_rows(data["sheet_csv"]):
            if _norm(p["text"]) not in seen:
                seen.add(_norm(p["text"]))
                prayers.append(p)
    out["prayers"] = prayers
    return out


if __name__ == "__main__":
    try:
        result = build()
    except Exception as e:          # noqa: BLE001
        print(f"::warning::Weather Prayers: couldn't read the approved sheet ({e}) - the published list stays as it is")
        sys.exit(3)
    Path(sys.argv[1]).write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(len(result["prayers"]), "prayers")
