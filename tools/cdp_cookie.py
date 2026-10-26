"""Read NetEase cookies out of Edge via the DevTools protocol (CDP).

Edge encrypts cookies with app-bound encryption (``v20``), which cannot be
unwrapped outside the browser. So instead of decrypting files we ask the browser
itself: launch Edge (closed profile required) with a debugging port, call
``Network.getCookies`` over CDP, keep the 163.com cookies, then shut the browser
down again.

Usage:
    python tools/cdp_cookie.py --check                 # report names only
    python tools/cdp_cookie.py --write-env .env        # write NETEASE_COOKIE=...
    python tools/cdp_cookie.py --profile <dir> --check # any Chromium profile

The browser is started minimized/background and terminated by this script; the
values are never printed.
"""
import argparse
import base64
import json
import os
import socket
import struct
import subprocess
import sys
import tempfile
import time
import urllib.request

EDGE = os.path.join(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"),
                    "Microsoft", "Edge", "Application", "msedge.exe")
CHROME = os.path.join(os.environ.get("PROGRAMFILES", r"C:\Program Files"),
                      "Google", "Chrome", "Application", "chrome.exe")
DEFAULT_EDGE_PROFILE = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "Edge", "User Data")
DEFAULT_CHROME_PROFILE = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Google", "Chrome", "User Data")


# --------------------------------------------------------------------------- #
# Minimal WebSocket client (enough for CDP: masked text frames both ways)
# --------------------------------------------------------------------------- #
class WebSocket:
    def __init__(self, url, timeout=20):
        assert url.startswith("ws://"), url
        rest = url[len("ws://"):]
        hostport, _, path = rest.partition("/")
        host, _, port = hostport.partition(":")
        self.sock = socket.create_connection((host, int(port or 80)), timeout=timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        req = (
            "GET /%s HTTP/1.1\r\nHost: %s\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
            "Sec-WebSocket-Key: %s\r\nSec-WebSocket-Version: 13\r\n\r\n" % (path, hostport, key)
        )
        self.sock.sendall(req.encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise OSError("handshake closed")
            buf += chunk
        if b"101" not in buf.split(b"\r\n", 1)[0]:
            raise OSError("websocket handshake failed: %r" % buf[:120])
        self._buf = buf.split(b"\r\n\r\n", 1)[1]

    def send(self, text):
        payload = text.encode("utf-8")
        header = bytearray([0x81])
        mask = os.urandom(4)
        n = len(payload)
        if n < 126:
            header.append(0x80 | n)
        elif n < 65536:
            header.append(0x80 | 126)
            header += struct.pack(">H", n)
        else:
            header.append(0x80 | 127)
            header += struct.pack(">Q", n)
        header += mask
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self.sock.sendall(bytes(header) + masked)

    def _recv_exact(self, n):
        while len(self._buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise OSError("socket closed")
            self._buf += chunk
        out, self._buf = self._buf[:n], self._buf[n:]
        return out

    def recv(self):
        out = []
        while True:
            b1, b2 = self._recv_exact(2)
            opcode = b1 & 0x0F
            masked = b2 & 0x80
            length = b2 & 0x7F
            if length == 126:
                length = struct.unpack(">H", self._recv_exact(2))[0]
            elif length == 127:
                length = struct.unpack(">Q", self._recv_exact(8))[0]
            mask = self._recv_exact(4) if masked else None
            data = self._recv_exact(length)
            if mask:
                data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
            if opcode == 0x8:  # close
                raise OSError("websocket closed by peer")
            if opcode == 0x9:  # ping -> pong
                self.sock.sendall(bytes([0x8A, 0x80]) + os.urandom(4))
                continue
            out.append(data)
            # Consolidate fragments / skip control frames.
            if opcode in (0x1, 0x2) and (b1 & 0x80):
                return b"".join(out).decode("utf-8")

    def close(self):
        try:
            self.sock.sendall(bytes([0x88, 0x80]) + os.urandom(4))
        except OSError:
            pass
        self.sock.close()


# --------------------------------------------------------------------------- #
# Browser plumbing
# --------------------------------------------------------------------------- #
def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_for_target(port, timeout=40):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen("http://127.0.0.1:%d/json/list" % port, timeout=1) as resp:
                targets = json.load(resp)
            for t in targets:
                if t.get("type") == "page" and t.get("webSocketDebuggerUrl"):
                    return t["webSocketDebuggerUrl"]
        except Exception:  # noqa: BLE001 - the browser is still booting
            pass
        time.sleep(0.25)
    raise SystemExit("Edge did not expose a CDP page target on port %d" % port)


def cdp_call(ws, msg_id, method, params=None):
    ws.send(json.dumps({"id": msg_id, "method": method, "params": params or {}}))
    while True:
        msg = json.loads(ws.recv())
        if msg.get("id") == msg_id:
            if "error" in msg:
                raise SystemExit("CDP %s failed: %s" % (method, msg["error"]))
            return msg.get("result", {})


def fetch_cookies(profile, browser_exe, url="https://music.163.com", keep_open=False):
    port = free_port()
    cmd = [
        browser_exe,
        "--user-data-dir=%s" % profile,
        "--remote-debugging-port=%d" % port,
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-extensions",
        "--window-position=-32000,-32000",
        "--window-size=800,600",
        "https://music.163.com/",
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        ws = WebSocket(wait_for_target(port))
        try:
            cdp_call(ws, 1, "Network.enable")
            result = cdp_call(ws, 2, "Network.getCookies", {"urls": [url]})
            return result.get("cookies", [])
        finally:
            ws.close()
    finally:
        if not keep_open:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()


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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--browser", default="edge", choices=["edge", "chrome"])
    ap.add_argument("--profile", default=None, help="Chromium user-data-dir (default: the browser's)")
    ap.add_argument("--check", action="store_true", help="print cookie names only")
    ap.add_argument("--write-env", metavar="PATH")
    args = ap.parse_args()

    exe = EDGE if args.browser == "edge" else CHROME
    profile = args.profile or (DEFAULT_EDGE_PROFILE if args.browser == "edge" else DEFAULT_CHROME_PROFILE)
    if not os.path.isfile(exe):
        raise SystemExit("browser not found: %s" % exe)
    if not os.path.isdir(profile):
        raise SystemExit("profile not found: %s" % profile)

    cookies = fetch_cookies(profile, exe)
    mine = [c for c in cookies if "163.com" in c.get("domain", "")]
    if not mine:
        print("no 163.com cookies in %s (not logged in, or Edge is still running and holding the profile)"
              % profile)
        return 1
    print("%d 163.com cookie(s):" % len(mine))
    for c in sorted(mine, key=lambda x: x["name"]):
        print("    %-16s %s%s" % (c["name"], c["domain"], " (httpOnly)" if c.get("httpOnly") else ""))
    names = {c["name"] for c in mine}
    if not args.write_env:
        return 0
    if "MUSIC_U" not in names:
        print("!! MUSIC_U missing -> this profile is not logged in to music.163.com")
        return 1
    cookie = "; ".join("%s=%s" % (c["name"], c["value"]) for c in mine)
    _write_env(args.write_env, cookie)
    print("-> wrote NETEASE_COOKIE (%d pairs, %d chars) to %s" % (len(mine), len(cookie), args.write_env))
    return 0


if __name__ == "__main__":
    sys.exit(main())
