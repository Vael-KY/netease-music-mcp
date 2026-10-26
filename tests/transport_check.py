"""Round-trip checks for the HTTP transports (stdio + Streamable HTTP + legacy SSE).

Usage: python tests/transport_check.py
Starts real server processes on free local ports; no NetEase account needed.
"""
import http.client
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER = os.path.join(HERE, "..", "server", "mcp-server", "server.py")
PY = sys.executable
ok = []


def check(name, cond, detail=""):
    ok.append(bool(cond))
    print("[%s] %s %s" % ("PASS" if cond else "FAIL", name, detail))


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_health(base, proc, seconds=10):
    deadline = time.time() + seconds
    while time.time() < deadline:
        if proc.poll() is not None:
            return False
        try:
            with urllib.request.urlopen(base + "/health", timeout=1) as r:
                return json.loads(r.read()).get("status") == "ok"
        except Exception:  # noqa: BLE001
            time.sleep(0.1)
    return False


def stdio_check():
    msgs = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "search_song", "arguments": {}}},
    ]
    env = dict(os.environ, MCP_TRANSPORT="stdio", LOG_LEVEL="QUIET")
    proc = subprocess.run([PY, SERVER, "--transport", "stdio"], input="\n".join(json.dumps(m) for m in msgs) + "\n",
                          capture_output=True, text=True, env=env, timeout=60)
    lines = [json.loads(l) for l in proc.stdout.splitlines() if l.strip()]
    check("stdio: two replies (no reply to notification)", len(lines) == 2, "got %d" % len(lines))
    check("stdio: initialize", lines and lines[0]["result"]["serverInfo"]["name"] == "netease-music-mcp")
    check("stdio: tool argument error surfaced", len(lines) > 1 and lines[1]["result"]["isError"] is True,
          (lines[1]["result"]["content"][0]["text"] if len(lines) > 1 else "")[:60])


def streamable_http_check(port):
    base = "http://127.0.0.1:%d" % port

    def post(payload, sid=None, timeout=10):
        headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        if sid:
            headers["Mcp-Session-Id"] = sid
        # One fresh connection per request: urllib's pool can hand back a socket that
        # the server closed, which surfaces as a hang followed by a timeout.
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
        try:
            conn.request("POST", "/mcp", body=json.dumps(payload).encode(), headers=headers)
            resp = conn.getresponse()
            return resp.status, resp.headers, resp.read()
        finally:
            conn.close()

    status, headers, body = post({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    sid = headers.get("Mcp-Session-Id")
    check("streamable: initialize issues session", status == 200 and bool(sid), sid or "")
    status, _, body = post({"jsonrpc": "2.0", "method": "notifications/initialized"}, sid)
    check("streamable: notification -> 202 empty", status == 202 and body == b"", "status=%s" % status)

    # GET with Accept: text/event-stream opens the server->client stream.
    req = urllib.request.Request(base + "/mcp", headers={"Accept": "text/event-stream", "Mcp-Session-Id": sid})
    with urllib.request.urlopen(req, timeout=5) as r:
        head = [r.readline() for _ in range(4)]
        check("streamable: GET opens SSE stream", r.status == 200 and any(h.startswith(b"event:") for h in head),
              b"".join(head)[:60])

    # DELETE ends the session; urllib drops 204 bodies, so use http.client here.
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request("DELETE", "/mcp", headers={"Mcp-Session-Id": sid})
    resp = conn.getresponse()
    resp.read()
    conn.close()
    check("streamable: DELETE session -> 204", resp.status == 204, "status=%s" % resp.status)
    try:
        status, _, body = post({"jsonrpc": "2.0", "id": 9, "method": "tools/list"}, sid, timeout=3)
        check("streamable: deleted session rejected", status == 404 and b"error" in body, "status=%s" % status)
    except OSError as exc:
        check("streamable: deleted session rejected", False, type(exc).__name__)


def legacy_sse_check(port):
    base = "http://127.0.0.1:%d" % port
    req = urllib.request.Request(base + "/sse", headers={"Accept": "text/event-stream"})
    with urllib.request.urlopen(req, timeout=5) as stream:
        assert stream.readline().strip() == b"retry: 5000"
        stream.readline()
        assert stream.readline().strip() == b"event: endpoint"
        endpoint = stream.readline().decode().strip()[len("data: "):]
        sid = endpoint.rsplit("sessionId=", 1)[1]
        body = json.dumps({"jsonrpc": "2.0", "id": 7, "method": "tools/list"}).encode()
        msg = urllib.request.Request(base + "/message?sessionId=" + sid, data=body,
                                     headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(msg, timeout=5) as r:
            status = r.status
        # Skip keepalive comments/blank lines; wait for the reply event.
        event, payload = None, None
        for _ in range(10):
            line = stream.readline().decode("utf-8").strip()
            if line.startswith("event:"):
                event = line
            elif line.startswith("data:"):
                payload = line[len("data:"):].strip()
                break
        data = json.loads(payload) if payload else {}
        check("legacy sse: endpoint event + POST /message 202", status == 202 and bool(sid), "status=%s" % status)
        check("legacy sse: reply streamed to SSE client", event == "event: message" and len(data["result"]["tools"]) == 18,
              "%s %s" % (event, (payload or "")[:40]))


def main():
    stdio_check()
    # Each scenario gets its own fresh server: a session that still has an open SSE
    # stream legitimately holds the Streamable HTTP lock while it streams.
    for scenario in (streamable_http_check, legacy_sse_check):
        port = free_port()
        env = dict(os.environ, MCP_PORT=str(port), MCP_HOST="127.0.0.1", NETEASE_COOKIE="", LOG_LEVEL="QUIET")
        proc = subprocess.Popen([PY, SERVER], env=env)
        try:
            check("http server is up (%s)" % scenario.__name__, wait_health("http://127.0.0.1:%d" % port, proc))
            scenario(port)
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
    print("\n%d/%d checks passed" % (sum(ok), len(ok)))
    return 0 if all(ok) else 1


if __name__ == "__main__":
    sys.exit(main())
