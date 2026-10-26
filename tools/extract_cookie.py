"""Extract NetEase Cloud Music cookies from a local Chromium browser (Edge/Chrome).

Runs entirely on this machine: the browser's DPAPI-protected key is unwrapped with
the current user's Windows credentials, v10/v11 cookies are AES-256-GCM decrypted
(in-process), and only the NetEase (163.com) cookies are kept.

Privacy: values are never printed. Use --write-env to append NETEASE_COOKIE=...
to the project .env, or --check to only report which cookie names exist.

Usage:
    python tools/extract_cookie.py --check
    python tools/extract_cookie.py --write-env .env
"""
import argparse
import base64
import ctypes
import json
import os
import shutil
import sqlite3
import sys
import tempfile

BROWSERS = {
    "edge": os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "Edge", "User Data"),
    "chrome": os.path.join(os.environ.get("LOCALAPPDATA", ""), "Google", "Chrome", "User Data"),
    "brave": os.path.join(os.environ.get("LOCALAPPDATA", ""), "BraveSoftware", "Brave-Browser", "User Data"),
}
WANTED = ("MUSIC_U", "__csrf", "MUSIC_A", "NMTID")


# --------------------------------------------------------------------------- #
# DPAPI (no third-party packages: ctypes against Crypt32)
# --------------------------------------------------------------------------- #
class DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", ctypes.c_uint32), ("pbData", ctypes.POINTER(ctypes.c_char))]


def dpapi_unprotect(blob: bytes) -> bytes:
    """CryptUnprotectData with the current user's logon credentials."""
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    buf = ctypes.create_string_buffer(blob, len(blob))
    data_in = DATA_BLOB(len(blob), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    data_out = DATA_BLOB()
    ok = crypt32.CryptUnprotectData(ctypes.byref(data_in), None, None, None, None, 0, ctypes.byref(data_out))
    if not ok:
        raise OSError("CryptUnprotectData failed (error %d)" % kernel32.GetLastError())
    try:
        return ctypes.string_at(data_out.pbData, data_out.cbData)
    finally:
        kernel32.LocalFree(data_out.pbData)


def master_key(profile_dir: str) -> bytes:
    local_state = os.path.join(os.path.dirname(profile_dir), "Local State")
    with open(local_state, encoding="utf-8") as fh:
        state = json.load(fh)
    os_crypt = state.get("os_crypt") or {}
    if not os_crypt.get("encrypted_key"):
        raise SystemExit("No os_crypt.encrypted_key in %s (browser may be unmanaged/portable)" % local_state)
    raw = base64.b64decode(os_crypt["encrypted_key"])
    if raw[:5] != b"DPAPI":
        raise SystemExit("Unexpected key prefix %r: this build uses app-bound encryption, not DPAPI" % raw[:5])
    key = dpapi_unprotect(raw[5:])
    if len(key) != 32:
        raise SystemExit("Unexpected master key length %d" % len(key))
    return key


# --------------------------------------------------------------------------- #
# AES-256-GCM for Chromium v10/v11 cookies, via Node's built-in crypto
# --------------------------------------------------------------------------- #
def decrypt_cookies_with_node(items):
    """items: list of (host, name, encrypted_hex, expires). Returns {name: value}.

    Chromium layout: 'v10'|'v11' + 12-byte nonce + ciphertext + 16-byte tag,
    keyed with AES-256-GCM using the DPAPI-unwrapped master key (empty AAD).
    """
    script = r"""
const crypto = require('crypto');
let raw = '';
process.stdin.on('data', d => raw += d);
process.stdin.on('end', () => {
  const {key, items} = JSON.parse(raw);
  const keyBuf = Buffer.from(key, 'base64');
  const out = {};
  for (const it of items) {
    const buf = Buffer.from(it.enc, 'hex');
    if (buf.slice(0, 3).toString() !== 'v10' && buf.slice(0, 3).toString() !== 'v11') {
      out[it.name] = null;
      continue;
    }
    try {
      const nonce = buf.slice(3, 15);
      const tag = buf.slice(buf.length - 16);
      const ct = buf.slice(15, buf.length - 16);
      const d = crypto.createDecipheriv('aes-256-gcm', keyBuf, nonce);
      d.setAuthTag(tag);
      out[it.name] = Buffer.concat([d.update(ct), d.final()]).toString('utf8');
    } catch (e) {
      out[it.name] = null;
    }
  }
  process.stdout.write(JSON.stringify(out));
});
"""
    payload = json.dumps({"key": items["key"], "items": items["rows"]})
    node = shutil.which("node") or os.path.join(
        os.environ.get("LOCALAPPDATA", ""), "Programs", "nodejs", "node.exe")
    if not os.path.isfile(node) and not shutil.which("node"):
        raise SystemExit("node not found; cannot decrypt v10 cookies")
    import subprocess

    proc = subprocess.run([node, "-e", script], input=payload, capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        raise SystemExit("node failed: %s" % proc.stderr[-300:])
    return json.loads(proc.stdout)


def _read_shared(path):
    """Read a file the browser holds open (exclusive on Windows) using
    CreateFileW with FILE_SHARE_READ|WRITE|DELETE, then plain ReadFile."""
    GENERIC_READ = 0x80000000
    FILE_SHARE_ALL = 0x1 | 0x2 | 0x4
    OPEN_EXISTING = 3
    kernel32 = ctypes.windll.kernel32
    kernel32.CreateFileW.restype = ctypes.c_void_p
    handle = kernel32.CreateFileW(
        ctypes.c_wchar_p(path), GENERIC_READ, FILE_SHARE_ALL, None, OPEN_EXISTING, 0, None
    )
    if handle in (None, ctypes.c_void_p(-1).value, 0xFFFFFFFFFFFFFFFF):
        raise OSError("CreateFileW failed for %s (error %d)" % (path, kernel32.GetLastError()))
    try:
        chunks, size = [], 1 << 20
        buf = ctypes.create_string_buffer(size)
        read = ctypes.c_uint32(0)
        while True:
            ok = kernel32.ReadFile(handle, buf, size, ctypes.byref(read), None)
            if not ok:
                raise OSError("ReadFile failed (error %d)" % kernel32.GetLastError())
            if read.value == 0:
                break
            chunks.append(buf.raw[: read.value])
        return b"".join(chunks)
    finally:
        kernel32.CloseHandle(ctypes.c_void_p(handle))


def read_cookies(profile_dir, key, need_decrypt=True):
    src = os.path.join(profile_dir, "Network", "Cookies")
    if not os.path.isfile(src):
        src = os.path.join(profile_dir, "Cookies")
    if not os.path.isfile(src):
        return []
    # Read into a private copy: the browser keeps an exclusive lock on the live
    # database, and a WAL sidecar may hold the freshest rows.
    tmpdir = tempfile.mkdtemp(prefix="cookiecopy_")
    tmp = os.path.join(tmpdir, "Cookies")
    try:
        with open(tmp, "wb") as fh:
            fh.write(_read_shared(src))
        for suffix in ("-wal", "-shm"):
            side = src + suffix
            if os.path.isfile(side):
                try:
                    with open(tmp + suffix, "wb") as fh:
                        fh.write(_read_shared(side))
                except OSError:
                    pass
        con = sqlite3.connect(tmp)
        rows = con.execute(
            "SELECT host_key, name, value, encrypted_value, expires_utc FROM cookies "
            "WHERE host_key LIKE '%163.com%' ORDER BY name"
        ).fetchall()
        con.close()
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    plain, encrypted = {}, []
    for host, name, value, enc, expires in rows:
        if value:
            plain[name] = value
        elif enc:
            encrypted.append({"name": name, "enc": enc.hex(), "host": host, "expires": expires})
    decrypted = {}
    if encrypted and need_decrypt:
        result = decrypt_cookies_with_node({"key": base64.b64encode(key).decode(), "rows": encrypted})
        decrypted = {k: v for k, v in result.items() if v}
    merged = dict(plain)
    merged.update(decrypted)
    return [{"host": h, "name": n, "value": merged.get(n), "expires": e} for h, n, v, enc, e in rows]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--browser", default="auto", choices=["auto"] + list(BROWSERS))
    ap.add_argument("--check", action="store_true", help="report which cookies exist, print no values")
    ap.add_argument("--write-env", metavar="PATH", help="write NETEASE_COOKIE=... into this .env")
    args = ap.parse_args()

    candidates = [args.browser] if args.browser != "auto" else list(BROWSERS)
    for name in candidates:
        root = BROWSERS[name]
        if not os.path.isdir(root):
            continue
        # Every profile ("Default", "Profile 1", ...) can hold a login.
        profiles = sorted(
            d for d in os.listdir(root)
            if (d == "Default" or d.startswith("Profile ")) and os.path.isdir(os.path.join(root, d))
        )
        for profile_name in profiles:
            profile = os.path.join(root, profile_name)
            try:
                key = master_key(profile)
                rows = read_cookies(profile, key)
            except SystemExit as exc:
                print("[%s/%s] %s" % (name, profile_name, exc))
                continue
            except Exception as exc:  # noqa: BLE001
                print("[%s/%s] failed: %s: %s" % (name, profile_name, type(exc).__name__, exc))
                continue
            if not rows:
                print("[%s/%s] no 163.com cookies" % (name, profile_name))
                continue
            print("[%s/%s] %d 163.com cookie(s)" % (name, profile_name, len(rows)))
            for r in sorted(rows, key=lambda x: x["name"]):
                print("    %-14s %-13s %s" % (r["name"], "ok" if r["value"] else "undecryptable", r["host"]))
            found = {r["name"]: r["value"] for r in rows}
            if not args.write_env:
                continue
            if not found.get("MUSIC_U"):
                print("    !! MUSIC_U missing/empty -> this profile is not logged in to music.163.com")
                continue
            pairs = ["%s=%s" % (r["name"], r["value"]) for r in rows if r["value"]]
            cookie = "; ".join(pairs)
            _write_env(args.write_env, cookie)
            print("    -> wrote NETEASE_COOKIE (%d pairs, %d chars) to %s" % (len(pairs), len(cookie), args.write_env))
            return 0
    return 1


def _write_env(path, cookie):
    with open(path, encoding="utf-8") as fh:
        lines = fh.readlines()
    out, done = [], False
    for line in lines:
        if line.strip().startswith("NETEASE_COOKIE="):
            out.append("NETEASE_COOKIE=%s\n" % cookie)
            done = True
        else:
            out.append(line)
    if not done:
        out.append("NETEASE_COOKIE=%s\n" % cookie)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.writelines(out)


if __name__ == "__main__":
    sys.exit(main())
