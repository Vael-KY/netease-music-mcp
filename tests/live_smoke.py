"""Live smoke test against the real music.163.com API.

Only public, anonymous read calls are used, so no cookie is needed and nothing
on any account is modified. Run it after changing request paths or encryption:

    python tests/live_smoke.py            # HTTP + MCP round trip on a random port
    python tests/live_smoke.py --api-only # skip the local server round trip

Exit code 0 = every check passed.
"""
import importlib.util
import json
import os
import subprocess
import sys
import threading
import time
import urllib.request
import socket

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER_PATH = os.path.join(HERE, "..", "server", "mcp-server", "server.py")

os.environ.setdefault("NETEASE_COOKIE", "")
os.environ.setdefault("LOG_LEVEL", "WARNING")

spec = importlib.util.spec_from_file_location("netease_server", SERVER_PATH)
srv = importlib.util.module_from_spec(spec)
spec.loader.exec_module(srv)

results = []


def check(name, ok, detail=""):
    results.append(ok)
    print("[%s] %s %s" % ("PASS" if ok else "FAIL", name, detail))
    return ok


def api_checks():
    # 1. weapi transport + AES/RSA: several encrypted read endpoints must answer.
    r = srv.netease_weapi("/search/get/web", {"s": "周杰伦 晴天", "type": 1, "limit": 3, "offset": 0})
    songs = (r.get("result") or {}).get("songs") or []
    check("weapi search", r.get("code") == 200 and bool(songs), "code=%s songs=%d" % (r.get("code"), len(songs)))
    song_id = songs[0]["id"] if songs else None
    r = srv.netease_weapi("/v3/discovery/recommend/songs", {})
    check("weapi anon recommend answers (not a protocol error)", r.get("code") != 50000005, "code=%s" % r.get("code"))

    # 2. Tool layer: search + details + lyrics.
    out = srv.search_song({"query": "晴天", "limit": 3})
    check("tool search_song", bool(out.get("results")), out.get("results", [out])[0][:80] if out.get("results") else str(out)[:120])
    if song_id:
        d = srv.get_song_details({"song_ids": str(song_id)})
        check("tool get_song_details", d.get("count") == 1, json.dumps(d.get("songs", [{}])[0], ensure_ascii=False)[:120])
        ly = srv.get_song_lyrics({"song_id": song_id, "max_lines": 5})
        check("tool get_song_lyrics", bool(ly.get("lyrics")), (ly.get("lyrics") or "")[:40].replace("\n", " / "))
        card = srv.play_music({"query": "晴天"})
        check("tool play_music", bool(card.get("now_playing")), card.get("now_playing", {}).get("title", str(card)[:100]))

    # 3. Artist hot songs.
    a = srv.get_artist_hot_songs({"artist_id": 6452})
    check("tool get_artist_hot_songs", bool(a.get("hot_songs")), "%s x%d" % (a.get("artist"), len(a.get("hot_songs") or [])))

    # 4. Public playlist read (official 热歌榜).
    p = srv.get_playlist_songs({"playlist_id": 3778678})
    check("tool get_playlist_songs", bool(p.get("songs")), "%s x%d" % (p.get("name"), len(p.get("songs") or [])))

    # 5. Account tools must fail gracefully without a cookie, not crash.
    h = srv.get_play_history({})
    check("account tool without cookie -> clean error", "error" in h, str(h)[:100])


def http_checks():
    port = _free_port()
    env = dict(os.environ, MCP_PORT=str(port), MCP_HOST="127.0.0.1", NETEASE_COOKIE=os.environ.get("NETEASE_COOKIE", ""))
    proc = subprocess.Popen([sys.executable, SERVER_PATH], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        base = "http://127.0.0.1:%d" % port
        health = None
        for _ in range(50):
            try:
                with urllib.request.urlopen(base + "/health", timeout=1) as resp:
                    health = json.loads(resp.read())
                break
            except Exception:  # noqa: BLE001
                time.sleep(0.1)
        check("GET /health", bool(health) and health.get("tools") == 18, json.dumps(health, ensure_ascii=False))

        def rpc(payload, sid=None):
            headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
            if sid:
                headers["Mcp-Session-Id"] = sid
            req = urllib.request.Request(base + "/mcp", data=json.dumps(payload).encode(), headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.status, resp.headers.get("Mcp-Session-Id"), json.loads(resp.read() or b"null")

        _, sid, body = rpc({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                            "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                                       "clientInfo": {"name": "smoke", "version": "0"}}})
        check("MCP initialize", body["result"]["serverInfo"]["name"] == "netease-music-mcp", "session=%s" % (sid or "")[:8])
        rpc({"jsonrpc": "2.0", "method": "notifications/initialized"}, sid)
        _, _, body = rpc({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, sid)
        check("MCP tools/list", len(body["result"]["tools"]) == 18)
        _, _, body = rpc({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                          "params": {"name": "search_song", "arguments": {"query": "晴天", "limit": 2}}}, sid)
        text = body["result"]["content"][0]["text"]
        check("MCP tools/call search_song", "ID:" in text, text[:100])
    finally:
        proc.terminate()
        try:
            _, err = proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            _, err = proc.communicate()
    if any(not r for r in results):
        print("server stderr tail:", err.decode(errors="replace")[-800:])


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


if __name__ == "__main__":
    api_checks()
    if "--api-only" not in sys.argv:
        http_checks()
    print("\n%d/%d checks passed" % (sum(results), len(results)))
    sys.exit(0 if all(results) else 1)
