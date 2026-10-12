#!/usr/bin/env python3
"""NetEase Cloud Music MCP Server - Pure Python, zero dependencies.

A lightweight MCP server that connects AI assistants to NetEase Cloud Music
for playlist management, music discovery, and listening history analysis.

GitHub: https://github.com/Vael-KY/netease-music-mcp
License: MIT
"""
import http.server, json, os, urllib.request, urllib.parse, urllib.error, threading, uuid, time, logging
import base64, secrets, string
from http.server import HTTPServer

VERSION = "3.2.1"

# --- .env loading ---
def _load_dotenv():
    """Load KEY=VALUE lines from a .env file; variables already in the environment win.

    Done here instead of `export $(cat .env | xargs)`: a Cookie value contains spaces
    and ';', which that shell idiom splits apart, silently dropping __csrf.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    for base in (os.getcwd(), os.path.dirname(os.path.dirname(here)), here):
        path = os.path.join(base, '.env')
        if not os.path.isfile(path):
            continue
        try:
            with open(path, encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith('#') or '=' not in line:
                        continue
                    key, _, value = line.partition('=')
                    key, value = key.strip(), value.strip()
                    if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                        value = value[1:-1]
                    if key and key not in os.environ:
                        os.environ[key] = value
        except OSError:
            pass
        return

_load_dotenv()

# --- Configuration ---
NETEASE_COOKIE = os.environ.get("NETEASE_COOKIE", "")
NETEASE_CSRF = os.environ.get("NETEASE_CSRF", "")
NETEASE_UID = os.environ.get("NETEASE_UID", "")  # optional; looked up automatically if empty
PORT = int(os.environ.get("MCP_PORT", "3456"))
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
SESSION_ID = str(uuid.uuid4())
try:
    TZ_OFFSET_HOURS = float(os.environ.get("NETEASE_TZ_OFFSET", "0"))  # timezone of timestamps in tool output
except ValueError:
    TZ_OFFSET_HOURS = 0.0

logging.basicConfig(level=getattr(logging, LOG_LEVEL, logging.INFO),
                    format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger("mcp-netease")

# --- Cookie / CSRF helpers ---
# NetEase's web client always sends a full cookie jar (device ids, __csrf, ...), and its
# gateway may reject incomplete cookies on playlist create/update. Missing cookies are
# filled with defaults used by community NeteaseCloudMusicApi projects; whether this
# resolves a particular 403 still needs an authenticated live test.
# Whatever you put in NETEASE_COOKIE always wins; nothing you supplied is overridden.
_UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) '
       'Chrome/124.0.0.0 Safari/537.36')

def _parse_cookie(raw):
    """'a=1; b=2' -> {'a': '1', 'b': '2'}. A bare value without '=' is taken as MUSIC_U."""
    raw = (raw or '').strip()
    if raw and '=' not in raw:
        raw = 'MUSIC_U=' + raw
    jar = {}
    for part in raw.split(';'):
        key, sep, value = part.strip().partition('=')
        if sep and key:
            jar[key] = value.strip()
    return jar

_DEVICE = {}

def _device_cookies():
    """A fake-but-consistent device identity, generated once per process."""
    if not _DEVICE:
        nuid = secrets.token_hex(16)
        now_ms = int(time.time() * 1000)
        tag = ''.join(secrets.choice(string.ascii_lowercase) for _ in range(6))
        _DEVICE.update({
            '__remember_me': 'true', 'ntes_kaola_ad': '1',
            '_ntes_nuid': nuid, '_ntes_nnid': f'{nuid},{now_ms}',
            'WNMCID': f'{tag}.{now_ms}.01.0', 'WEVNSM': '1.0.0',
            'NMTID': '00O' + secrets.token_hex(19),
            'os': 'pc', 'appver': '3.1.17.204416',
            'osver': 'Microsoft-Windows-10-Professional-build-19045-64bit',
            'channel': 'netease',
        })
    return _DEVICE

def build_cookie_header():
    jar = _parse_cookie(NETEASE_COOKIE)
    if NETEASE_CSRF and '__csrf' not in jar:
        jar['__csrf'] = NETEASE_CSRF  # the csrf_token we send must also be present as a cookie
    for key, value in _device_cookies().items():
        jar.setdefault(key, value)
    return '; '.join(f'{k}={v}' for k, v in jar.items())

def get_csrf():
    return _parse_cookie(NETEASE_COOKIE).get('__csrf') or NETEASE_CSRF or ''

def _hint(*results):
    """Short, actionable explanation for the usual failure codes."""
    codes = {r.get('code') for r in results if isinstance(r, dict)}
    if 403 in codes:
        return ("403 illegal request: put the FULL Cookie header copied from music.163.com "
                "(DevTools > Network > any request > Cookie) into NETEASE_COOKIE; "
                "it must contain MUSIC_U and __csrf")
    if 301 in codes:
        return "Not logged in or session expired: refresh MUSIC_U / __csrf in NETEASE_COOKIE"
    if -460 in codes or 460 in codes:
        return "NetEase risk control flagged this server's IP or cookie; retry later or use the full browser cookie"
    return None

def _with_hint(result):
    hint = _hint(result)
    return dict(result, hint=hint) if hint else result

def _http_error_result(path, e):
    """Turn an HTTPError into a result dict that keeps NetEase's own error body."""
    try:
        raw = e.read().decode('utf-8', 'replace')
    except Exception:
        raw = ''
    logger.error("NetEase HTTP %s [%s]", e.code, path.split('?', 1)[0])
    try:
        body = json.loads(raw)
        if isinstance(body, dict):
            body.setdefault('code', e.code)
            body['http_status'] = e.code
            return body
    except ValueError:
        pass
    return {"code": e.code, "error": f"HTTP {e.code}: {e.reason}", "body": raw[:300]}

# --- NetEase API Helper ---
def netease_request(path, data=None, method='POST'):
    """Plain /api/ request (body not encrypted). Fine for public/read endpoints."""
    url = f'https://music.163.com{path}'
    headers = {
        'Cookie': build_cookie_header(),
        'Content-Type': 'application/x-www-form-urlencoded',
        'User-Agent': _UA,
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
    except urllib.error.HTTPError as e:
        return _http_error_result(path, e)
    except Exception as e:
        logger.error("NetEase API error [%s]: %s", path.split('?', 1)[0], type(e).__name__)
        return {"code": -1, "error": str(e)}

# --- weapi Encryption (pure Python, zero dependencies) ---
# Some endpoints (e.g. playlist creation) now reject plain /api/ requests.
# NetEase's web client uses "weapi": AES-128-CBC twice + RSA on the key.
# AES is implemented below in pure Python so the project stays dependency-free.

_WEAPI_PRESET_KEY = b'0CoJUm6Qyw8W8jud'
_WEAPI_IV = b'0102030405060708'
_WEAPI_PUB_E = 0x010001
_WEAPI_MODULUS = int(
    '00e0b509f6259df8642dbc35662901477df22677ec152b5ff68ace615bb7b725152b3ab17a876aea8a5aa76d2e417629ec'
    '4ee341f56135fccf695280104e0312ecbda92557c93870114af6c9d05c4f7f0c3685b7a46bee255932575cce10b424d8'
    '13cfe4875d3e82047b97ddef52741d546b8e289dc6935b3ece0462db0a22b8e7', 16)

def _rotl8(x, s):
    return ((x << s) | (x >> (8 - s))) & 0xFF

def _xtime(a):
    return ((a << 1) ^ 0x1B) & 0xFF if a & 0x80 else a << 1

def _build_sbox():
    sbox = [0] * 256
    p = q = 1
    while True:
        p = p ^ ((p << 1) & 0xFF) ^ (0x1B if p & 0x80 else 0)
        q ^= q << 1
        q ^= q << 2
        q ^= q << 4
        q &= 0xFF
        if q & 0x80:
            q ^= 0x09
        x = q ^ _rotl8(q, 1) ^ _rotl8(q, 2) ^ _rotl8(q, 3) ^ _rotl8(q, 4)
        sbox[p] = x ^ 0x63
        if p == 1:
            break
    sbox[0] = 0x63
    return sbox

_SBOX = _build_sbox()

def _expand_key(key):
    w = [list(key[i:i + 4]) for i in range(0, 16, 4)]
    rcon = 1
    for i in range(4, 44):
        t = list(w[i - 1])
        if i % 4 == 0:
            t = t[1:] + t[:1]
            t = [_SBOX[b] for b in t]
            t[0] ^= rcon
            rcon = _xtime(rcon)
        w.append([w[i - 4][j] ^ t[j] for j in range(4)])
    return [sum(w[r * 4:r * 4 + 4], []) for r in range(11)]

def _encrypt_block(block, rks):
    s = [b ^ k for b, k in zip(block, rks[0])]
    for r in range(1, 11):
        s = [_SBOX[b] for b in s]
        s = [s[((i // 4 + i % 4) % 4) * 4 + i % 4] for i in range(16)]
        if r != 10:
            m = []
            for c in range(4):
                a = s[c * 4:c * 4 + 4]
                t = a[0] ^ a[1] ^ a[2] ^ a[3]
                m += [a[i] ^ t ^ _xtime(a[i] ^ a[(i + 1) % 4]) for i in range(4)]
            s = m
        s = [b ^ k for b, k in zip(s, rks[r])]
    return s

def _aes_cbc_encrypt(data, key, iv):
    pad = 16 - len(data) % 16
    data = data + bytes([pad]) * pad
    rks = _expand_key(key)
    prev = list(iv)
    out = bytearray()
    for i in range(0, len(data), 16):
        prev = _encrypt_block([b ^ p for b, p in zip(data[i:i + 16], prev)], rks)
        out += bytes(prev)
    return bytes(out)

def weapi_encrypt(payload):
    """Encrypt a dict into weapi form fields {params, encSecKey}."""
    text = json.dumps(payload).encode()
    alphabet = string.ascii_letters + string.digits
    secret = ''.join(secrets.choice(alphabet) for _ in range(16)).encode()
    first = base64.b64encode(_aes_cbc_encrypt(text, _WEAPI_PRESET_KEY, _WEAPI_IV))
    params = base64.b64encode(_aes_cbc_encrypt(first, secret, _WEAPI_IV)).decode()
    rs = pow(int(secret[::-1].hex(), 16), _WEAPI_PUB_E, _WEAPI_MODULUS)
    return {'params': params, 'encSecKey': format(rs, 'x').zfill(256)}

def netease_weapi(path, payload=None):
    """Make an authenticated weapi (encrypted) request. path like '/playlist/create'."""
    csrf = get_csrf()
    payload = dict(payload or {}, csrf_token=csrf)
    url = f'https://music.163.com/weapi{path}?csrf_token={csrf}'
    headers = {
        'Cookie': build_cookie_header(),
        'Content-Type': 'application/x-www-form-urlencoded',
        'User-Agent': _UA,
        'Referer': 'https://music.163.com/',
        'Origin': 'https://music.163.com',
    }
    try:
        body = urllib.parse.urlencode(weapi_encrypt(payload)).encode()
        req = urllib.request.Request(url, data=body, headers=headers, method='POST')
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return _http_error_result(f'/weapi{path}', e)
    except Exception as e:
        logger.error("NetEase weapi error [%s]: %s", path.split('?', 1)[0], type(e).__name__)
        return {"code": -1, "error": str(e)}

# Codes meaning "the gateway refused this route/format" (worth trying the other route),
# as opposed to business errors such as 502 "song already in playlist".
_ROUTE_FAILURES = {-1, 400, 403, 404, 405}

def write_call(path, payload):
    """Write operation: encrypted weapi first, then the old plain /api/ route as fallback.

    On total failure returns {"code", "weapi", "legacy", "hint"} for the caller to report.
    """
    primary = netease_weapi(path, payload)
    if primary.get('code') == 200:
        return primary
    # A transport failure does not prove the write failed. Retrying through another
    # route could create the same playlist twice.
    if primary.get('code') == -1:
        return dict(primary, hint="Write outcome unknown; check the playlist before retrying")
    if primary.get('code') not in _ROUTE_FAILURES:
        return _with_hint(primary)
    logger.warning(f"weapi {path} failed (code {primary.get('code')}), trying legacy route")
    csrf = get_csrf()
    legacy = netease_request(f'/api{path}?csrf_token={csrf}', dict(payload, csrf_token=csrf))
    if legacy.get('code') == 200:
        return legacy
    return {"code": primary.get('code'), "weapi": primary, "legacy": legacy, "hint": _hint(primary, legacy)}

def read_call(path, payload=None, legacy_get=None):
    """Read operation: weapi first; optionally fall back to a plain GET of the old route."""
    primary = netease_weapi(path, payload)
    if primary.get('code') == 200:
        return primary
    if legacy_get and primary.get('code') in _ROUTE_FAILURES:
        logger.warning(f"weapi {path} failed (code {primary.get('code')}), trying legacy route")
        legacy = netease_request(legacy_get, method='GET')
        if legacy.get('code') == 200:
            return legacy
        return {"code": primary.get('code'), "weapi": primary, "legacy": legacy, "hint": _hint(primary, legacy)}
    return _with_hint(primary)

_UID_CACHE = {}

def get_uid():
    """Logged-in user id: NETEASE_UID if set, else looked up once and cached."""
    if NETEASE_UID:
        return NETEASE_UID
    if _UID_CACHE.get('uid'):
        return _UID_CACHE['uid']
    attempts = (
        lambda: netease_weapi('/nuser/account/get', {}),
        lambda: netease_weapi('/w/nuser/account/get', {}),
        lambda: netease_request('/api/w/nuser/account/get', method='GET'),
    )
    for attempt in attempts:
        r = attempt()
        if r.get('code') == 200:
            uid = (r.get('account') or {}).get('id') or (r.get('profile') or {}).get('userId')
            if uid:
                _UID_CACHE['uid'] = str(uid)
                return _UID_CACHE['uid']
    return None

def _fmt_time(value):
    """Epoch seconds or milliseconds -> 'YYYY-MM-DD HH:MM UTC+8' ('?' if missing)."""
    try:
        ts = float(value)
    except (TypeError, ValueError):
        return '?'
    if ts <= 0:
        return '?'
    if ts > 1e11:  # milliseconds
        ts /= 1000
    label = 'UTC' if TZ_OFFSET_HOURS == 0 else f'UTC{TZ_OFFSET_HOURS:+g}'
    return time.strftime('%Y-%m-%d %H:%M', time.gmtime(ts + TZ_OFFSET_HOURS * 3600)) + ' ' + label

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
    limit = max(1, int(params.get('limit', 30)))
    uid = get_uid()
    if not uid:
        return {"error": "Cannot determine user ID (MUSIC_U missing or expired?)"}
    # This endpoint needs the uid and is served over weapi only (a plain GET answers 400);
    # it has no limit parameter, so `limit` is applied locally.
    result = netease_weapi('/v1/play/record', {'uid': uid, 'type': 0 if all_time else 1})
    if result.get('code') != 200:
        return {"error": "Failed to get play history", "detail": _with_hint(result)}
    key = 'allData' if all_time else 'weekData'
    records = (result.get(key) or [])[:limit]
    output = []
    for i, r in enumerate(records, 1):
        song = r.get('song') or {}
        artists = ', '.join(a.get('name', '?') for a in song.get('ar') or [])
        score = r.get('score', 0)
        output.append(f"{i}. {song.get('name', '?')} - {artists} (plays: {score}, ID:{song.get('id')})")
    return {"history": output}

def get_recent_plays(params):
    """Get actual recent play events with timestamps."""
    limit = max(1, min(int(params.get('limit', 100)), 300))
    # `limit` must be in the (encrypted) request body; in the URL the server ignores it
    # and returns its default page, so the result is also trimmed locally.
    result = read_call('/play-record/song/list', {'limit': limit},
                       legacy_get=f'/api/play-record/song/list?limit={limit}')
    if result.get('code') != 200:
        return {"error": "Failed to get recent plays", "detail": result}
    records = ((result.get('data') or {}).get('list') or [])[:limit]
    output = []
    without_time = 0
    for i, r in enumerate(records, 1):
        song = r.get('data') or {}
        artists = ', '.join(a.get('name', '?') for a in song.get('ar') or [])
        play_time = next((r[k] for k in ('playTime', 'time', 'updateTime', 'lastPlayTime') if r.get(k)), None)
        if play_time is None:
            without_time += 1
        output.append(f"{i}. {song.get('name', '?')} - {artists} (ID:{song.get('id')}) [{_fmt_time(play_time)}]")
    out = {"recent_plays": output}
    if records and without_time == len(records):
        # None of the known timestamp fields exist: report the real field names so it can be fixed
        out["debug_record_keys"] = sorted(records[0].keys())
    return out

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
    # weapi first (the plain /api/ route is refused since ~Oct 2026), legacy route as fallback
    result = write_call('/playlist/create', {'name': name, 'privacy': str(privacy), 'type': 'NORMAL'})
    if result.get('code') != 200:
        return {"error": "Failed to create playlist", "detail": result}
    pid = result.get('id') or (result.get('playlist') or {}).get('id')
    # Set description if provided
    desc_status = ""
    if desc and pid:
        desc_result = write_call('/playlist/desc/update', {'id': str(pid), 'desc': desc})
        if desc_result.get('code') == 200:
            desc_status = " | Description: set"
        else:
            desc_status = f" | Description: failed (code {desc_result.get('code')})"
    return {"playlist_id": pid, "name": name, "status": f"created{desc_status}"}

def add_to_playlist(params):
    """Add songs to a playlist."""
    pid = params.get('playlist_id')
    song_ids = params.get('song_ids', '')
    if not pid or not song_ids:
        return {"error": "playlist_id and song_ids required"}
    ids = [s.strip() for s in str(song_ids).split(',') if s.strip()]
    if not ids:
        return {"error": "song_ids is empty"}
    result = write_call('/playlist/manipulate/tracks',
                        {'pid': str(pid), 'trackIds': json.dumps(ids), 'op': 'add', 'imme': 'true'})
    if result.get('code') != 200:
        return {"error": "Failed to add songs", "detail": result}
    return {"status": f"Added {len(ids)} song(s) to playlist {pid}"}

def remove_from_playlist(params):
    """Remove songs from a playlist."""
    pid = params.get('playlist_id')
    song_ids = params.get('song_ids', '')
    if not pid or not song_ids:
        return {"error": "playlist_id and song_ids required"}
    ids = [s.strip() for s in str(song_ids).split(',') if s.strip()]
    if not ids:
        return {"error": "song_ids is empty"}
    result = write_call('/playlist/manipulate/tracks',
                        {'pid': str(pid), 'trackIds': json.dumps(ids), 'op': 'del', 'imme': 'true'})
    if result.get('code') != 200:
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
    result = write_call('/playlist/desc/update', {'id': str(pid), 'desc': desc})
    if result.get('code') != 200:
        return {"error": "Failed to update description", "detail": result}
    return {"status": f"Updated description for playlist {pid}"}

def reorder_playlist_tracks(params):
    """Reorder all tracks in a playlist."""
    pid = params.get('playlist_id')
    song_ids = params.get('song_ids', '')
    if not pid or not song_ids:
        return {"error": "playlist_id and song_ids required"}
    ids = [int(s.strip()) for s in str(song_ids).split(',') if s.strip()]
    # Get current order
    detail = netease_request(f'/api/v6/playlist/detail?id={pid}', method='GET')
    if not detail or detail.get('code') != 200:
        return {"error": "Failed to get playlist detail"}
    current_ids = [t['id'] for t in detail.get('playlist', {}).get('tracks', [])]
    if current_ids == ids:
        return {"status": "Playlist is already in the requested order."}
    result = write_call('/playlist/manipulate/tracks',
                        {'pid': str(pid), 'trackIds': json.dumps(ids), 'op': 'update', 'imme': 'true'})
    if result.get('code') != 200:
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
    result = read_call('/v1/radio/get', {}, legacy_get='/api/v1/radio/get')
    if result.get('code') != 200:
        return {"error": "Failed to get personal FM", "detail": result}
    output, seen = [], set()
    for s in result.get('data') or []:
        if s.get('id') in seen:  # the endpoint can repeat a track within one batch
            continue
        seen.add(s.get('id'))
        artists = ', '.join(a.get('name', '?') for a in s.get('artists') or s.get('ar') or [])
        album = (s.get('album') or s.get('al') or {}).get('name', '?')
        output.append(f"{len(output) + 1}. {s.get('name', '?')} - {artists} | Album: {album} (ID:{s.get('id')})")
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
            self._json_response({"status": "ok", "tools": len(TOOLS), "version": VERSION})
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
                      "serverInfo": {"name": "netease-music-mcp", "version": VERSION}}
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
                    if isinstance(tool_result, dict) and "error" in tool_result:
                        result["isError"] = True
                except Exception as e:
                    logger.error(f"Tool error [{tool_name}]: {e}")
                    result = {"content": [{"type": "text", "text": json.dumps({"error": str(e)})}], "isError": True}
            else:
                result = {"content": [{"type": "text", "text": f"Unknown tool: {tool_name}"}], "isError": True}
        else:
            self._json_response({"jsonrpc": "2.0", "id": req_id,
                                 "error": {"code": -32601, "message": f"Unknown method: {method}"}})
            return
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

def _startup_checks():
    """Log which credentials were found (key names only, never values)."""
    jar = _parse_cookie(NETEASE_COOKIE)
    logger.info("Cookie keys provided: " + (", ".join(sorted(jar)) or "(none)"))
    if 'MUSIC_U' not in jar:
        logger.warning("No MUSIC_U in NETEASE_COOKIE: requests are anonymous and account calls will fail")
    if not get_csrf():
        logger.warning("No __csrf (in NETEASE_COOKIE or NETEASE_CSRF): write operations will fail with 403")

if __name__ == '__main__':
    logger.info(f"Starting NetEase Music MCP Server v{VERSION} with {len(TOOLS)} tools on port {PORT}")
    _startup_checks()
    server = ThreadedHTTPServer(('0.0.0.0', PORT), MCPHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Server shutting down")
        server.shutdown()
