"""prayers.py - Weather Prayers sent in by SubZeroAstro users.

Just for fun: Help > Weather Prayer shows a "prayer of the day" picked from a short list that Brian approves.
The same list feeds the box on subzeroastro.com, and both pick the same prayer on the same day.

- Sending: a user's prayer goes into Brian's Google Form (free; Google emails him each new one and keeps them in
  a sheet). Nothing a user sends shows anywhere until Brian approves it. The form's address and its question ids
  come with the approved list ("send"), so sending can be switched on or moved without a new version.
- Approved list: prayers/prayers.json in this repository, copied by the workflow publish-prayers.yml to the
  public downloads repo, where the app and the website read it (LIST_URL).
- Prayer of the day: the same one for everybody for two days (UTC), then the next one in the list.
"""
from __future__ import annotations

import datetime as _dt
import json
import time
import urllib.request

LIST_URL = "https://raw.githubusercontent.com/HowlingStarProjects/SubZeroAstro-Downloads/main/prayers.json"
DAYS_PER_PRAYER = 2
EPOCH = _dt.date(2026, 1, 1)           # the website uses the same start date, so both show the same prayer
MAX_LEN = 800                          # characters in a prayer
MIN_LEN = 15
WAIT_BETWEEN = 10 * 60                 # seconds between two prayers sent from one computer

BUILT_IN = {
    "text": "Dear God, I have been really good and my astro equipment is ready to go. Please bless me and "
            "all other astrophotographers with clear dark skies, good seeing and minimal moonlight for "
            "tonight.  If you could also throw in some anti-Murphy's Law pixie dust and maybe a cold beer, "
            "that would be great and would let me know you really care about me.  Amen.",
    "by": "",
}

FORM_PREFIX = "https://docs.google.com/forms/"
FIELDS = ("prayer", "name", "place", "ok_to_show", "sent_from")

submit_hook = None      # the self-test puts a fake here so nothing is really sent
fetch_hook = None


# ------------------------------------------------------------------ the approved list

def clean_list(data) -> list[dict]:
    """The usable prayers in a downloaded list (anything odd is skipped, never an error)."""
    items = data.get("prayers") if isinstance(data, dict) else data
    out = []
    for p in items or []:
        if isinstance(p, str):
            p = {"text": p}
        if not isinstance(p, dict):
            continue
        text = str(p.get("text") or "").strip()
        if text:
            out.append({"text": text[:MAX_LEN * 2], "by": str(p.get("by") or "").strip()[:80]})
    return out


def clean_send(data) -> dict | None:
    """Where prayers are sent: {"form": Google Form formResponse address, "fields": {name: "entry.N"}}."""
    send = data.get("send") if isinstance(data, dict) else None
    if not isinstance(send, dict):
        return None
    form = str(send.get("form") or "").strip()
    fields = send.get("fields") if isinstance(send.get("fields"), dict) else {}
    fields = {k: str(v) for k, v in fields.items() if k in FIELDS and str(v).startswith("entry.")}
    if not form.startswith(FORM_PREFIX) or not form.endswith("/formResponse") or "prayer" not in fields:
        return None
    return {"form": form, "fields": fields}


def clean_data(data) -> dict:
    return {"prayers": clean_list(data), "send": clean_send(data)}


def prayer_of_the_day(prayers: list[dict], day: _dt.date | None = None) -> dict:
    """Same prayer for DAYS_PER_PRAYER days, for everybody (UTC), then the next one in the list."""
    prayers = prayers or [BUILT_IN]
    day = day or _dt.datetime.now(_dt.timezone.utc).date()
    n = ((day - EPOCH).days // DAYS_PER_PRAYER) % len(prayers)
    return prayers[n]


def fetch_list(timeout=10) -> dict:
    """The approved list from the internet: {"prayers": [...], "send": {...} or None}."""
    if fetch_hook is not None:
        return clean_data(fetch_hook())
    from target_info import ssl_context
    req = urllib.request.Request(f"{LIST_URL}?t={int(time.time() // 3600)}",
                                 headers={"User-Agent": "SubZeroAstro", "Cache-Control": "no-cache"})
    with urllib.request.urlopen(req, timeout=timeout, context=ssl_context()) as r:
        return clean_data(json.loads(r.read().decode("utf-8")))


# ------------------------------------------------------------------ sending one in

def check_prayer(text: str) -> str:
    """'' when the prayer can be sent, otherwise what to fix, in plain words."""
    t = (text or "").strip()
    if len(t) < MIN_LEN:
        return "Please write a few more words - that's a short prayer!"
    if len(t) > MAX_LEN:
        return f"That's a long prayer - please keep it under {MAX_LEN} letters (it's {len(t)} now)."
    return ""


def submit(text: str, name: str = "", place: str = "", ok_to_show: bool = True,
           app_version: str = "", send: dict | None = None, timeout=20) -> tuple[bool, str]:
    """Send one prayer to Brian's form. Returns (sent, message for the user)."""
    problem = check_prayer(text)
    if problem:
        return False, problem
    values = {
        "prayer": text.strip(),
        "name": name.strip()[:60],
        "place": place.strip()[:60],
        "ok_to_show": "Yes" if ok_to_show else "No - just for Brian",
        "sent_from": f"SubZeroAstro {app_version}".strip(),
    }
    if submit_hook is not None:
        return submit_hook(values)
    if not send:
        return False, "Sending prayers isn't switched on yet - please try again in a few days."
    import urllib.parse
    from target_info import ssl_context
    body = urllib.parse.urlencode({send["fields"][k]: v for k, v in values.items() if k in send["fields"]})
    req = urllib.request.Request(send["form"], data=body.encode("utf-8"), method="POST",
                                 headers={"Content-Type": "application/x-www-form-urlencoded",
                                          "User-Agent": "Mozilla/5.0 (SubZeroAstro)"})
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ssl_context()) as r:
            ok = 200 <= r.status < 400
    except Exception as e:                       # noqa: BLE001 - no internet, form closed ...
        return False, f"Your prayer couldn't be sent - is this computer online? ({e})"
    if ok:
        return True, "Amen! Your prayer is on its way. If it's picked, it will show up here and on subzeroastro.com."
    return False, "Your prayer couldn't be sent - please try again later."
