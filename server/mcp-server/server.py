#!/usr/bin/env python3
import http.server, json, os, urllib.request, urllib.parse, threading, uuid, time
from http.server import HTTPServer

NETEASE_COOKIE = os.environ.get("NETEASE_COOKIE", "")
PORT = int(os.environ.get("MCP_PORT", "3456"))
SESSION_ID = str(uuid.uuid4())

def netease_request(url, data=None):
    headers = {'User-Agent': 'Mozilla/5.0', 'Referer': 'https://music.163.com/', 'Cookie': NETEASE_COOKIE, 'Content-Type': 'application/x-www-form-urlencoded' if data else 'application/json'}
    if data and isinstance(data, dict):
        data = urllib.parse.urlencode(data).encode()
    elif data and isinstance(data, str):
        data = data.encode()
    req = urllib.request.Request(url, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        return {"code": -1, "error": str(e)}

def get_uid():
    resp = netease_request('https://music.163.com/api/nuser/account/get')
    try:
        return resp.get('profile', {}).get('userId') or resp.get('account', {}).get('id')
    except:
        return None

def get_csrf():
    for part in NETEASE_COOKIE.split(';'):
        part = part.strip()
        if part.startswith('__csrf='):
            return part.split('=', 1)[1]
    return ''

def play_music(query, note=None):
    url = 'https://music.163.com/api/search/get?s=' + urllib.parse.quote(query) + '&type=1&limit=5'
    resp = netease_request(url)
    songs = resp.get('result', {}).get('songs', [])
    if not songs:
        return "No results for '" + query + "'"
    s = songs[0]
    song_id = s.get('id')
    try:
        dd = netease_request('https://music.163.com/api/song/detail?ids=[' + str(song_id) + ']')
        pic_url = dd['songs'][0]['album'].get('picUrl', '')
    except:
        pic_url = ''
    name = s.get('name', '').replace(':', '\uff1a')
    artist = ', '.join([a.get('name', '') for a in s.get('artists', [])]).replace(':', '\uff1a')
    link = "https://music.163.com/song?id=" + str(song_id)
    return "[music:" + str(song_id) + ":" + name + ":" + artist + ":" + pic_url + "]" + (note or '') + "\n" + link

def create_playlist(name, description='', privacy=0):
    csrf = get_csrf()
    url = 'https://music.163.com/api/playlist/create?csrf_token=' + csrf
    data = {'name': name, 'privacy': str(privacy), 'type': 'NORMAL'}
    if description:
        data['description'] = description
    resp = netease_request(url, data=data)
    if resp.get('code') == 200:
        pl = resp.get('playlist', {})
        return "Created playlist '" + name + "' (ID: " + str(pl.get('id')) + ")"
    return "Failed: " + resp.get('message', resp.get('error', 'unknown'))

def add_to_playlist(playlist_id, song_ids):
    csrf = get_csrf()
    if isinstance(song_ids, str):
        ids = [s.strip() for s in song_ids.split(',')]
    else:
        ids = [str(song_ids)]
    url = 'https://music.163.com/api/playlist/manipulate/tracks?csrf_token=' + csrf
    data = {'op': 'add', 'pid': str(playlist_id), 'trackIds': json.dumps([int(i) for i in ids])}
    resp = netease_request(url, data=data)
    if resp.get('code') == 200:
        return "Added " + str(len(ids)) + " song(s) to playlist " + str(playlist_id)
    if resp.get('code') == 502:
        return "Song already in playlist"
    return "Failed: " + resp.get('message', resp.get('error', 'unknown'))

def remove_from_playlist(playlist_id, song_ids):
    csrf = get_csrf()
    if isinstance(song_ids, str):
        ids = [s.strip() for s in song_ids.split(',')]
    else:
        ids = [str(song_ids)]
    url = 'https://music.163.com/api/playlist/manipulate/tracks?csrf_token=' + csrf
    data = {'op': 'del', 'pid': str(playlist_id), 'trackIds': json.dumps([int(i) for i in ids])}
    resp = netease_request(url, data=data)
    if resp.get('code') == 200:
        return "Removed " + str(len(ids)) + " song(s) from playlist " + str(playlist_id)
    return "Failed: " + resp.get('message', resp.get('error', 'unknown'))

def list_my_playlists():
    uid = get_uid()
    if not uid:
        return "Failed to get user ID. Cookie may be expired."
    url = 'https://music.163.com/api/user/playlist?uid=' + str(uid) + '&limit=50&offset=0'
    resp = netease_request(url)
    playlists = resp.get('playlist', [])
    if not playlists:
        return "No playlists found"
    lines = []
    for pl in playlists:
        own = '(mine)' if pl.get('creator', {}).get('userId') == uid else '(collected)'
        lines.append("ID:" + str(pl['id']) + " | " + pl['name'] + " | " + str(pl.get('trackCount', 0)) + " songs " + own)
    return "\n".join(lines)

def get_playlist_songs(playlist_id):
    url = 'https://music.163.com/api/v6/playlist/detail?id=' + str(playlist_id)
    resp = netease_request(url)
    playlist = resp.get('playlist', {})
    tracks = playlist.get('tracks', [])
    if not tracks:
        track_ids = playlist.get('trackIds', [])
        if track_ids:
            ids = [t['id'] for t in track_ids[:50]]
            detail = netease_request('https://music.163.com/api/song/detail?ids=' + json.dumps(ids))
            tracks = detail.get('songs', [])
    if not tracks:
        return "Playlist " + str(playlist_id) + " is empty"
    lines = ["Playlist: " + playlist.get('name', '') + " (" + str(len(tracks)) + " songs)"]
    for i, t in enumerate(tracks[:50], 1):
        artist = ', '.join([a.get('name', '') for a in t.get('ar', t.get('artists', []))])
        lines.append(str(i) + ". " + t.get('name', '') + " - " + artist + " (ID:" + str(t.get('id', '')) + ")")
    return "\n".join(lines)

def get_play_history(limit=30, all_time=False):
    uid = get_uid()
    if not uid:
        return "Failed to get user ID."
    record_type = '0' if all_time else '1'
    url = 'https://music.163.com/api/v1/play/record?uid=' + str(uid) + '&type=' + record_type + '&limit=' + str(limit)
    resp = netease_request(url)
    records = resp.get('weekData') or resp.get('allData') or []
    if not records:
        return "No play history found"
    lines = ["Recent play history:"]
    for i, r in enumerate(records[:limit], 1):
        song = r.get('song', {})
        name = song.get('name', '')
        artist = ', '.join([a.get('name', '') for a in song.get('ar', song.get('artists', []))])
        pc = r.get('playCount', r.get('score', ''))
        lines.append(str(i) + ". " + name + " - " + artist + " (plays:" + str(pc) + ", ID:" + str(song.get('id', '')) + ")")
    return "\n".join(lines)

def like_song(song_id, like=True):
    csrf = get_csrf()
    action = 'true' if like else 'false'
    url = 'https://music.163.com/api/radio/like?alg=itembased&trackId=' + str(song_id) + '&like=' + action + '&time=25&csrf_token=' + csrf
    resp = netease_request(url)
    if resp.get('code') == 200:
        return "Liked song " + str(song_id) if like else "Unliked song " + str(song_id)
    return "Failed: " + resp.get('message', resp.get('error', 'unknown'))

def daily_recommend():
    csrf = get_csrf()
    url = 'https://music.163.com/api/v3/discovery/recommend/songs?csrf_token=' + csrf
    resp = netease_request(url, data='{}')
    songs = resp.get('data', {}).get('dailySongs', [])
    if not songs:
        return "Could not fetch daily recommendations."
    lines = ["Today's recommendations:"]
    for i, s in enumerate(songs[:30], 1):
        name = s.get('name', '')
        artist = ', '.join([a.get('name', '') for a in s.get('ar', s.get('artists', []))])
        reason = s.get('reason', '')
        line = str(i) + ". " + name + " - " + artist + " (ID:" + str(s.get('id', '')) + ")"
        if reason:
            line += " [" + reason + "]"
        lines.append(line)
    return "\n".join(lines)

TOOLS = [
    {"name": "play_music", "description": "Search and play a song from NetEase Cloud Music.", "inputSchema": {"type": "object", "properties": {"query": {"type": "string", "description": "Search query"}, "note": {"type": "string", "description": "Optional note"}}, "required": ["query"]}},
    {"name": "create_playlist", "description": "Create a new playlist in NetEase account.", "inputSchema": {"type": "object", "properties": {"name": {"type": "string", "description": "Playlist name"}, "description": {"type": "string", "description": "Description"}, "privacy": {"type": "integer", "description": "0=public, 10=private"}}, "required": ["name"]}},
    {"name": "add_to_playlist", "description": "Add song(s) to a playlist.", "inputSchema": {"type": "object", "properties": {"playlist_id": {"type": "integer", "description": "Playlist ID"}, "song_ids": {"type": "string", "description": "Song ID(s), comma-separated"}}, "required": ["playlist_id", "song_ids"]}},
    {"name": "remove_from_playlist", "description": "Remove song(s) from a playlist.", "inputSchema": {"type": "object", "properties": {"playlist_id": {"type": "integer", "description": "Playlist ID"}, "song_ids": {"type": "string", "description": "Song ID(s) to remove"}}, "required": ["playlist_id", "song_ids"]}},
    {"name": "list_my_playlists", "description": "List all playlists of the logged-in user.", "inputSchema": {"type": "object", "properties": {}}},
    {"name": "get_playlist_songs", "description": "Get all songs in a playlist.", "inputSchema": {"type": "object", "properties": {"playlist_id": {"type": "integer", "description": "Playlist ID"}}, "required": ["playlist_id"]}},
    {"name": "get_play_history", "description": "Get recent play history.", "inputSchema": {"type": "object", "properties": {"limit": {"type": "integer", "description": "Number of records, default 30"}, "all_time": {"type": "boolean", "description": "true=all time, false=this week (default)"}}}},
    {"name": "like_song", "description": "Like or unlike a song.", "inputSchema": {"type": "object", "properties": {"song_id": {"type": "integer", "description": "Song ID"}, "like": {"type": "boolean", "description": "true=like, false=unlike"}}, "required": ["song_id"]}},
    {"name": "daily_recommend", "description": "Get today's personalized recommendations.", "inputSchema": {"type": "object", "properties": {}}}
]

def handle_jsonrpc(body):
    method = body.get('method', '')
    req_id = body.get('id')
    if method == 'initialize':
        return {"jsonrpc": "2.0", "id": req_id, "result": {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}}, "serverInfo": {"name": "netease-music-mcp", "version": "2.0.0"}}}
    elif method == 'tools/list':
        return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": TOOLS}}
    elif method == 'tools/call':
        name = body.get('params', {}).get('name', '')
        args = body.get('params', {}).get('arguments', {})
        if name == 'play_music':
            text = play_music(args.get('query', ''), args.get('note'))
        elif name == 'create_playlist':
            text = create_playlist(args.get('name', ''), args.get('description', ''), args.get('privacy', 0))
        elif name == 'add_to_playlist':
            text = add_to_playlist(args.get('playlist_id'), args.get('song_ids', ''))
        elif name == 'remove_from_playlist':
            text = remove_from_playlist(args.get('playlist_id'), args.get('song_ids', ''))
        elif name == 'list_my_playlists':
            text = list_my_playlists()
        elif name == 'get_playlist_songs':
            text = get_playlist_songs(args.get('playlist_id'))
        elif name == 'get_play_history':
            text = get_play_history(args.get('limit', 30), args.get('all_time', False))
        elif name == 'like_song':
            text = like_song(args.get('song_id'), args.get('like', True))
        elif name == 'daily_recommend':
            text = daily_recommend()
        else:
            text = "Unknown tool: " + name
        return {"jsonrpc": "2.0", "id": req_id, "result": {"content": [{"type": "text", "text": text}]}}
    elif method.startswith('notifications/'):
        return None
    else:
        return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": "Unknown method: " + method}}

class MCPHandler(http.server.BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        self.send_response(200)
        self._cors()
        self.end_headers()
    def do_GET(self):
        if self.path == '/health':
            self._json_response({"status": "ok", "tools": len(TOOLS)})
        elif self.path.startswith('/sse'):
            self._handle_sse()
        else:
            self.send_error(404)
    def do_POST(self):
        if self.path.startswith('/mcp') or self.path.startswith('/message'):
            self._handle_mcp()
        else:
            self.send_error(404)
    def _cors(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Headers', '*')
        self.send_header('Access-Control-Allow-Methods', '*')
    def _json_response(self, data, status=200):
        self.send_response(status)
        self._cors()
        self.send_header('Content-Type', 'application/json')
        self.send_header('Mcp-Session-Id', SESSION_ID)
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())
    def _handle_mcp(self):
        length = int(self.headers.get('Content-Length', 0))
        body = json.loads(self.rfile.read(length)) if length else {}
        method = body.get('method', '')
        if method.startswith('notifications/') or body.get('id') is None:
            self.send_response(204)
            self._cors()
            self.send_header('Mcp-Session-Id', SESSION_ID)
            self.end_headers()
            return
        result = handle_jsonrpc(body)
        if result is None:
            self.send_response(204)
            self._cors()
            self.end_headers()
            return
        self._json_response(result)
    def _handle_sse(self):
        self.send_response(200)
        self._cors()
        self.send_header('Content-Type', 'text/event-stream')
        self.send_header('Cache-Control', 'no-cache')
        self.end_headers()
        self.wfile.write(b"event: endpoint\ndata: /message\n\n")
        self.wfile.flush()
        try:
            while True:
                time.sleep(30)
                self.wfile.write(b": keepalive\n\n")
                self.wfile.flush()
        except:
            pass
    def log_message(self, format, *args):
        pass

class ThreadedHTTPServer(HTTPServer):
    def process_request(self, request, client_address):
        t = threading.Thread(target=self._handle, args=(request, client_address))
        t.daemon = True
        t.start()
    def _handle(self, request, client_address):
        try:
            self.finish_request(request, client_address)
        except:
            pass
        finally:
            self.shutdown_request(request)

if __name__ == '__main__':
    print("NetEase Music MCP v2 on port " + str(PORT))
    print("Tools: " + str(len(TOOLS)))
    server = ThreadedHTTPServer(('0.0.0.0', PORT), MCPHandler)
    server.serve_forever()
