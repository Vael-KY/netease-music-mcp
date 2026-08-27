#!/usr/bin/env python3
"""NetEase Cloud Music MCP Server - Pure Python, zero dependencies.

A lightweight MCP server that connects AI assistants to NetEase Cloud Music
for playlist management, music discovery, and listening history analysis.

GitHub: https://github.com/Vael-KY/netease-music-mcp
License: MIT
"""
import http.server, json, os, urllib.request, urllib.parse, threading, uuid, time, logging
from http.server import HTTPServer

# --- Configuration ---
NETEASE_COOKIE = os.environ.get("NETEASE_COOKIE", "")
NETEASE_CSRF = os.environ.get("NETEASE_CSRF", "")
PORT = int(os.environ.get("MCP_PORT", "3456"))
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
SESSION_ID = str(uuid.uuid4())

logging.basicConfig(level=getattr(logging, LOG_LEVEL, logging.INFO),
                    format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger("mcp-netease")

# --- CSRF Helper ---
def get_csrf():
    if NETEASE_CSRF:
        return NETEASE_CSRF
    for part in NETEASE_COOKIE.split(';'):
        part = part.strip()
        if part.startswith('__csrf='):
            return part.split('=', 1)[1]
    return ''

# --- NetEase API Helper ---
def netease_request(path, data=None, method='POST'):
    """Make authenticated request to NetEase Cloud Music API."""
    url = f'https://music.163.com{path}'
    headers = {
        'Cookie': NETEASE_COOKIE,
        'Content-Type': 'application/x-www-form-urlencoded',
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Referer': 'https://music.163.com/',
    }
    try:
        if data:
            body = urllib.parse.urlencode(data).encode()
        else:
            body = None
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        logger.error(f"NetEase API error [{path}]: {e}")
        return {"code": -1, "error": str(e)}

# --- Tool Implementations ---

def search_song(params):
    """Search songs by keyword."""
    query = params.get('query', '')
    limit = min(int(params.get('limit', 5)), 10)
    result = netease_request(f'/api/search/get?s={urllib.parse.quote(query)}&type=1&limit={limit}&offset=0', method='GET')
    if not result or result.get('code') != 200:
        return {"error": "Search failed", "detail": result}
    songs = result.get('result', {}).get('songs', [])
    output = []
    for i, s in enumerate(songs, 1):
        artists = ', '.join(a['name'] for a in s.get('artists', []))
        output.append(f"{i}. {s['name']} - {artists} (ID:{s['id']})")
    return {"results": output}

def play_music(params):
    """Search and format a song for playback."""
    query = params.get('query', '')
    result = netease_request(f'/api/search/get?s={urllib.parse.quote(query)}&type=1&limit=1&offset=0', method='GET')
    if not result or result.get('code') != 200:
        return {"error": "Search failed"}
    songs = result.get('result', {}).get('songs', [])
    if not songs:
        return {"error": "No songs found"}
    s = songs[0]
    artists = ', '.join(a['name'] for a in s.get('artists', []))
    return {"title": s['name'], "artist": artists, "id": s['id'],
            "link": f"https://music.163.com/#/song?id={s['id']}"}

def get_play_history(params):
    """Get play history (weekly or all-time)."""
    all_time = str(params.get('all_time', 'false')).lower() == 'true'
    limit = int(params.get('limit', 30))
    rec_type = 0 if all_time else 1
    result = netease_request(f'/api/v1/play/record?type={rec_type}&limit={limit}', method='GET')
    if not result or result.get('code') != 200:
        return {"error": "Failed to get play history", "detail": result}
    key = 'allData' if all_time else 'weekData'
    records = result.get(key, [])[:limit]
    output = []
    for i, r in enumerate(records, 1):
        song = r.get('song', {})
        artists = ', '.join(a['name'] for a in song.get('ar', []))
        score = r.get('score', 0)
        output.append(f"{i}. {song.get('name', '?')} - {artists} (plays: {score}, ID:{song.get('id')})")
    return {"history": output}

def get_recent_plays(params):
    """Get actual recent play events with timestamps."""
    limit = min(int(params.get('limit', 100)), 300)
    result = netease_request(f'/api/play-record/song/list?limit={limit}', method='GET')
    if not result or result.get('code') != 200:
        return {"error": "Failed to get recent plays", "detail": result}
    records = result.get('data', {}).get('list', [])
    output = []
    for i, r in enumerate(records, 1):
        song = r.get('data', {})
        artists = ', '.join(a['name'] for a in song.get('ar', []))
        play_time = r.get('time', 0)
        time_str = time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime(play_time / 1000)) if play_time else '?'
        output.append(f"{i}. {song.get('name', '?')} - {artists} (ID:{song.get('id')}) [{time_str}]")
    return {"recent_plays": output}

def daily_recommend(params):
    """Get daily personalized recommendations."""
    result = netease_request('/api/v3/discovery/recommend/songs', method='GET')
    if not result or result.get('code') != 200:
        return {"error": "Failed to get recommendations", "detail": result}
    songs = result.get('data', {}).get('dailySongs', [])
    output = []
    for i, s in enumerate(songs, 1):
        artists = ', '.join(a['name'] for a in s.get('ar', []))
        output.append(f"{i}. {s['name']} - {artists} (ID:{s['id']})")
    return {"recommendations": output}

def list_my_playlists(params):
    """List all playlists of the logged-in user."""
    uid_result = netease_request('/api/w/nuser/account/get', method='GET')
    if not uid_result or uid_result.get('code') != 200:
        return {"error": "Failed to get user info"}
    uid = uid_result.get('account', {}).get('id')
    if not uid:
        return {"error": "Cannot determine user ID"}
    result = netease_request(f'/api/user/playlist?uid={uid}&limit=50&offset=0', method='GET')
    if not result or result.get('code') != 200:
        return {"error": "Failed to get playlists"}
    playlists = result.get('playlist', [])
    output = []
    for p in playlists:
        owner = "mine" if p.get('creator', {}).get('userId') == uid else "collected"
        desc = f" | {p.get('description', '')[:30]}" if p.get('description') else ""
        output.append(f"ID:{p['id']} | {p['name']} | {p.get('trackCount', 0)} songs ({owner}){desc}")
    return {"playlists": output}

def get_playlist_songs(params):
    """Get all songs in a playlist."""
    pid = params.get('playlist_id')
    if not pid:
        return {"error": "playlist_id required"}
    result = netease_request(f'/api/v6/playlist/detail?id={pid}', method='GET')
    if not result or result.get('code') != 200:
        return {"error": "Failed to get playlist", "detail": result}
    playlist = result.get('playlist', {})
    tracks = playlist.get('tracks', [])
    output = []
    for i, t in enumerate(tracks, 1):
        artists = ', '.join(a['name'] for a in t.get('ar', []))
        output.append(f"{i}. {t['name']} - {artists} (ID:{t['id']})")
    return {"name": playlist.get('name'), "songs": output}

def create_playlist(params):
    """Create a new playlist."""
    name = params.get('name', 'New Playlist')
    privacy = params.get('privacy', 0)
    desc = params.get('description', '')
    csrf = get_csrf()
    data = {'name': name, 'privacy': privacy, 'csrf_token': csrf}
    result = netease_request(f'/api/playlist/create?csrf_token={csrf}', data)
    if not result or result.get('code') != 200:
        return {"error": "Failed to create playlist", "detail": result}
    pid = result.get('id') or result.get('playlist', {}).get('id')
    # Set description if provided
    desc_status = ""
    if desc and pid:
        desc_data = {'id': pid, 'desc': desc, 'csrf_token': csrf}
        desc_result = netease_request(f'/api/playlist/desc/update?csrf_token={csrf}', desc_data)
        if desc_result and desc_result.get('code') == 200:
            desc_status = f" | Description: set"
        else:
            desc_status = f" | Description: failed"
    return {"playlist_id": pid, "name": name, "status": f"created{desc_status}"}

def add_to_playlist(params):
    """Add songs to a playlist."""
    pid = params.get('playlist_id')
    song_ids = params.get('song_ids', '')
    if not pid or not song_ids:
        return {"error": "playlist_id and song_ids required"}
    csrf = get_csrf()
    ids = [s.strip() for s in str(song_ids).split(',')]
    data = {'pid': pid, 'trackIds': json.dumps(ids), 'op': 'add', 'csrf_token': csrf}
    result = netease_request(f'/api/playlist/manipulate/tracks?csrf_token={csrf}', data)
    if not result or result.get('code') != 200:
        return {"error": "Failed to add songs", "detail": result}
    return {"status": f"Added {len(ids)} song(s) to playlist {pid}"}

def remove_from_playlist(params):
    """Remove songs from a playlist."""
    pid = params.get('playlist_id')
    song_ids = params.get('song_ids', '')
    if not pid or not song_ids:
        return {"error": "playlist_id and song_ids required"}
    csrf = get_csrf()
    ids = [s.strip() for s in str(song_ids).split(',')]
    data = {'pid': pid, 'trackIds': json.dumps(ids), 'op': 'del', 'csrf_token': csrf}
    result = netease_request(f'/api/playlist/manipulate/tracks?csrf_token={csrf}', data)
    if not result or result.get('code') != 200:
        return {"error": "Failed to remove songs", "detail": result}
    return {"status": f"Removed {len(ids)} song(s) from playlist {pid}"}

def like_song(params):
    """Like or unlike a song."""
    song_id = params.get('song_id')
    like = str(params.get('like', 'true')).lower() == 'true'
    if not song_id:
        return {"error": "song_id required"}
    csrf = get_csrf()
    result = netease_request(f'/api/song/like?trackId={song_id}&like={str(like).lower()}&csrf_token={csrf}', method='GET')
    if not result or result.get('code') != 200:
        return {"error": "Failed to like/unlike song", "detail": result}
    action = "Liked" if like else "Unliked"
    return {"status": f"{action} song {song_id}"}

def update_playlist_description(params):
    """Update a playlist's description."""
    pid = params.get('playlist_id')
    desc = params.get('description', '')
    if not pid:
        return {"error": "playlist_id required"}
    csrf = get_csrf()
    data = {'id': pid, 'desc': desc, 'csrf_token': csrf}
    result = netease_request(f'/api/playlist/desc/update?csrf_token={csrf}', data)
    if not result or result.get('code') != 200:
        return {"error": "Failed to update description", "detail": result}
    return {"status": f"Updated description for playlist {pid}"}

def reorder_playlist_tracks(params):
    """Reorder all tracks in a playlist."""
    pid = params.get('playlist_id')
    song_ids = params.get('song_ids', '')
    if not pid or not song_ids:
        return {"error": "playlist_id and song_ids required"}
    csrf = get_csrf()
    ids = [int(s.strip()) for s in str(song_ids).split(',')]
    # Get current order
    detail = netease_request(f'/api/v6/playlist/detail?id={pid}', method='GET')
    if not detail or detail.get('code') != 200:
        return {"error": "Failed to get playlist detail"}
    current_ids = [t['id'] for t in detail.get('playlist', {}).get('tracks', [])]
    if current_ids == ids:
        return {"status": "Playlist is already in the requested order."}
    data = {'pid': pid, 'trackIds': json.dumps(ids), 'op': 'update', 'csrf_token': csrf}
    result = netease_request(f'/api/playlist/manipulate/tracks?csrf_token={csrf}', data)
    if not result or result.get('code') != 200:
        return {"error": "Failed to reorder", "detail": result}
    return {"status": f"Reordered {len(ids)} tracks in playlist {pid}"}

def get_song_lyrics(params):
    """Get lyrics for a song."""
    song_id = params.get('song_id')
    if not song_id:
        return {"error": "song_id required"}
    result = netease_request(f'/api/song/lyric?id={song_id}&lv=1&tv=1', method='GET')
    if not result or result.get('code') != 200:
        return {"error": "Failed to get lyrics", "detail": result}
    lrc = result.get('lrc', {}).get('lyric', '')
    tlyric = result.get('tlyric', {}).get('lyric', '')
    return {"lyrics": lrc, "translation": tlyric if tlyric else None, "song_id": song_id}

def get_song_details(params):
    """Get detailed info for one or more songs (max 50)."""
    song_ids = params.get('song_ids', '')
    if not song_ids:
        return {"error": "song_ids required (comma-separated)"}
    ids = [s.strip() for s in str(song_ids).split(',')][:50]
    c_param = json.dumps([{"id": int(i)} for i in ids])
    result = netease_request(f'/api/v3/song/detail', data={'c': c_param})
    if not result or result.get('code') != 200:
        return {"error": "Failed to get song details", "detail": result}
    songs = result.get('songs', [])
    output = []
    for s in songs:
        artists = ', '.join(a['name'] for a in s.get('ar', []))
        album = s.get('al', {}).get('name', '?')
        duration_ms = s.get('dt', 0)
        duration_str = f"{duration_ms // 60000}:{(duration_ms % 60000) // 1000:02d}"
        publish_time = s.get('publishTime', 0)
        year = time.strftime('%Y', time.gmtime(publish_time / 1000)) if publish_time else '?'
        output.append({"id": s['id'], "name": s['name'], "artists": artists,
                       "album": album, "duration": duration_str, "year": year})
    return {"songs": output}

def get_artist_hot_songs(params):
    """Get an artist's top/hot songs."""
    artist_id = params.get('artist_id')
    if not artist_id:
        return {"error": "artist_id required"}
    result = netease_request(f'/api/artist/top/song?id={artist_id}&limit=20', method='GET')
    if not result or result.get('code') != 200:
        # Fallback to older API
        result = netease_request(f'/api/v1/artist/{artist_id}', method='GET')
        if not result or result.get('code') != 200:
            return {"error": "Failed to get artist songs", "detail": result}
        songs = result.get('hotSongs', [])
    else:
        songs = result.get('songs', [])
    output = []
    for i, s in enumerate(songs[:20], 1):
        artists = ', '.join(a['name'] for a in s.get('ar', s.get('artists', [])))
        output.append(f"{i}. {s['name']} - {artists} (ID:{s['id']})")
    artist_name = songs[0].get('ar', songs[0].get('artists', [{}]))[0].get('name', '?') if songs else '?'
    return {"artist": artist_name, "hot_songs": output}

def get_personal_fm(params):
    """Get personal FM recommendations."""
    result = netease_request('/api/v1/radio/get', method='GET')
    if not result or result.get('code') != 200:
        return {"error": "Failed to get personal FM", "detail": result}
    songs = result.get('data', [])
    output = []
    for i, s in enumerate(songs, 1):
        artists = ', '.join(a['name'] for a in s.get('artists', []))
        album = s.get('album', {}).get('name', '?')
        output.append(f"{i}. {s['name']} - {artists} | Album: {album} (ID:{s['id']})")
    return {"personal_fm": output}

def get_liked_songs(params):
    """Get all liked (hearted) song IDs."""
    uid_result = netease_request('/api/w/nuser/account/get', method='GET')
    if not uid_result or uid_result.get('code') != 200:
        return {"error": "Failed to get user info"}
    uid = uid_result.get('account', {}).get('id')
    if not uid:
        return {"error": "Cannot determine user ID"}
    result = netease_request(f'/api/song/like/get?uid={uid}', method='GET')
    if not result or result.get('code') != 200:
        return {"error": "Failed to get liked songs", "detail": result}
    ids = result.get('ids', [])
    return {"count": len(ids), "song_ids": ids[:200], "note": f"Showing first 200 of {len(ids)} liked songs" if len(ids) > 200 else None}



# --- Tool Registry ---
TOOLS = [
    {"name": "search_song", "description": "Search songs by keyword. Returns a list of matching songs with IDs.",
     "inputSchema": {"type": "object", "properties": {"query": {"type": "string", "description": "Search keyword"}, "limit": {"type": "integer", "description": "Max results (1-10, default 5)"}}, "required": ["query"]}},
    {"name": "play_music", "description": "Search and play a song. Returns the top match with playback link.",
     "inputSchema": {"type": "object", "properties": {"query": {"type": "string", "description": "Song name or artist"}}, "required": ["query"]}},
    {"name": "get_play_history", "description": "Get play history rankings (weekly or all-time).",
     "inputSchema": {"type": "object", "properties": {"all_time": {"type": "boolean", "description": "true=all time, false=this week"}, "limit": {"type": "integer", "description": "Number of records (default 30)"}}}},
    {"name": "get_recent_plays", "description": "Get actual recent play events with timestamps.",
     "inputSchema": {"type": "object", "properties": {"limit": {"type": "integer", "description": "Number of events (1-300, default 100)"}}}},
    {"name": "daily_recommend", "description": "Get today's personalized song recommendations.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "list_my_playlists", "description": "List all playlists of the logged-in user.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "get_playlist_songs", "description": "Get all songs in a playlist.",
     "inputSchema": {"type": "object", "properties": {"playlist_id": {"type": "integer", "description": "Playlist ID"}}, "required": ["playlist_id"]}},
    {"name": "create_playlist", "description": "Create a new playlist.",
     "inputSchema": {"type": "object", "properties": {"name": {"type": "string", "description": "Playlist name"}, "description": {"type": "string", "description": "Playlist description"}, "privacy": {"type": "integer", "description": "0=public, 10=private"}}, "required": ["name"]}},
    {"name": "add_to_playlist", "description": "Add songs to a playlist.",
     "inputSchema": {"type": "object", "properties": {"playlist_id": {"type": "integer", "description": "Playlist ID"}, "song_ids": {"type": "string", "description": "Comma-separated song IDs"}}, "required": ["playlist_id", "song_ids"]}},
    {"name": "remove_from_playlist", "description": "Remove songs from a playlist.",
     "inputSchema": {"type": "object", "properties": {"playlist_id": {"type": "integer", "description": "Playlist ID"}, "song_ids": {"type": "string", "description": "Comma-separated song IDs"}}, "required": ["playlist_id", "song_ids"]}},
    {"name": "like_song", "description": "Like or unlike a song.",
     "inputSchema": {"type": "object", "properties": {"song_id": {"type": "integer", "description": "Song ID"}, "like": {"type": "boolean", "description": "true=like, false=unlike"}}, "required": ["song_id"]}},
    {"name": "update_playlist_description", "description": "Update a playlist's description.",
     "inputSchema": {"type": "object", "properties": {"playlist_id": {"type": "integer", "description": "Playlist ID"}, "description": {"type": "string", "description": "New description"}}, "required": ["playlist_id", "description"]}},
    {"name": "reorder_playlist_tracks", "description": "Reorder all tracks in a playlist. Provide complete list of song IDs in desired order.",
     "inputSchema": {"type": "object", "properties": {"playlist_id": {"type": "integer", "description": "Playlist ID"}, "song_ids": {"type": "string", "description": "All song IDs in desired order, comma-separated"}}, "required": ["playlist_id", "song_ids"]}},
    {"name": "get_song_lyrics", "description": "Get lyrics (and translation if available) for a song.",
     "inputSchema": {"type": "object", "properties": {"song_id": {"type": "integer", "description": "Song ID"}}, "required": ["song_id"]}},
    {"name": "get_song_details", "description": "Get detailed info for songs: album, duration, release year. Max 50 songs per call.",
     "inputSchema": {"type": "object", "properties": {"song_ids": {"type": "string", "description": "Comma-separated song IDs (max 50)"}}, "required": ["song_ids"]}},
    {"name": "get_artist_hot_songs", "description": "Get an artist's top 20 hot songs.",
     "inputSchema": {"type": "object", "properties": {"artist_id": {"type": "integer", "description": "Artist ID"}}, "required": ["artist_id"]}},
    {"name": "get_personal_fm", "description": "Get personal FM recommendations (algorithmically curated).",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "get_liked_songs", "description": "Get all liked (red-heart) song IDs for the current user.",
     "inputSchema": {"type": "object", "properties": {}}},
]

def get_user_level(params):
    """Get user level, listen stats, and account age."""
    uid_result = netease_request('/api/w/nuser/account/get', method='GET')
    if not uid_result or uid_result.get('code') != 200:
        return {"error": "Failed to get user info"}
    uid = uid_result.get('account', {}).get('id')
    if not uid:
        return {"error": "Cannot determine user ID"}
    result = netease_request(f'/api/user/detail?uid={uid}', method='GET')
    if not result or result.get('code') != 200:
        return {"error": "Failed to get user detail", "detail": result}
    profile = result.get('profile', {})
    return {
        "level": result.get('level', profile.get('level')),
        "listen_songs": result.get('listenSongs', profile.get('listenSongs')),
        "create_days": result.get('createDays'),
        "create_time": result.get('createTime'),
        "nickname": profile.get('nickname'),
        "vip_type": profile.get('vipType'),
    }

TOOL_DISPATCH = {
    "search_song": search_song,
    "play_music": play_music,
    "get_play_history": get_play_history,
    "get_recent_plays": get_recent_plays,
    "daily_recommend": daily_recommend,
    "list_my_playlists": list_my_playlists,
    "get_playlist_songs": get_playlist_songs,
    "create_playlist": create_playlist,
    "add_to_playlist": add_to_playlist,
    "remove_from_playlist": remove_from_playlist,
    "like_song": like_song,
    "update_playlist_description": update_playlist_description,
    "reorder_playlist_tracks": reorder_playlist_tracks,
    "get_song_lyrics": get_song_lyrics,
    "get_song_details": get_song_details,
    "get_artist_hot_songs": get_artist_hot_songs,
    "get_personal_fm": get_personal_fm,
    "get_liked_songs": get_liked_songs,
    "get_user_level": get_user_level,
}

# --- MCP Protocol Handler ---
class MCPHandler(http.server.BaseHTTPRequestHandler):
    def _cors(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', '*')
    def _json_response(self, data, status=200):
        body = json.dumps(data).encode()
        self.send_response(status)
        self._cors()
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', len(body))
        self.end_headers()
        self.wfile.write(body)
    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()
    def do_GET(self):
        if self.path == '/health':
            self._json_response({"status": "ok", "tools": len(TOOLS), "version": "3.1.0"})
        elif self.path == '/sse':
            self._handle_sse()
        else:
            self._json_response({"error": "Not found"}, 404)
    def do_POST(self):
        length = int(self.headers.get('Content-Length', 0))
        body = json.loads(self.rfile.read(length)) if length else {}
        method = body.get('method', '')
        req_id = body.get('id')
        # Notifications and requests without id get 204 (no response body)
        if method.startswith('notifications/') or req_id is None:
            self.send_response(204)
            self._cors()
            self.end_headers()
            return
        if method == 'initialize':
            result = {"protocolVersion": "2025-03-26", "capabilities": {"tools": {"listChanged": False}},
                      "serverInfo": {"name": "netease-music-mcp", "version": "3.1.0"}}
        elif method == 'tools/list':
            result = {"tools": TOOLS}
        elif method == 'tools/call':
            tool_name = body.get('params', {}).get('name', '')
            arguments = body.get('params', {}).get('arguments', {})
            logger.info(f"Tool call: {tool_name}")
            handler = TOOL_DISPATCH.get(tool_name)
            if handler:
                try:
                    tool_result = handler(arguments)
                    result = {"content": [{"type": "text", "text": json.dumps(tool_result, ensure_ascii=False)}]}
                except Exception as e:
                    logger.error(f"Tool error [{tool_name}]: {e}")
                    result = {"content": [{"type": "text", "text": json.dumps({"error": str(e)})}], "isError": True}
            else:
                result = {"content": [{"type": "text", "text": f"Unknown tool: {tool_name}"}], "isError": True}
        else:
            result = {"error": {"code": -32601, "message": f"Unknown method: {method}"}}
        response = {"jsonrpc": "2.0", "id": req_id, "result": result}
        self._json_response(response)
    def _handle_sse(self):
        self.send_response(200)
        self._cors()
        self.send_header('Content-Type', 'text/event-stream')
        self.send_header('Cache-Control', 'no-cache')
        self.send_header('Connection', 'keep-alive')
        self.end_headers()
        endpoint = f"http://localhost:{PORT}/message?sessionId={SESSION_ID}"
        self.wfile.write(f"event: endpoint\ndata: {endpoint}\n\n".encode())
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
    logger.info(f"Starting NetEase Music MCP Server v3.1.0 with {len(TOOLS)} tools on port {PORT}")
    server = ThreadedHTTPServer(('0.0.0.0', PORT), MCPHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Server shutting down")
        server.shutdown()
