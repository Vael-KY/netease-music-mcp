#!/usr/bin/env python3
"""NetEase Cloud Music MCP Server - Pure Python, zero dependencies.

A lightweight MCP server that connects AI assistants to NetEase Cloud Music
for playlist management, music discovery, and listening history analysis.

Derived from the original netease-music-mcp project by Vael-KY.
License: MIT
"""
import http.server, json, os, urllib.request, urllib.parse, threading, uuid, time, logging
from http.server import HTTPServer

# --- Configuration ---
NETEASE_COOKIE = os.environ.get("NETEASE_COOKIE", "")
PORT = int(os.environ.get("MCP_PORT", "3456"))
SESSION_ID = str(uuid.uuid4())

# --- Logging ---
logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(message)s",
)
LOG = logging.getLogger("netease_music_mcp")

# --- NetEase API Layer ---
def netease_request(url, data=None):
    """Make a request to the NetEase Cloud Music API."""
    headers = {
        'User-Agent': 'Mozilla/5.0',
        'Referer': 'https://music.163.com/',
        'Cookie': NETEASE_COOKIE,
        'Content-Type': 'application/x-www-form-urlencoded' if data else 'application/json'
    }
    if data and isinstance(data, dict):
        data = urllib.parse.urlencode(data).encode()
    elif data and isinstance(data, str):
        data = data.encode()
    req = urllib.request.Request(url, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        LOG.warning("NetEase HTTP error %d for %s", e.code, url.split('?')[0])
        return {"code": -1, "error": f"HTTP {e.code}"}
    except urllib.error.URLError as e:
        LOG.warning("NetEase URL error for %s: %s", url.split('?')[0], e.reason)
        return {"code": -1, "error": str(e.reason)}
    except Exception as e:
        LOG.error("Unexpected error for %s: %s", url.split('?')[0], str(e))
        return {"code": -1, "error": str(e)}

def get_uid():
    """Get the current user's NetEase UID."""
    resp = netease_request('https://music.163.com/api/nuser/account/get')
    try:
        return resp.get('profile', {}).get('userId') or resp.get('account', {}).get('id')
    except Exception:
        return None

def get_csrf():
    """Extract CSRF token from cookie."""
    for part in NETEASE_COOKIE.split(';'):
        part = part.strip()
        if part.startswith('__csrf='):
            return part.split('=', 1)[1]
    return ''

# --- Tool Implementations ---
def search_song(query, limit=5):
    """Search for songs by keyword."""
    if not query or not query.strip():
        return "Query cannot be empty."
    limit = max(1, min(limit, 10))
    url = 'https://music.163.com/api/search/get?s=' + urllib.parse.quote(query.strip()) + '&type=1&limit=' + str(limit)
    resp = netease_request(url)
    songs = resp.get('result', {}).get('songs', [])
    if not songs:
        return "No results for '" + query.strip() + "'"
    lines = []
    for i, s in enumerate(songs[:limit], 1):
        artist = ', '.join([a.get('name', '') for a in s.get('artists', [])])
        lines.append(str(i) + ". " + s.get('name', '') + " - " + artist + " (ID:" + str(s.get('id', '')) + ")")
    return "\n".join(lines)

def play_music(query, note=None):
    """Search and return a formatted music card."""
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
    except Exception:
        pic_url = ''
    name = s.get('name', '').replace(':', '\uff1a')
    artist = ', '.join([a.get('name', '') for a in s.get('artists', [])]).replace(':', '\uff1a')
    link = "https://music.163.com/song?id=" + str(song_id)
    return "[music:" + str(song_id) + ":" + name + ":" + artist + ":" + pic_url + "]" + (note or '') + "\n" + link

def update_playlist_description(playlist_id, description):
    """Update a playlist's description."""
    csrf = get_csrf()
    url = 'https://music.163.com/api/playlist/desc/update?csrf_token=' + csrf
    data = {'id': str(playlist_id), 'desc': description}
    resp = netease_request(url, data=data)
    if resp.get('code') == 200:
        return "Updated description for playlist " + str(playlist_id)
    return "Failed: " + str(resp.get('message', resp.get('error', 'unknown')))

def create_playlist(name, description='', privacy=0):
    """Create a new playlist, optionally with a description."""
    csrf = get_csrf()
    url = 'https://music.163.com/api/playlist/create?csrf_token=' + csrf
    data = {'name': name, 'privacy': str(privacy), 'type': 'NORMAL'}
    resp = netease_request(url, data=data)
    if resp.get('code') == 200:
        pl = resp.get('playlist', {})
        pl_id = pl.get('id')
        result = "Created playlist '" + name + "' (ID: " + str(pl_id) + ")"
        if description and pl_id:
            desc_result = update_playlist_description(pl_id, description)
            result += " | Description: " + desc_result
        return result
    return "Failed: " + str(resp.get('message', resp.get('error', 'unknown')))

def add_to_playlist(playlist_id, song_ids):
    """Add songs to a playlist."""
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
    return "Failed: " + str(resp.get('message', resp.get('error', 'unknown')))

def remove_from_playlist(playlist_id, song_ids):
    """Remove songs from a playlist."""
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
    return "Failed: " + str(resp.get('message', resp.get('error', 'unknown')))

def reorder_playlist_tracks(playlist_id, song_ids):
    """Reorder tracks in a playlist. Requires the complete list of song IDs in desired order."""
    csrf = get_csrf()
    uid = get_uid()
    if not uid:
        return "Failed to get user ID. Cookie may be expired."
    # Verify ownership
    url = 'https://music.163.com/api/v6/playlist/detail?id=' + str(playlist_id) + '&n=100000&s=0'
    resp = netease_request(url)
    playlist = resp.get('playlist', {})
    creator = playlist.get('creator', {})
    if creator.get('userId') != uid:
        return "Error: You can only reorder playlists you own."
    # Get current track IDs
    track_ids_raw = playlist.get('trackIds', [])
    current_ids = [t['id'] for t in track_ids_raw if isinstance(t, dict) and 'id' in t]
    # Validate: must be same set
    if isinstance(song_ids, str):
        requested_ids = [int(s.strip()) for s in song_ids.split(',')]
    elif isinstance(song_ids, list):
        requested_ids = [int(s) for s in song_ids]
    else:
        return "Error: song_ids must be a list or comma-separated string."
    if set(requested_ids) != set(current_ids) or len(requested_ids) != len(current_ids):
        return "Error: song_ids must contain exactly the same tracks as the playlist, in your desired order."
    if requested_ids == current_ids:
        return "Playlist is already in the requested order."
    # Execute reorder
    url = 'https://music.163.com/api/playlist/manipulate/tracks?csrf_token=' + csrf
    data = {'pid': str(playlist_id), 'trackIds': json.dumps(requested_ids), 'op': 'update'}
    resp = netease_request(url, data=data)
    if resp.get('code') == 200:
        return "Reordered " + str(len(requested_ids)) + " tracks in playlist " + str(playlist_id)
    return "Failed: " + str(resp.get('message', resp.get('error', 'unknown')))

def list_my_playlists():
    """List all playlists of the current user."""
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
    """Get all songs in a playlist."""
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
    """Get aggregated play history (weekly or all-time ranking)."""
    uid = get_uid()
    if not uid:
        return "Failed to get user ID."
    record_type = '0' if all_time else '1'
    url = 'https://music.163.com/api/v1/play/record?uid=' + str(uid) + '&type=' + record_type + '&limit=' + str(limit)
    resp = netease_request(url)
    records = resp.get('weekData') or resp.get('allData') or []
    if not records:
        return "No play history found"
    lines = ["Play history (" + ("all time" if all_time else "this week") + "):"]
    for i, r in enumerate(records[:limit], 1):
        song = r.get('song', {})
        name = song.get('name', '')
        artist = ', '.join([a.get('name', '') for a in song.get('ar', song.get('artists', []))])
        pc = r.get('playCount', r.get('score', ''))
        lines.append(str(i) + ". " + name + " - " + artist + " (plays:" + str(pc) + ", ID:" + str(song.get('id', '')) + ")")
    return "\n".join(lines)

def get_recent_plays(limit=100):
    """Get actual recent play events with timestamps."""
    limit = max(1, min(limit, 100))
    resp = netease_request(
        'https://music.163.com/api/play-record/song/list',
        data={'limit': str(limit)}
    )
    if resp.get('code') not in (None, 200):
        return "Failed to fetch recent plays: " + str(resp.get('message', resp.get('error', 'unknown')))
    data = resp.get('data', {})
    entries = data.get('list', [])
    if not entries:
        return "No recent play events found."
    lines = ["Recent plays (" + str(len(entries)) + " events):"]
    for i, entry in enumerate(entries[:limit], 1):
        song = entry.get('data', {})
        name = song.get('name', '')
        artists = song.get('ar', song.get('artists', []))
        artist = ', '.join([a.get('name', '') for a in artists]) if artists else ''
        play_time = entry.get('playTime')
        time_str = ''
        if play_time and isinstance(play_time, int):
            from datetime import datetime, timezone
            dt = datetime.fromtimestamp(play_time / 1000, tz=timezone.utc)
            time_str = ' [' + dt.strftime('%Y-%m-%d %H:%M') + ' UTC]'
        song_id = song.get('id', entry.get('resourceId', ''))
        lines.append(str(i) + ". " + name + " - " + artist + " (ID:" + str(song_id) + ")" + time_str)
    return "\n".join(lines)

def like_song(song_id, like=True):
    """Like or unlike a song."""
    csrf = get_csrf()
    action = 'true' if like else 'false'
    url = 'https://music.163.com/api/radio/like?alg=itembased&trackId=' + str(song_id) + '&like=' + action + '&time=25&csrf_token=' + csrf
    resp = netease_request(url)
    if resp.get('code') == 200:
        return ("Liked" if like else "Unliked") + " song " + str(song_id)
    return "Failed: " + str(resp.get('message', resp.get('error', 'unknown')))

def daily_recommend():
    """Get today's personalized daily recommendations."""
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

# --- Tool Registry ---
TOOLS = [
    {"name": "search_song", "description": "Search NetEase Cloud Music by keyword. Returns a list of matching songs with IDs.", "inputSchema": {"type": "object", "properties": {"query": {"type": "string", "description": "Search keyword"}, "limit": {"type": "integer", "description": "Max results (1-10, default 5)"}}, "required": ["query"]}},
    {"name": "play_music", "description": "Search and play a song from NetEase Cloud Music.", "inputSchema": {"type": "object", "properties": {"query": {"type": "string", "description": "Search query"}, "note": {"type": "string", "description": "Optional note"}}, "required": ["query"]}},
    {"name": "create_playlist", "description": "Create a new playlist in NetEase account.", "inputSchema": {"type": "object", "properties": {"name": {"type": "string", "description": "Playlist name"}, "description": {"type": "string", "description": "Description"}, "privacy": {"type": "integer", "description": "0=public, 10=private"}}, "required": ["name"]}},
    {"name": "update_playlist_description", "description": "Update a playlist's description.", "inputSchema": {"type": "object", "properties": {"playlist_id": {"type": "integer", "description": "Playlist ID"}, "description": {"type": "string", "description": "New description text"}}, "required": ["playlist_id", "description"]}},
    {"name": "add_to_playlist", "description": "Add song(s) to a playlist.", "inputSchema": {"type": "object", "properties": {"playlist_id": {"type": "integer", "description": "Playlist ID"}, "song_ids": {"type": "string", "description": "Song ID(s), comma-separated"}}, "required": ["playlist_id", "song_ids"]}},
    {"name": "remove_from_playlist", "description": "Remove song(s) from a playlist.", "inputSchema": {"type": "object", "properties": {"playlist_id": {"type": "integer", "description": "Playlist ID"}, "song_ids": {"type": "string", "description": "Song ID(s) to remove"}}, "required": ["playlist_id", "song_ids"]}},
    {"name": "reorder_playlist_tracks", "description": "Reorder all tracks in a playlist. Provide the complete list of song IDs in your desired order.", "inputSchema": {"type": "object", "properties": {"playlist_id": {"type": "integer", "description": "Playlist ID"}, "song_ids": {"type": "string", "description": "All song IDs in desired order, comma-separated"}}, "required": ["playlist_id", "song_ids"]}},
    {"name": "list_my_playlists", "description": "List all playlists of the logged-in user.", "inputSchema": {"type": "object", "properties": {}}},
    {"name": "get_playlist_songs", "description": "Get all songs in a playlist.", "inputSchema": {"type": "object", "properties": {"playlist_id": {"type": "integer", "description": "Playlist ID"}}, "required": ["playlist_id"]}},
    {"name": "get_play_history", "description": "Get recent play history.", "inputSchema": {"type": "object", "properties": {"limit": {"type": "integer", "description": "Number of records, default 30"}, "all_time": {"type": "boolean", "description": "true=all time, false=this week (default)"}}}},
    {"name": "get_recent_plays", "description": "Get actual recent play events with timestamps. Unlike play_history which shows aggregated rankings, this returns individual play events in chronological order.", "inputSchema": {"type": "object", "properties": {"limit": {"type": "integer", "description": "Number of events (1-100, default 100)"}}}},
    {"name": "like_song", "description": "Like or unlike a song.", "inputSchema": {"type": "object", "properties": {"song_id": {"type": "integer", "description": "Song ID"}, "like": {"type": "boolean", "description": "true=like, false=unlike"}}, "required": ["song_id"]}},
    {"name": "daily_recommend", "description": "Get today's personalized recommendations.", "inputSchema": {"type": "object", "properties": {}}}
]

# --- JSON-RPC Handler ---
def handle_jsonrpc(body):
    method = body.get('method', '')
    req_id = body.get('id')
    if method == 'initialize':
        return {"jsonrpc": "2.0", "id": req_id, "result": {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}}, "serverInfo": {"name": "netease-music-mcp", "version": "3.0.0"}}}
    elif method == 'tools/list':
        return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": TOOLS}}
    elif method == 'tools/call':
        name = body.get('params', {}).get('name', '')
        args = body.get('params', {}).get('arguments', )
        LOG.info("Tool call: %s", name)
        try:
            if name == 'search_song':
                text = search_song(args.get('query', ''), args.get('limit', 5))
            elif name == 'play_music':
                text = play_music(args.get('query', ''), args.get('note'))
            elif name == 'create_playlist':
                text = create_playlist(args.get('name', ''), args.get('description', ''), args.get('privacy', 0))
            elif name == 'update_playlist_description':
                text = update_playlist_description(args.get('playlist_id'), args.get('description', ''))
            elif name == 'add_to_playlist':
                text = add_to_playlist(args.get('playlist_id'), args.get('song_ids', ''))
            elif name == 'remove_from_playlist':
                text = remove_from_playlist(args.get('playlist_id'), args.get('song_ids', ''))
            elif name == 'reorder_playlist_tracks':
                text = reorder_playlist_tracks(args.get('playlist_id'), args.get('song_ids', ''))
            elif name == 'list_my_playlists':
                text = list_my_playlists()
            elif name == 'get_playlist_songs':
                text = get_playlist_songs(args.get('playlist_id'))
            elif name == 'get_play_history':
                text = get_play_history(args.get('limit', 30), args.get('all_time', False))
            elif name == 'get_recent_plays':
                text = get_recent_plays(args.get('limit', 100))
            elif name == 'like_song':
                text = like_song(args.get('song_id'), args.get('like', True))
            elif name == 'daily_recommend':
                text = daily_recommend()
            else:
                text = "Unknown tool: " + name
        except Exception as e:
            LOG.error("Tool %s failed: %s", name, str(e))
            text = "Error: " + str(e)
        return {"jsonrpc": "2.0", "id": req_id, "result": {"content": [{"type": "text", "text": text}]}}
    elif method.startswith('notifications/'):
        return None
    else:
        return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": "Unknown method: " + method}}

# --- HTTP Server ---
class MCPHandler(http.server.BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        self.send_response(200)
        self._cors()
        self.end_headers()
    def do_GET(self):
        if self.path == '/health':
            self._json_response({"status": "ok", "tools": len(TOOLS), "version": "3.0.0"})
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
        except Exception:
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
        except Exception:
            pass
        finally:
            self.shutdown_request(request)

if __name__ == '__main__':
    LOG.info("NetEase Music MCP v3.0.0 starting on port %d with %d tools", PORT, len(TOOLS))
    server = ThreadedHTTPServer(('0.0.0.0', PORT), MCPHandler)
    server.serve_forever()
