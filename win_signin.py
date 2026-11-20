"""win_signin.py - signing in to another Windows computer's shared folder (a N.I.N.A. computer) so that the
automatic run, which has no one at the keyboard, can still get in.

Clean Up My N.I.N.A. Computers › Sign In… keeps the user name and password in Windows' own Credential Manager
(the safe place Windows keeps network passwords; never in SubZeroAstro's settings or logs): once as a Windows
credential for that computer - so File Explorer and every program use it too - and once for SubZeroAstro, so a
run that is turned away can sign in again by itself. Windows only; on a Mac, Finder's keychain does this."""
from __future__ import annotations

import re
import sys

APP_TARGET = "SubZeroAstro:N.I.N.A.:"          # + the computer's name or IP, in Credential Manager

# Windows' answers that mean "signed in wrongly / not signed in" (worth trying the saved sign-in)
SIGNIN_ERRORS = {5, 86, 1219, 1244, 1326, 1327, 1328, 1329, 1330, 1331, 1909, 2202}
UNREACHABLE = {53, 64, 67, 121, 1231, 1232, 1203, 1222, 59, 51}


def unc_parts(folder) -> tuple:
    """'\\\\host\\share\\x\\y' -> ('host', '\\\\host\\share'); not a network folder -> ('', '')."""
    m = re.match(r"^[\\/]{2}([^\\/]+)[\\/]+([^\\/]+)", str(folder or "").strip())
    if not m:
        return "", ""
    return m.group(1), f"\\\\{m.group(1)}\\{m.group(2)}"


def guess_user(folder) -> str:
    """'\\\\192.168.0.37\\Users\\PrimaLuceLab\\Documents\\…' -> 'PrimaLuceLab': a good first guess for the
    user name (the folder under Users is the account that owns it)."""
    m = re.search(r"[\\/]Users[\\/]([^\\/]+)", str(folder or ""), re.I)
    if m and m.group(1).lower() not in ("public", "default", "all users"):
        return m.group(1)
    return ""


def winerror(e) -> int:
    """Windows' error number of an OSError (or the number itself)."""
    if isinstance(e, int):
        return e
    return int(getattr(e, "winerror", 0) or 0)


def plain(e, folder="") -> str:
    """An OSError in plain words, with what to do - never Python's quoted path with doubled backslashes."""
    w = winerror(e)
    host = unc_parts(folder)[0] or "that computer"
    if w in (1326, 1327, 1328, 1329, 1330, 1331, 1909, 86, 1244, 2202):
        return (f"{host} turned down the sign-in (Windows: wrong or missing user name or password). In Clean Up "
                f"My N.I.N.A. Computers, pick this computer and press Sign In… to save its user name and password "
                f"- the automatic run uses them from then on")
    if w == 1219:
        return (f"Windows is already signed in to {host} as a different user. Press Sign In… in Clean Up My "
                f"N.I.N.A. Computers to sign in again with the right one")
    if w == 5:
        return (f"{host} let SubZeroAstro in but not into this folder (no permission). On that computer, share "
                f"the folder with permission to change files, or Sign In… as its owner")
    if w in UNREACHABLE:
        return f"can't reach {host} - is it switched on and on the network?"
    if w in (2, 3):
        return "the folder isn't there - check the path (try opening it in File Explorer)"
    if isinstance(e, int):
        return f"Windows error {e}"
    return getattr(e, "strerror", None) or str(e)


# ---------------------------------------------------------------- Windows Credential Manager and connections
def _api():
    import ctypes
    from ctypes import wintypes as W

    class FILETIME(ctypes.Structure):
        _fields_ = [("lo", W.DWORD), ("hi", W.DWORD)]

    class CREDENTIAL(ctypes.Structure):
        _fields_ = [("Flags", W.DWORD), ("Type", W.DWORD), ("TargetName", W.LPWSTR), ("Comment", W.LPWSTR),
                    ("LastWritten", FILETIME), ("CredentialBlobSize", W.DWORD),
                    ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)), ("Persist", W.DWORD),
                    ("AttributeCount", W.DWORD), ("Attributes", ctypes.c_void_p), ("TargetAlias", W.LPWSTR),
                    ("UserName", W.LPWSTR)]

    class NETRESOURCE(ctypes.Structure):
        _fields_ = [("dwScope", W.DWORD), ("dwType", W.DWORD), ("dwDisplayType", W.DWORD), ("dwUsage", W.DWORD),
                    ("lpLocalName", W.LPWSTR), ("lpRemoteName", W.LPWSTR), ("lpComment", W.LPWSTR),
                    ("lpProvider", W.LPWSTR)]

    adv = ctypes.WinDLL("advapi32", use_last_error=True)
    mpr = ctypes.WinDLL("mpr", use_last_error=True)
    adv.CredWriteW.argtypes = [ctypes.POINTER(CREDENTIAL), W.DWORD]
    adv.CredReadW.argtypes = [W.LPCWSTR, W.DWORD, W.DWORD, ctypes.POINTER(ctypes.POINTER(CREDENTIAL))]
    adv.CredDeleteW.argtypes = [W.LPCWSTR, W.DWORD, W.DWORD]
    adv.CredFree.argtypes = [ctypes.c_void_p]
    mpr.WNetAddConnection2W.argtypes = [ctypes.POINTER(NETRESOURCE), W.LPCWSTR, W.LPCWSTR, W.DWORD]
    mpr.WNetCancelConnection2W.argtypes = [W.LPCWSTR, W.DWORD, W.BOOL]
    return ctypes, CREDENTIAL, NETRESOURCE, adv, mpr


CRED_TYPE_GENERIC, CRED_TYPE_DOMAIN_PASSWORD, CRED_PERSIST_LOCAL_MACHINE = 1, 2, 2


def _write(kind, target, user, password) -> bool:
    ctypes, CREDENTIAL, _N, adv, _m = _api()
    blob = (password or "").encode("utf-16-le")
    buf = (ctypes.c_ubyte * max(len(blob), 1)).from_buffer_copy(blob or b"\0")
    c = CREDENTIAL()
    c.Type, c.TargetName, c.UserName = kind, target, user
    c.CredentialBlobSize, c.CredentialBlob = len(blob), ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte))
    c.Persist = CRED_PERSIST_LOCAL_MACHINE
    return bool(adv.CredWriteW(ctypes.byref(c), 0))


def _read(host) -> tuple:
    """(user, password) SubZeroAstro saved for this computer, or ('', '')."""
    if sys.platform != "win32" or not host:
        return "", ""
    try:
        ctypes, CREDENTIAL, _N, adv, _m = _api()
        p = ctypes.POINTER(CREDENTIAL)()
        if not adv.CredReadW(APP_TARGET + host, CRED_TYPE_GENERIC, 0, ctypes.byref(p)):
            return "", ""
        try:
            c = p.contents
            pw = bytes(c.CredentialBlob[:c.CredentialBlobSize]).decode("utf-16-le") if c.CredentialBlobSize else ""
            return c.UserName or "", pw
        finally:
            adv.CredFree(p)
    except (OSError, AttributeError, ValueError):
        return "", ""


def has_saved(folder) -> bool:
    return bool(_read(unc_parts(folder)[0])[0])


def saved_user(folder) -> str:
    return _read(unc_parts(folder)[0])[0]


def _connect(share, user, password) -> int:
    """Sign in to \\\\host\\share; 0 = done, else Windows' error number. A different user already signed in to
    that computer (1219) is signed out first - Windows allows only one user per computer."""
    ctypes, _C, NETRESOURCE, _a, mpr = _api()
    host = unc_parts(share)[0]
    for attempt in range(2):
        nr = NETRESOURCE()
        nr.dwType, nr.lpRemoteName = 1, share                 # RESOURCETYPE_DISK
        rc = mpr.WNetAddConnection2W(ctypes.byref(nr), password, user or None, 0)
        if rc != 1219 or attempt:
            return rc
        for name in (share, f"\\\\{host}\\IPC$", f"\\\\{host}"):
            mpr.WNetCancelConnection2W(name, 0, True)
    return rc


def sign_in(folder, user, password) -> tuple:
    """Try the user name and password on the computer's shared folder; if they work, keep them in Credential
    Manager. (ok, message in plain words)."""
    if sys.platform != "win32":
        return False, ("On a Mac, connect once in Finder (Go › Connect to Server…) and tick 'Remember this "
                       "password in my keychain'.")
    host, share = unc_parts(folder)
    if not share:
        return False, ("This folder isn't a network folder (\\\\computer\\share\\…), so there is nothing to sign in "
                       "to.")
    user = (user or "").strip()
    try:
        rc = _connect(share, user, password)
    except OSError as e:
        return False, plain(e, folder)
    if rc != 0:
        if rc in (1326, 1327, 1328, 1329, 1330, 1331, 1909, 86):
            return False, (f"{host} said the user name or password is wrong. Use the account you sign in to "
                           f"that computer with (its user name, e.g. {guess_user(folder) or 'PrimaLuceLab'}, and "
                           f"its password). If that account has no password, Windows won't let it in over the "
                           f"network - give it a password on that computer first.")
        return False, plain(rc, folder)
    try:
        _write(CRED_TYPE_GENERIC, APP_TARGET + host, user, password)
        _write(CRED_TYPE_DOMAIN_PASSWORD, host, user, password)    # File Explorer and Windows use it too
    except OSError:
        pass
    return True, (f"Signed in to {host} as {user}. The user name and password are kept in Windows' Credential "
                  f"Manager, so the automatic run can sign in by itself.")


def forget(folder):
    host = unc_parts(folder)[0]
    if sys.platform != "win32" or not host:
        return
    try:
        _c, _C, _N, adv, _m = _api()
        adv.CredDeleteW(APP_TARGET + host, CRED_TYPE_GENERIC, 0)
        adv.CredDeleteW(host, CRED_TYPE_DOMAIN_PASSWORD, 0)
    except OSError:
        pass


def reconnect(folder) -> bool:
    """Turned away: sign in again with the saved user name and password (if any). True = signed in."""
    if sys.platform != "win32":
        return False
    host, share = unc_parts(folder)
    user, pw = _read(host)
    if not user:
        return False
    try:
        return _connect(share, user, pw) == 0
    except OSError:
        return False
