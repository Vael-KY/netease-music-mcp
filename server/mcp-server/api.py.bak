#!/usr/bin/env python3
"""
Music API Server
Provides HTTP endpoints for the frontend player:
  /api/music/url     - Get playable URL for a song
  /api/music/lyric   - Get lyrics (original + translation)
  /api/music/search  - Search songs
  /api/music/proxy   - Proxy audio stream (bypass CORS)
  /api/playlists     - List playlists
  /api/playlists/:id/songs - List songs in a playlist
"""
import http.server
import json
import os
import sqlite3
import urllib.request
import urllib.parse
from http.server import HTTPServer

DB_PATH = os.environ.get("MUSIC_DB_PATH", os.path.join(os.path.dirname(__file__), "music.db"))
API_PORT = int(os.environ.get("API_PORT", "3457"))
NETEASE_PROXY = os.environ.get("NETEASE_PROXY", "http://127.0.0.1:3460")
AUTH_TOKEN = os.environ.get("AUTH_TOKEN", "changeme")

# Load NetEase cookie from file or env
COOKIE_PATH = os.environ.get("NETEASE_COOKIE_FILE", os.path.join(os.path.dirname(__file__), "netease_cookie.txt"))
NETEASE_COOKIE = ""
if os.path.exists(COOKIE_PATH):
    with open(COOKIE_PATH) as f:
        NETEASE_COOKIE = f.read().strip()
NETEASE_COOKIE = os.environ.get("NETEASE_COOKIE", NETEASE_COOKIE)

def _headers():
    return {
        'User-Agent': 'Mozilla/5.0',
        'Referer': 'https://music.163.com/',
        'Cookie': NETEASE_COOKIE
    }

class APIHandler(http.server.BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        self.send_response(200)
        self._cors()
        self.end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        params = dict(urllib.parse.parse_qsl(parsed.query))

        # Auth check (skip for proxy and health)
        if path not in ('/api/health', '/api/music/proxy'):
            token = self.headers.get('Authorization', '').replace('Bearer ', '')
            if token != AUTH_TOKEN:
                self._json({"error": "unauthorized"}, 401)
                return

        if path == '/api/health':
            self._json({"status": "ok"})

        elif path == '/api/music/url':
            song_id = params.get('id', '')
            if not song_id:
                self._json({"error": "missing id"}, 400)
                return
            try:
                url = f'{NETEASE_PROXY}/song_url?id={song_id}'
                req = urllib.request.Request(url)
                with urllib.request.urlopen(req, timeout=8) as r:
                    data = json.loads(r.read().decode())
                self._json(data)
            except Exception as e:
                self._json({"error": str(e)}, 500)

        elif path == '/api/music/lyric':
            song_id = params.get('id', '')
            if not song_id:
                self._json({"error": "missing id"}, 400)
                return
            try:
                url = f'https://music.163.com/api/song/lyric?id={song_id}&lv=1&kv=1&tv=-1'
                req = urllib.request.Request(url, headers=_headers())
                with urllib.request.urlopen(req, timeout=8) as r:
                    data = json.loads(r.read().decode())
                lrc = data.get('lrc', {}).get('lyric', '')
                tlrc = data.get('tlyric', {}).get('lyric', '')
                self._json({"lyric": lrc, "tlyric": tlrc})
            except Exception as e:
                self._json({"error": str(e)}, 500)

        elif path == '/api/music/search':
            q = params.get('q', '')
            if not q:
                self._json({"error": "missing q"}, 400)
                return
            try:
                url = 'https://music.163.com/api/search/get?s=' + urllib.parse.quote(q) + '&type=1&limit=5'
                req = urllib.request.Request(url, headers=_headers())
                with urllib.request.urlopen(req, timeout=8) as r:
                    data = json.loads(r.read().decode())
                songs = data.get('result', {}).get('songs', [])
                results = []
                for s in songs:
                    song_id = s.get('id')
                    detail_url = f'https://music.163.com/api/song/detail?ids=[{song_id}]'
                    detail_req = urllib.request.Request(detail_url, headers=_headers())
                    try:
                        with urllib.request.urlopen(detail_req, timeout=5) as dr:
                            dd = json.loads(dr.read().decode())
                        pic = dd['songs'][0]['album'].get('picUrl', '')
                    except:
                        pic = ''
                    play_url = ''
                    try:
                        pu = f'{NETEASE_PROXY}/song_url?id={song_id}'
                        with urllib.request.urlopen(pu, timeout=5) as pr:
                            play_url = json.loads(pr.read().decode()).get('url', '')
                    except:
                        pass
                    results.append({
                        'id': song_id,
                        'name': s.get('name', ''),
                        'artist': ', '.join([a.get('name', '') for a in s.get('artists', [])]),
                        'pic': pic,
                        'url': f'https://music.163.com/#/song?id={song_id}',
                        'play_url': play_url
                    })
                self._json(results)
            except Exception as e:
                self._json({"error": str(e)}, 500)

        elif path == '/api/music/proxy':
            target_url = params.get('url', '')
            if not target_url:
                self.send_error(400)
                return
            try:
                req = urllib.request.Request(target_url, headers={
                    'User-Agent': 'Mozilla/5.0',
                    'Referer': 'https://music.163.com/'
                })
                with urllib.request.urlopen(req, timeout=15) as r:
                    ct = r.headers.get('Content-Type', 'audio/mpeg')
                    self.send_response(200)
                    self._cors()
                    self.send_header('Content-Type', ct)
                    cl = r.headers.get('Content-Length')
                    if cl:
                        self.send_header('Content-Length', cl)
                    self.end_headers()
                    while True:
                        chunk = r.read(65536)
                        if not chunk:
                            break
                        self.wfile.write(chunk)
            except Exception as e:
                self.send_error(502, str(e))

        elif path == '/api/playlists':
            try:
                db = sqlite3.connect(DB_PATH)
                db.row_factory = sqlite3.Row
                rows = db.execute("""SELECT p.*, COALESCE(c.cnt,0) as song_count
                    FROM playlists p LEFT JOIN (SELECT playlist_id, COUNT(*) as cnt
                    FROM playlist_songs GROUP BY playlist_id) c ON p.id=c.playlist_id
                    ORDER BY p.id ASC""").fetchall()
                db.close()
                self._json([dict(r) for r in rows])
            except Exception as e:
                self._json({"error": str(e)}, 500)

        elif path.startswith('/api/playlists/') and path.endswith('/songs'):
            pl_id = path.split('/')[3]
            try:
                db = sqlite3.connect(DB_PATH)
                db.row_factory = sqlite3.Row
                rows = db.execute("SELECT * FROM playlist_songs WHERE playlist_id=? ORDER BY added_at ASC",
                                  (pl_id,)).fetchall()
                db.close()
                self._json([dict(r) for r in rows])
            except Exception as e:
                self._json({"error": str(e)}, 500)

        else:
            self.send_error(404)

    def _cors(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Headers', '*')
        self.send_header('Access-Control-Allow-Methods', '*')

    def _json(self, data, status=200):
        self.send_response(status)
        self._cors()
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode())

    def log_message(self, format, *args):
        pass

if __name__ == '__main__':
    print(f"🎵 Music API Server running on port {API_PORT}")
    server = HTTPServer(('0.0.0.0', API_PORT), APIHandler)
    server.serve_forever()
