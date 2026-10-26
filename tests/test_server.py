"""Offline test suite for the NetEase Cloud Music MCP server.

Runs with the standard library only:  python -m unittest discover -s tests -v
No NetEase account and no network access are needed: the upstream API is mocked.
"""
import io
import importlib.util
import json
import os
import socket
import sys
import threading
import time
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER_PATH = os.path.join(HERE, "..", "server", "mcp-server", "server.py")

# Configure before importing so the module picks the values up at import time.
os.environ.setdefault("NETEASE_COOKIE", "MUSIC_U=test-token; __csrf=test-csrf")
os.environ.setdefault("MCP_PORT", "0")
os.environ.setdefault("LOG_LEVEL", "QUIET")

spec = importlib.util.spec_from_file_location("netease_server", SERVER_PATH)
srv = importlib.util.module_from_spec(spec)
spec.loader.exec_module(srv)


# --------------------------------------------------------------------------- #
# Mock NetEase upstream
# --------------------------------------------------------------------------- #
class _Upstream(BaseHTTPRequestHandler):
    calls = []

    def log_message(self, *args):  # silence
        pass

    def _reply(self, data, headers=None):
        body = json.dumps(data).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        self._handle("GET", None)

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        self._handle("POST", self.rfile.read(length).decode())

    def _handle(self, method, body):
        path = self.path.split("?", 1)[0]
        _Upstream.calls.append({"method": method, "path": path, "query": self.path, "body": body,
                                "cookie": self.headers.get("Cookie")})
        if path == "/weapi/cloudsearch/get/web":
            self._reply({"code": 200, "result": {"songs": [
                {"id": 1001, "name": "晴天", "ar": [{"name": "周杰伦"}], "al": {"name": "叶惠美"}, "dt": 269000},
                {"id": 1002, "name": "晴天 (Live)", "ar": [{"name": "周杰伦"}], "al": {"name": "Live"}, "dt": 300000},
            ]}})
        elif path == "/weapi/v3/song/detail":
            self._reply({"code": 200, "songs": [
                {"id": 1001, "name": "晴天", "ar": [{"name": "周杰伦"}], "al": {"name": "叶惠美", "publishTime": 1057363200000},
                 "dt": 269000, "publishTime": 1057363200000},
            ]})
        elif path == "/weapi/song/lyric" or path == "/api/song/lyric":
            self._reply({"code": 200, "lrc": {"lyric": "[00:00.00] 作词 : 周杰伦\n[00:01.00]故事的小黄花\n[00:05.00]从出生那年就飘着"},
                         "tlyric": {"lyric": ""}})
        elif path == "/weapi/w/nuser/account/get":
            self._reply({"code": 200, "account": {"id": 42}, "profile": {"userId": 42}})
        elif path == "/weapi/v1/play/record":
            self._reply({"code": 200, "weekData": [{"song": {"id": 1001, "name": "晴天", "ar": [{"name": "周杰伦"}]},
                                                     "playCount": 12}]})
        elif path == "/weapi/play-record/song/list":
            self._reply({"code": 200, "data": {"list": [
                {"playTime": 1720000000000, "data": {"id": 1001, "name": "晴天", "ar": [{"name": "周杰伦"}]}}]}})
        elif path == "/weapi/v3/discovery/recommend/songs":
            self._reply({"code": 200, "data": {"dailySongs": [
                {"id": 1001, "name": "晴天", "ar": [{"name": "周杰伦"}], "al": {"name": "叶惠美"}}],
                "recommendReasons": [{"songId": 1001, "reason": "因为你喜欢周杰伦"}]}})
        elif path == "/weapi/v1/radio/get":
            self._reply({"code": 200, "data": [
                {"id": 2001, "name": "FM歌", "artists": [{"name": "某某"}], "album": {"name": "FM专辑"}},
                {"id": 2001, "name": "FM歌", "artists": [{"name": "某某"}], "album": {"name": "FM专辑"}}]})
        elif path == "/weapi/v6/playlist/detail":
            self._reply({"code": 200, "playlist": {"id": 9, "name": "测试歌单", "trackCount": 2,
                                                    "creator": {"nickname": "tester"},
                                                    "tracks": [{"id": 1001, "name": "晴天", "ar": [{"name": "周杰伦"}], "dt": 269000},
                                                               {"id": 1002, "name": "七里香", "ar": [{"name": "周杰伦"}], "dt": 299000}]}})
        elif path == "/weapi/playlist/create":
            self._reply({"code": 200, "id": 777})
        elif path == "/weapi/playlist/desc/update":
            self._reply({"code": 200})
        elif path == "/weapi/playlist/manipulate/tracks":
            self._reply({"code": 200})
        elif path == "/weapi/artist/top/song":
            self._reply({"code": 200, "songs": [{"id": 1001, "name": "晴天", "ar": [{"name": "周杰伦"}]}]})
        elif path == "/api/user/playlist":
            self._reply({"code": 200, "playlist": [
                {"id": 9, "name": "测试歌单", "trackCount": 2, "creator": {"userId": 42}, "description": "desc"},
                {"id": 10, "name": "收藏的", "trackCount": 5, "creator": {"userId": 99}}]})
        elif path == "/api/song/like/get":
            self._reply({"code": 200, "ids": [1001, 1002]})
        elif path == "/api/song/like":
            self._reply({"code": 200, "playlistId": 7})
        elif path == "/weapi/song/like":
            self._reply({"code": 200, "playlistId": 7})
        else:
            self._reply({"code": 404, "msg": "no mock for " + path})


class MockUpstream:
    def __enter__(self):
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Upstream)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.base = "http://127.0.0.1:%d" % self.httpd.server_address[1]
        return self

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.httpd.server_close()


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #
class CryptoTest(unittest.TestCase):
    def test_sbox_is_a_permutation(self):
        self.assertEqual(len(set(srv.SBOX)), 256)
        self.assertEqual(srv.SBOX[0x53], 0xED)  # SubBytes(0x53) in FIPS-197

    def test_key_expansion_fips197(self):
        rks = srv._expand_key(bytes(range(16)))
        self.assertEqual(bytes(rks[1]).hex(), "d6aa74fdd2af72fadaa678f1d6ab76fe")
        self.assertEqual(bytes(rks[10]).hex(), "13111d7fe3944a17f307a78b4d2b30c5")  # w[43]

    def test_fips197_cbc_vector(self):
        ct = srv.aes_cbc_encrypt(bytes.fromhex("00112233445566778899aabbccddeeff"), bytes(range(16)), bytes(16))
        self.assertEqual(ct.hex()[:32], "69c4e0d86a7b0430d8cdb78070b4c55a")

    def test_weapi_payload_shape(self):
        enc = srv.weapi_encrypt({"csrf_token": ""}, secret="aaaaaaaaaaaaaaaa")
        self.assertEqual(set(enc), {"params", "encSecKey"})
        self.assertEqual(len(enc["encSecKey"]), 256)
        self.assertTrue(all(c in "0123456789abcdef" for c in enc["encSecKey"]))

    def test_cookie_merges_device_and_csrf(self):
        header = srv.build_cookie_header()
        self.assertIn("MUSIC_U=test-token", header)
        self.assertIn("__csrf=test-csrf", header)
        self.assertIn("_ntes_nnid=", header)


class ToolsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mock = MockUpstream().__enter__()
        cls._orig_base = srv.NETEASE_API_BASE
        srv.NETEASE_API_BASE = cls.mock.base

    @classmethod
    def tearDownClass(cls):
        srv.NETEASE_API_BASE = cls._orig_base
        cls.mock.__exit__()

    def setUp(self):
        _Upstream.calls.clear()

    def test_registry_has_18_tools_with_schemas(self):
        self.assertEqual(len(srv.TOOLS), 18)
        for tool in srv.TOOLS:
            self.assertIn(tool["name"], srv.TOOL_DISPATCH)
            self.assertEqual(tool["inputSchema"]["type"], "object")
            for req in tool["inputSchema"].get("required", []):
                self.assertIn(req, tool["inputSchema"]["properties"])
            self.assertIn("annotations", tool)

    def test_search_song(self):
        out = srv.search_song({"query": "晴天", "limit": 2})
        self.assertEqual(len(out["results"]), 2)
        self.assertIn("ID:1001", out["results"][0])
        self.assertIn("周杰伦", out["results"][0])

    def test_search_requires_query(self):
        with self.assertRaises(ValueError):
            srv.search_song({})

    def test_search_limit_clamped(self):
        srv.search_song({"query": "x", "limit": 500})
        sent = _Upstream.calls[-1]
        self.assertTrue(sent["path"].startswith("/weapi/cloudsearch"), sent)

    def test_lyrics_strips_timestamps(self):
        out = srv.get_song_lyrics({"song_id": 1001})
        self.assertNotIn("[00:", out["lyrics"])
        self.assertIn("故事的小黄花", out["lyrics"])
        self.assertNotIn("translation", out)

    def test_play_music_card(self):
        out = srv.play_music({"query": "晴天"})
        card = out["now_playing"]
        self.assertEqual(card["id"], 1001)
        self.assertTrue(card["link"].endswith("id=1001"))
        self.assertEqual(card["duration"], "4:29")

    def test_play_history_uses_uid(self):
        out = srv.get_play_history({"all_time": False, "limit": 5})
        self.assertEqual(out["range"], "this week")
        self.assertIn("plays: 12", out["history"][0])

    def test_recent_plays_timestamp(self):
        out = srv.get_recent_plays({"limit": 10})
        self.assertIn("UTC+8", out["recent_plays"][0])
        self.assertNotIn("?]", out["recent_plays"][0])

    def test_daily_recommend_reason(self):
        out = srv.daily_recommend({})
        self.assertIn("因为你喜欢周杰伦", out["recommendations"][0])

    def test_personal_fm_dedupes(self):
        out = srv.get_personal_fm({})
        self.assertEqual(len(out["personal_fm"]), 1)

    def test_list_playlists_owner(self):
        out = srv.list_my_playlists({})
        self.assertIn("mine", out["playlists"][0])
        self.assertIn("collected", out["playlists"][1])

    def test_playlist_songs(self):
        out = srv.get_playlist_songs({"playlist_id": 9})
        self.assertEqual(out["name"], "测试歌单")
        self.assertEqual(len(out["songs"]), 2)

    def test_create_playlist_with_description(self):
        out = srv.create_playlist({"name": "新歌单", "description": "你好"})
        self.assertEqual(out["playlist_id"], 777)
        self.assertIn("Description: set", out["status"])
        self.assertTrue(_Upstream.calls[0]["path"].startswith("/weapi/playlist/create"))
        self.assertIn("MUSIC_U=test-token", _Upstream.calls[0]["cookie"])

    def test_add_and_remove(self):
        self.assertIn("Added 2", srv.add_to_playlist({"playlist_id": 9, "song_ids": "1001, 1002"})["status"])
        self.assertIn("Removed 1", srv.remove_from_playlist({"playlist_id": 9, "song_ids": "1001"})["status"])

    def test_reorder_requires_full_set(self):
        out = srv.reorder_playlist_tracks({"playlist_id": 9, "song_ids": "1001"})
        self.assertIn("error", out)
        self.assertEqual(out["missing_from_request"], ["1002"])
        out = srv.reorder_playlist_tracks({"playlist_id": 9, "song_ids": "1002, 1001"})
        self.assertIn("Reordered 2", out["status"])

    def test_update_description(self):
        out = srv.update_playlist_description({"playlist_id": 9, "description": "新描述"})
        self.assertEqual(out["description"], "新描述")

    def test_song_details_year(self):
        out = srv.get_song_details({"song_ids": "1001"})
        self.assertEqual(out["songs"][0]["year"], "2003")

    def test_artist_hot_songs(self):
        out = srv.get_artist_hot_songs({"artist_id": 6452})
        self.assertEqual(out["artist"], "周杰伦")

    def test_liked_songs_resolves_names(self):
        out = srv.get_liked_songs({"limit": 10})
        self.assertEqual(out["count"], 2)
        self.assertIn("晴天", out["liked_songs"][0])

    def test_like_song(self):
        out = srv.like_song({"song_id": 1001, "like": True})
        self.assertIn("Liked song 1001", out["status"])

    def test_write_call_does_not_retry_transport_errors(self):
        orig = srv.netease_weapi
        calls = []

        def fake(path, payload=None):
            calls.append(path)
            return {"code": -1, "error": "timeout"}

        srv.netease_weapi = fake
        try:
            out = srv.write_call("/playlist/create", {"name": "x"})
        finally:
            srv.netease_weapi = orig
        self.assertEqual(calls, ["/playlist/create"])
        self.assertIn("unknown", out["hint"])

    def test_write_call_falls_back_on_403(self):
        orig_w, orig_r = srv.netease_weapi, srv.netease_request
        seen = []
        srv.netease_weapi = lambda path, payload=None: {"code": 403}
        srv.netease_request = lambda path, data=None, method="POST": seen.append(path) or {"code": 200}
        try:
            out = srv.write_call("/playlist/create", {"name": "x"})
        finally:
            srv.netease_weapi, srv.netease_request = orig_w, orig_r
        self.assertEqual(out["code"], 200)
        self.assertTrue(seen and seen[0].startswith("/api/playlist/create"))


class ProtocolTest(unittest.TestCase):
    def rpc(self, method, params=None, msg_id=1, notification=False):
        msg = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        if not notification:
            msg["id"] = msg_id
        resp, status, _ = srv.handle_request(msg)
        return resp, status

    def test_initialize(self):
        resp, status = self.rpc("initialize", {"protocolVersion": "2025-03-26", "capabilities": {},
                                               "clientInfo": {"name": "t", "version": "0"}})
        self.assertEqual(status, 200)
        self.assertEqual(resp["result"]["protocolVersion"], "2025-03-26")
        self.assertIn("tools", resp["result"]["capabilities"])
        self.assertEqual(resp["id"], 1)

    def test_initialize_unknown_version_falls_back(self):
        resp, _ = self.rpc("initialize", {"protocolVersion": "1999-01-01"})
        self.assertIn(resp["result"]["protocolVersion"], srv.SUPPORTED_PROTOCOL_VERSIONS)

    def test_notification_has_no_reply(self):
        resp, status = self.rpc("notifications/initialized", notification=True)
        self.assertIsNone(resp)
        self.assertEqual(status, 202)

    def test_tools_list(self):
        resp, _ = self.rpc("tools/list")
        self.assertEqual([t["name"] for t in resp["result"]["tools"]], [t["name"] for t in srv.TOOLS])

    def test_unknown_method(self):
        resp, _ = self.rpc("does/not/exist")
        self.assertEqual(resp["error"]["code"], -32601)

    def test_ping(self):
        resp, _ = self.rpc("ping")
        self.assertEqual(resp["result"], {})

    def test_tool_call_bad_args_is_error_result(self):
        resp, _ = self.rpc("tools/call", {"name": "search_song", "arguments": {}})
        self.assertTrue(resp["result"]["isError"])
        self.assertIn("query is required", resp["result"]["content"][0]["text"])

    def test_tool_call_unknown_tool(self):
        with self.assertRaises(srv.MCPError):
            srv.call_tool("nope", {})


class HTTPTransportTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), srv.MCPHandler)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def post(self, payload, headers=None, path="/mcp"):
        req = urllib.request.Request(
            "http://127.0.0.1:%d%s" % (self.port, path),
            data=json.dumps(payload).encode(),
            headers=dict({"Content-Type": "application/json", "Accept": "application/json, text/event-stream"},
                         **(headers or {})),
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read()
            return resp.status, dict(resp.headers), (json.loads(raw) if raw else None)

    def test_health(self):
        with urllib.request.urlopen("http://127.0.0.1:%d/health" % self.port, timeout=5) as resp:
            body = json.loads(resp.read())
        self.assertEqual(body["tools"], 18)
        self.assertEqual(body["status"], "ok")

    def test_full_handshake_with_session(self):
        status, headers, body = self.post({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                           "params": {"protocolVersion": "2025-06-18"}})
        self.assertEqual(status, 200)
        sid = headers.get("Mcp-Session-Id")
        self.assertTrue(sid)
        status, _, body = self.post({"jsonrpc": "2.0", "method": "notifications/initialized"}, {"Mcp-Session-Id": sid})
        self.assertEqual(status, 202)
        self.assertIsNone(body)
        status, _, body = self.post({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, {"Mcp-Session-Id": sid})
        self.assertEqual(len(body["result"]["tools"]), 18)

    def test_unknown_session_rejected(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.post({"jsonrpc": "2.0", "id": 3, "method": "tools/list"}, {"Mcp-Session-Id": "nope"})
        self.assertEqual(ctx.exception.code, 404)

    def test_dns_rebinding_origin_blocked(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.post({"jsonrpc": "2.0", "id": 4, "method": "ping"}, {"Origin": "http://evil.example"})
        self.assertEqual(ctx.exception.code, 403)

    def test_localhost_origin_allowed(self):
        status, _, body = self.post({"jsonrpc": "2.0", "id": 5, "method": "ping"}, {"Origin": "http://localhost:3000"})
        self.assertEqual(status, 200)
        self.assertEqual(body["result"], {})

    def test_batch_requests(self):
        _, _, body = self.post([{"jsonrpc": "2.0", "id": 6, "method": "ping"},
                                {"jsonrpc": "2.0", "id": 7, "method": "tools/list"}])
        self.assertEqual([r["id"] for r in body], [6, 7])

    def test_parse_error(self):
        req = urllib.request.Request("http://127.0.0.1:%d/mcp" % self.port, data=b"{not json",
                                     headers={"Content-Type": "application/json"}, method="POST")
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req, timeout=5)
        self.assertEqual(ctx.exception.code, 400)
        self.assertEqual(json.loads(ctx.exception.read())["error"]["code"], -32700)


if __name__ == "__main__":
    unittest.main(verbosity=2)
