#!/usr/bin/env python3
"""NetEase Cloud Music MCP Server - Pure Python, zero dependencies.

A lightweight MCP server that connects AI assistants to NetEase Cloud Music
for playlist management, music discovery, and listening history analysis.

GitHub: https://github.com/Vael-KY/netease-music-mcp
License: MIT
"""
import http.server, json, os, sys, urllib.request, urllib.parse, urllib.error, threading, uuid, time, logging
import base64, hashlib, hmac, queue, secrets, string, socketserver
from collections import Counter
from http.server import HTTPServer

VERSION = "3.3.0"

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
HOST = os.environ.get("MCP_HOST", "0.0.0.0")
# Optional shared secret. When set, every MCP request needs `Authorization: Bearer <token>`.
AUTH_TOKEN = os.environ.get("MCP_AUTH_TOKEN", "").strip()
# Browser origins allowed to call the server (comma-separated, exact match such as
# https://chat.example.com). Requests without an Origin header (CLI clients, servers)
# are not affected; localhost origins are always allowed.
ALLOWED_ORIGINS = {o.strip().rstrip('/') for o in os.environ.get("MCP_ALLOWED_ORIGINS", "").split(',') if o.strip()}
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
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

_COOKIE_OVERRIDE = threading.local()

def current_cookie():
    """The account cookie in use. Normally NETEASE_COOKIE; inside `using_cookie(...)` it is
    that cookie instead, for this thread only (the Listen Together extension uses this to
    act as a second account without touching the normal tools)."""
    override = getattr(_COOKIE_OVERRIDE, 'value', None)
    return NETEASE_COOKIE if override is None else override

def _csrf_fallback():
    # NETEASE_CSRF belongs to NETEASE_COOKIE's account; never mix it into another account
    return NETEASE_CSRF if getattr(_COOKIE_OVERRIDE, 'value', None) is None else ''

class using_cookie:
    """`with using_cookie(c): ...` makes every request in the block use cookie `c`."""
    def __init__(self, cookie):
        self.cookie = cookie
    def __enter__(self):
        self.previous = getattr(_COOKIE_OVERRIDE, 'value', None)
        _COOKIE_OVERRIDE.value = self.cookie
        return self
    def __exit__(self, *exc):
        _COOKIE_OVERRIDE.value = self.previous
        return False

def build_cookie_header():
    jar = _parse_cookie(current_cookie())
    csrf = _csrf_fallback()
    if csrf and '__csrf' not in jar:
        jar['__csrf'] = csrf  # the csrf_token we send must also be present as a cookie
    for key, value in _device_cookies().items():
        jar.setdefault(key, value)
    return '; '.join(f'{k}={v}' for k, v in jar.items())

def get_csrf():
    return _parse_cookie(current_cookie()).get('__csrf') or _csrf_fallback() or ''

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

# --- eapi (the mobile/desktop client format; used by Listen Together and private messages) ---
_EAPI_KEY = b'e82ckenh8dichen8'
_EAPI_UA = 'NeteaseMusic 9.0.90/5038 (iPhone; iOS 16.2; zh_CN)'
_EAPI_DEVICE_ID = secrets.token_hex(26).upper()

def _aes_ecb_encrypt(data, key):
    pad = 16 - len(data) % 16
    data = data + bytes([pad]) * pad
    rks = _expand_key(key)
    out = bytearray()
    for i in range(0, len(data), 16):
        out += bytes(_encrypt_block(list(data[i:i + 16]), rks))
    return bytes(out)

def eapi_encrypt(api_path, payload):
    """Encrypt a dict for an eapi endpoint. api_path is the '/api/...' form of the route."""
    text = json.dumps(payload, ensure_ascii=False, separators=(',', ':'))
    digest = hashlib.md5(f'nobody{api_path}use{text}md5forencrypt'.encode()).hexdigest()
    data = f'{api_path}-36cd479b6b5-{text}-36cd479b6b5-{digest}'.encode()
    return {'params': _aes_ecb_encrypt(data, _EAPI_KEY).hex().upper()}

def netease_eapi(api_path, payload=None):
    """Make an eapi request. api_path like '/api/listen/together/heartbeat'."""
    jar = _parse_cookie(current_cookie())
    device = _device_cookies()
    now_ms = int(time.time() * 1000)
    header = {
        'osver': jar.get('osver', device['osver']), 'deviceId': _EAPI_DEVICE_ID,
        'os': jar.get('os', device['os']), 'appver': jar.get('appver', device['appver']),
        'versioncode': '140', 'mobilename': '', 'buildver': str(now_ms)[:10],
        'resolution': '1920x1080', '__csrf': get_csrf(), 'channel': jar.get('channel', device['channel']),
        'requestId': f'{now_ms}_{secrets.randbelow(1000):04d}',
    }
    if jar.get('MUSIC_U'):
        header['MUSIC_U'] = jar['MUSIC_U']
    body = dict(payload or {}, header=header, e_r=False)
    url = 'https://interface.music.163.com/eapi/' + api_path[len('/api/'):]
    headers = {
        'Cookie': '; '.join(f'{urllib.parse.quote(k)}={urllib.parse.quote(str(v))}' for k, v in header.items()),
        'Content-Type': 'application/x-www-form-urlencoded',
        'User-Agent': _EAPI_UA,
    }
    try:
        data = urllib.parse.urlencode(eapi_encrypt(api_path, body)).encode()
        req = urllib.request.Request(url, data=data, headers=headers, method='POST')
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return _http_error_result(f'/eapi{api_path[4:]}', e)
    except Exception as e:
        logger.error("NetEase eapi error [%s]: %s", api_path, type(e).__name__)
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
    records = _play_records(result, all_time)[:limit]
    output = []
    for i, r in enumerate(records, 1):
        song = r.get('song') or {}
        artists = ', '.join(a.get('name', '?') for a in song.get('ar') or [])
        # playCount is the real number of plays; score is NetEase's 0-100 ranking weight
        output.append(f"{i}. {song.get('name', '?')} - {artists} (plays: {r.get('playCount', 0)}, ID:{song.get('id')})")
    return {"history": output}

def _play_records(result, all_time):
    return result.get('allData' if all_time else 'weekData') or []

def get_listening_profile(params):
    """Summarise listening taste from the play-history rankings."""
    uid = get_uid()
    if not uid:
        return {"error": "Cannot determine user ID (MUSIC_U missing or expired?)"}
    top_n = max(1, min(int(params.get('top', 10)), 30))
    profile = {}
    for label, all_time in (('this_week', False), ('all_time', True)):
        result = netease_weapi('/v1/play/record', {'uid': uid, 'type': 0 if all_time else 1})
        if result.get('code') != 200:
            profile[label] = {"error": "Failed to get play history", "detail": _with_hint(result)}
            continue
        profile[label] = summarise_play_records(_play_records(result, all_time), top_n)
    return profile

def summarise_play_records(records, top_n=10):
    """Aggregate play-history records into top artists, albums and songs."""
    artists, albums = Counter(), Counter()
    songs = []
    total = 0
    for r in records:
        song = r.get('song') or {}
        plays = int(r.get('playCount') or 0)
        total += plays
        for a in song.get('ar') or []:
            if a.get('name'):
                artists[a['name']] += plays
        album = (song.get('al') or {}).get('name')
        if album:
            albums[album] += plays
        names = ', '.join(a.get('name', '?') for a in song.get('ar') or [])
        songs.append((plays, f"{song.get('name', '?')} - {names}"))
    songs.sort(key=lambda item: -item[0])
    return {
        "songs_counted": len(records), "total_plays": total,
        "top_artists": [f"{name} ({n} plays)" for name, n in artists.most_common(top_n)],
        "top_albums": [f"{name} ({n} plays)" for name, n in albums.most_common(top_n)],
        "top_songs": [f"{title} ({n} plays)" for n, title in songs[:top_n]],
    }

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
    result = netease_request(f'/api/user/playlist?uid={uid}&limit=1000&offset=0', method='GET')
    if not result or result.get('code') != 200:
        return {"error": "Failed to get playlists"}
    playlists = result.get('playlist', [])
    output = []
    for p in playlists:
        owner = "mine" if p.get('creator', {}).get('userId') == uid else "collected"
        desc = f" | {p.get('description', '')[:30]}" if p.get('description') else ""
        output.append(f"ID:{p['id']} | {p['name']} | {p.get('trackCount', 0)} songs ({owner}){desc}")
    return {"playlists": output}

def _playlist_track_ids(playlist):
    """Full ordered track ids. `tracks` can be truncated on big playlists; `trackIds` is not."""
    ids = [t.get('id') for t in playlist.get('trackIds') or [] if t.get('id')]
    return ids or [t.get('id') for t in playlist.get('tracks') or [] if t.get('id')]

def _song_details(ids):
    """Song detail dicts for ids (order kept), fetched in batches."""
    by_id = {}
    for start in range(0, len(ids), 500):
        chunk = ids[start:start + 500]
        result = netease_request('/api/v3/song/detail', data={'c': json.dumps([{"id": int(i)} for i in chunk])})
        if result.get('code') != 200:
            break
        for s in result.get('songs') or []:
            by_id[s.get('id')] = s
    return [by_id[i] for i in ids if i in by_id]

def get_playlist_songs(params):
    """Get the songs in a playlist (up to `limit`, default 1000)."""
    pid = params.get('playlist_id')
    if not pid:
        return {"error": "playlist_id required"}
    limit = max(1, min(int(params.get('limit', 1000)), 5000))
    result = netease_request(f'/api/v6/playlist/detail?id={pid}', method='GET')
    if not result or result.get('code') != 200:
        return {"error": "Failed to get playlist", "detail": result}
    playlist = result.get('playlist', {})
    ids = _playlist_track_ids(playlist)
    tracks = playlist.get('tracks') or []
    if len(tracks) < min(len(ids), limit):
        tracks = _song_details(ids[:limit]) or tracks
    output = []
    for i, t in enumerate(tracks[:limit], 1):
        artists = ', '.join(a['name'] for a in t.get('ar', []))
        output.append(f"{i}. {t['name']} - {artists} (ID:{t['id']})")
    out = {"name": playlist.get('name'), "total": len(ids), "songs": output}
    if len(ids) > len(output):
        out["note"] = f"Showing {len(output)} of {len(ids)} songs"
    return out

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

def check_reorder(current_ids, new_ids):
    """A reorder must contain exactly the playlist's songs, once each; otherwise refuse,
    because NetEase writes whatever list it gets and would drop or add songs."""
    if len(set(new_ids)) != len(new_ids):
        dupes = sorted({i for i in new_ids if new_ids.count(i) > 1})
        return {"error": "song_ids contains duplicates", "duplicates": dupes}
    missing = [i for i in current_ids if i not in set(new_ids)]
    extra = [i for i in new_ids if i not in set(current_ids)]
    if missing or extra:
        return {"error": "song_ids must list every song in the playlist exactly once",
                "missing": missing[:50], "not_in_playlist": extra[:50],
                "expected_count": len(current_ids), "given_count": len(new_ids)}
    return None

def reorder_playlist_tracks(params):
    """Reorder all tracks in a playlist."""
    pid = params.get('playlist_id')
    song_ids = params.get('song_ids', '')
    if not pid or not song_ids:
        return {"error": "playlist_id and song_ids required"}
    try:
        ids = [int(s.strip()) for s in str(song_ids).split(',') if s.strip()]
    except ValueError:
        return {"error": "song_ids must be comma-separated numbers"}
    # Get current order
    detail = netease_request(f'/api/v6/playlist/detail?id={pid}', method='GET')
    if not detail or detail.get('code') != 200:
        return {"error": "Failed to get playlist detail"}
    current_ids = _playlist_track_ids(detail.get('playlist', {}))
    problem = check_reorder(current_ids, ids)
    if problem:
        return problem
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

def account_status(params):
    """Is the cookie still logged in, and as whom."""
    jar = _parse_cookie(current_cookie())
    out = {"has_MUSIC_U": 'MUSIC_U' in jar, "has_csrf": bool(get_csrf())}
    result = netease_weapi('/nuser/account/get', {})
    if result.get('code') != 200:
        return dict(out, logged_in=False, error="Account check failed", detail=_with_hint(result))
    account, profile = result.get('account') or {}, result.get('profile') or {}
    if not account.get('id') or not profile:
        return dict(out, logged_in=False,
                    hint="Not logged in or session expired: refresh MUSIC_U / __csrf in NETEASE_COOKIE")
    return dict(out, logged_in=True, uid=account.get('id'), nickname=profile.get('nickname'),
                vip_type=account.get('vipType'))

def _fmt_comment(c):
    user = (c.get('user') or {}).get('nickname', '?')
    return {"user": user, "content": c.get('content', ''), "likes": c.get('likedCount', 0),
            "time": _fmt_time(c.get('time'))}

def get_song_comments(params):
    """Hot (or latest) comments of a song."""
    song_id = params.get('song_id')
    if not song_id:
        return {"error": "song_id required"}
    limit = max(1, min(int(params.get('limit', 10)), 50))
    hot = str(params.get('hot', 'true')).lower() != 'false'
    kind = 'hotcomments' if hot else 'comments'
    result = netease_request(f'/api/v1/resource/{kind}/R_SO_4_{int(song_id)}?limit={limit}&offset=0', method='GET')
    if result.get('code') != 200:
        return {"error": "Failed to get comments", "detail": _with_hint(result)}
    comments = result.get('hotComments') if hot else result.get('comments')
    return {"song_id": song_id, "total": result.get('total'),
            "comments": [_fmt_comment(c) for c in (comments or [])[:limit]]}

def get_similar_songs(params):
    """Songs NetEase considers similar to the given one."""
    song_id = params.get('song_id')
    if not song_id:
        return {"error": "song_id required"}
    result = netease_request(f'/api/v1/discovery/simiSong?songid={int(song_id)}&limit=10&offset=0', method='GET')
    if result.get('code') != 200:
        return {"error": "Failed to get similar songs", "detail": _with_hint(result)}
    output = []
    for i, s in enumerate(result.get('songs') or [], 1):
        artists = ', '.join(a.get('name', '?') for a in s.get('artists') or s.get('ar') or [])
        output.append(f"{i}. {s.get('name', '?')} - {artists} (ID:{s.get('id')})")
    return {"similar": output}

def _private_text(raw):
    """A private message's `msg` is JSON; pull out readable text and any song/invite."""
    try:
        body = json.loads(raw) if isinstance(raw, str) else (raw or {})
    except ValueError:
        return raw
    if not isinstance(body, dict):
        return str(body)
    text = body.get('msg') or ''
    song = body.get('song') or {}
    if song.get('name'):
        text = (text + ' ' if text else '') + f"[song: {song['name']} (ID:{song.get('id')})]"
    if not text and 'listenTogether' in json.dumps(body, ensure_ascii=False):
        text = '[Listen Together invitation]'
    return text or body.get('title') or '[non-text message]'

def get_private_messages(params):
    """Recent private-message conversations, or the history with one user."""
    user_id = params.get('user_id')
    limit = max(1, min(int(params.get('limit', 20)), 100))
    if not user_id:
        result = netease_weapi('/msg/private/users', {'offset': 0, 'limit': limit, 'total': 'true'})
        if result.get('code') != 200:
            return {"error": "Failed to get conversations", "detail": _with_hint(result)}
        out = []
        for m in result.get('msgs') or []:
            peer = m.get('fromUser') or {}
            out.append({"user_id": peer.get('userId'), "nickname": peer.get('nickname'),
                        "last": _private_text(m.get('lastMsg')), "time": _fmt_time(m.get('lastMsgTime')),
                        "unread": m.get('newMsgCount', 0)})
        return {"conversations": out}
    result = netease_weapi('/msg/private/history', {'userId': str(user_id), 'limit': limit, 'time': 0, 'total': 'true'})
    if result.get('code') != 200:
        return {"error": "Failed to get messages", "detail": _with_hint(result)}
    out = []
    for m in sorted(result.get('msgs') or [], key=lambda m: m.get('time') or 0):
        sender = (m.get('fromUser') or {})
        out.append({"from": sender.get('nickname') or sender.get('userId'), "text": _private_text(m.get('msg')),
                    "time": _fmt_time(m.get('time'))})
    return {"user_id": user_id, "messages": out}

def send_private_message(params):
    """Send a text private message to one user."""
    user_id, text = params.get('user_id'), str(params.get('text', '')).strip()
    if not user_id or not text:
        return {"error": "user_id and text required"}
    if len(text) > 500:
        return {"error": "text is longer than 500 characters"}
    result = netease_eapi('/api/msg/private/send', {'type': 'text', 'msg': text, 'userIds': f'[{int(user_id)}]'})
    if result.get('code') != 200:
        return {"error": "Failed to send message", "detail": _with_hint(result)}
    return {"status": f"Sent to {user_id}"}



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
    {"name": "get_playlist_songs", "description": "Get the songs in a playlist, in order (big playlists included).",
     "inputSchema": {"type": "object", "properties": {"playlist_id": {"type": "integer", "description": "Playlist ID"}, "limit": {"type": "integer", "description": "Max songs (default 1000, max 5000)"}}, "required": ["playlist_id"]}},
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
    {"name": "account_status", "description": "Check whether the NetEase cookie is still logged in, and as which account.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "get_listening_profile", "description": "Summarise listening taste: top artists, albums and songs this week and of all time.",
     "inputSchema": {"type": "object", "properties": {"top": {"type": "integer", "description": "How many of each to list (1-30, default 10)"}}}},
    {"name": "get_song_comments", "description": "Read a song's hot comments (or the latest ones).",
     "inputSchema": {"type": "object", "properties": {"song_id": {"type": "integer", "description": "Song ID"}, "hot": {"type": "boolean", "description": "true=hot comments (default), false=latest"}, "limit": {"type": "integer", "description": "Number of comments (1-50, default 10)"}}, "required": ["song_id"]}},
    {"name": "get_similar_songs", "description": "Find songs similar to a given song.",
     "inputSchema": {"type": "object", "properties": {"song_id": {"type": "integer", "description": "Song ID"}}, "required": ["song_id"]}},
    {"name": "get_private_messages", "description": "List recent private-message conversations, or read the messages with one user.",
     "inputSchema": {"type": "object", "properties": {"user_id": {"type": "integer", "description": "Other user's ID; omit to list conversations"}, "limit": {"type": "integer", "description": "Max items (1-100, default 20)"}}}},
    {"name": "send_private_message", "description": "Send a text private message to a NetEase user. Only send when the account owner asked for it.",
     "inputSchema": {"type": "object", "properties": {"user_id": {"type": "integer", "description": "Recipient user ID"}, "text": {"type": "string", "description": "Message text (max 500 characters)"}}, "required": ["user_id", "text"]}},
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
    "account_status": account_status,
    "get_listening_profile": get_listening_profile,
    "get_song_comments": get_song_comments,
    "get_similar_songs": get_similar_songs,
    "get_private_messages": get_private_messages,
    "send_private_message": send_private_message,
}

# --- MCP protocol ---
# Streamable HTTP (POST /mcp) is the main transport. The older HTTP+SSE pair
# (GET /sse + POST /message) is kept for clients that still use it.
SUPPORTED_PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
LATEST_PROTOCOL_VERSION = SUPPORTED_PROTOCOL_VERSIONS[0]
MAX_BODY_BYTES = 1 << 20

_sessions = {}          # Mcp-Session-Id -> {"version": str, "created": float}
_sse_sessions = {}      # legacy sessionId -> queue.Queue of outgoing messages
_sessions_lock = threading.Lock()

def _rpc_error(req_id, code, message):
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}

def negotiate_version(requested):
    return requested if requested in SUPPORTED_PROTOCOL_VERSIONS else LATEST_PROTOCOL_VERSION

def call_tool(name, arguments):
    """Run one tool and wrap its dict result as MCP tool content."""
    handler = TOOL_DISPATCH[name]
    logger.info(f"Tool call: {name}")
    try:
        tool_result = handler(arguments)
    except Exception as e:
        logger.error(f"Tool error [{name}]: {type(e).__name__}: {e}")
        return {"content": [{"type": "text", "text": json.dumps({"error": str(e)}, ensure_ascii=False)}], "isError": True}
    result = {"content": [{"type": "text", "text": json.dumps(tool_result, ensure_ascii=False)}]}
    if isinstance(tool_result, dict) and "error" in tool_result:
        result["isError"] = True
    return result

def handle_rpc(message):
    """Handle one JSON-RPC message. Returns the response dict, or None for notifications/responses."""
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
        return _rpc_error(None, -32600, "Invalid Request")
    if "method" not in message:
        return None  # a response to something we never asked; nothing to answer
    method, req_id = message.get("method"), message.get("id")
    if "id" not in message:
        return None  # notification (notifications/initialized, notifications/cancelled, ...)
    params = message.get("params") or {}
    if not isinstance(params, dict):
        return _rpc_error(req_id, -32602, "params must be an object")
    if method == "initialize":
        result = {"protocolVersion": negotiate_version(params.get("protocolVersion")),
                  "capabilities": {"tools": {"listChanged": False}},
                  "serverInfo": {"name": "netease-music-mcp", "version": VERSION}}
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        name, arguments = params.get("name", ""), params.get("arguments") or {}
        if not isinstance(arguments, dict):
            return _rpc_error(req_id, -32602, "arguments must be an object")
        if name not in TOOL_DISPATCH:
            return _rpc_error(req_id, -32602, f"Unknown tool: {name}")
        result = call_tool(name, arguments)
    else:
        return _rpc_error(req_id, -32601, f"Method not found: {method}")
    return {"jsonrpc": "2.0", "id": req_id, "result": result}

def handle_payload(payload):
    """A single message or a batch. Returns (response or None, negotiated version if it held an initialize)."""
    if isinstance(payload, list) and not payload:
        return _rpc_error(None, -32600, "Empty batch"), None
    messages = payload if isinstance(payload, list) else [payload]
    init_version = None
    for m in messages:
        if isinstance(m, dict) and m.get("method") == "initialize" and "id" in m:
            init_version = negotiate_version((m.get("params") or {}).get("protocolVersion"))
    responses = [r for r in (handle_rpc(m) for m in messages) if r is not None]
    if not responses:
        return None, init_version
    return (responses if isinstance(payload, list) else responses[0]), init_version

def origin_allowed(origin):
    """No Origin header = not a browser (allowed). Browsers: localhost or MCP_ALLOWED_ORIGINS only,
    so a random web page cannot drive the logged-in account (DNS rebinding / CSRF)."""
    if not origin:
        return True
    origin = origin.rstrip('/')
    if origin in ALLOWED_ORIGINS or '*' in ALLOWED_ORIGINS:
        return True
    host = urllib.parse.urlparse(origin).hostname or ''
    return host in ('localhost', '127.0.0.1', '::1')

def token_ok(header):
    if not AUTH_TOKEN:
        return True
    scheme, _, value = (header or '').partition(' ')
    return scheme.lower() == 'bearer' and hmac.compare_digest(value.strip().encode(), AUTH_TOKEN.encode())

class MCPHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    # -- plumbing --
    def _cors(self):
        origin = self.headers.get('Origin')
        if origin and origin_allowed(origin):
            self.send_header('Access-Control-Allow-Origin', origin)
            self.send_header('Vary', 'Origin')
            self.send_header('Access-Control-Allow-Methods', 'GET, POST, DELETE, OPTIONS')
            self.send_header('Access-Control-Allow-Headers',
                             'Content-Type, Authorization, Mcp-Session-Id, MCP-Protocol-Version, Last-Event-ID')
            self.send_header('Access-Control-Expose-Headers', 'Mcp-Session-Id')

    def _send(self, status, data=None, headers=None):
        body = b'' if data is None else json.dumps(data, ensure_ascii=False).encode()
        if status >= 400:
            # The request body may be unread; do not let it leak into a next keep-alive request.
            self.close_connection = True
        self.send_response(status)
        self._cors()
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        if data is not None:
            self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        if self.close_connection:
            self.send_header('Connection', 'close')
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _path(self):
        return urllib.parse.urlparse(self.path).path.rstrip('/') or '/'

    def _guard(self):
        """Origin + auth checks shared by every MCP route. Returns True if the request may go on."""
        if not origin_allowed(self.headers.get('Origin')):
            self._send(403, {"error": "Origin not allowed"})
            return False
        if not token_ok(self.headers.get('Authorization')):
            self._send(401, {"error": "Unauthorized"}, {'WWW-Authenticate': 'Bearer'})
            return False
        return True

    def _read_json(self):
        try:
            length = int(self.headers.get('Content-Length') or 0)
        except ValueError:
            length = -1
        if length < 0 or length > MAX_BODY_BYTES:
            self._send(413, _rpc_error(None, -32600, "Body too large"))
            return None, False
        try:
            return json.loads(self.rfile.read(length) or b'null'), True
        except ValueError:
            self._send(400, _rpc_error(None, -32700, "Parse error"))
            return None, False

    # -- routes --
    def do_OPTIONS(self):
        self._send(204 if origin_allowed(self.headers.get('Origin')) else 403)

    def do_GET(self):
        path = self._path()
        if path == '/health':
            return self._send(200, {"status": "ok", "tools": len(TOOLS), "version": VERSION})
        if not self._guard():
            return
        if path == '/mcp':
            # This server never pushes unrequested messages, so it offers no GET stream.
            return self._send(405, {"error": "Method not allowed"}, {'Allow': 'POST, DELETE'})
        if path == '/sse':
            return self._handle_sse()
        self._send(404, {"error": "Not found"})

    def do_DELETE(self):
        if not self._guard():
            return
        if self._path() != '/mcp':
            return self._send(404, {"error": "Not found"})
        sid = self.headers.get('Mcp-Session-Id')
        with _sessions_lock:
            known = _sessions.pop(sid, None) if sid else None
        self._send(204 if known else 404)

    def do_POST(self):
        path = self._path()
        if path not in ('/mcp', '/message'):
            return self._send(404, {"error": "Not found"})
        if not self._guard():
            return
        if path == '/message':
            return self._handle_legacy_post()
        version = self.headers.get('MCP-Protocol-Version')
        if version and version not in SUPPORTED_PROTOCOL_VERSIONS:
            return self._send(400, _rpc_error(None, -32600, f"Unsupported MCP-Protocol-Version: {version}"))
        sid = self.headers.get('Mcp-Session-Id')
        if sid:
            with _sessions_lock:
                known = sid in _sessions
            if not known:
                return self._send(404, _rpc_error(None, -32001, "Session not found"))
        payload, ok = self._read_json()
        if not ok:
            return
        response, init_version = handle_payload(payload)
        headers = {}
        if init_version:
            sid = str(uuid.uuid4())
            with _sessions_lock:
                _sessions[sid] = {"version": init_version, "created": time.time()}
                if len(_sessions) > 1000:  # forget the oldest; that client just re-initializes
                    _sessions.pop(next(iter(_sessions)))
            headers['Mcp-Session-Id'] = sid
        if response is None:
            return self._send(202, None, headers)
        self._send(200, response, headers)

    # -- legacy HTTP+SSE (protocol 2024-11-05) --
    def _handle_sse(self):
        sid = uuid.uuid4().hex
        outbox = queue.Queue()
        with _sessions_lock:
            _sse_sessions[sid] = outbox
        self.send_response(200)
        self._cors()
        self.send_header('Content-Type', 'text/event-stream')
        self.send_header('Cache-Control', 'no-cache')
        self.end_headers()
        self.close_connection = True
        try:
            # A relative URI: the client resolves it against the /sse URL it used.
            self.wfile.write(f"event: endpoint\ndata: /message?sessionId={sid}\n\n".encode())
            self.wfile.flush()
            while True:
                try:
                    msg = outbox.get(timeout=25)
                    self.wfile.write(f"event: message\ndata: {json.dumps(msg, ensure_ascii=False)}\n\n".encode())
                except queue.Empty:
                    self.wfile.write(b": keepalive\n\n")
                self.wfile.flush()
        except OSError:
            pass
        finally:
            with _sessions_lock:
                _sse_sessions.pop(sid, None)

    def _handle_legacy_post(self):
        sid = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).get('sessionId', [''])[0]
        with _sessions_lock:
            outbox = _sse_sessions.get(sid)
        if outbox is None:
            return self._send(404, {"error": "Unknown sessionId"})
        payload, ok = self._read_json()
        if not ok:
            return
        response, _ = handle_payload(payload)
        if response is not None:
            outbox.put(response)
        self._send(202)

    def log_message(self, format, *args):
        pass

class ThreadedHTTPServer(socketserver.ThreadingMixIn, HTTPServer):
    daemon_threads = True

def _startup_checks():
    """Log which credentials were found (key names only, never values)."""
    jar = _parse_cookie(current_cookie())
    logger.info("Cookie keys provided: " + (", ".join(sorted(jar)) or "(none)"))
    if 'MUSIC_U' not in jar:
        logger.warning("No MUSIC_U in NETEASE_COOKIE: requests are anonymous and account calls will fail")
    if not get_csrf():
        logger.warning("No __csrf (in NETEASE_COOKIE or NETEASE_CSRF): write operations will fail with 403")
    if not AUTH_TOKEN and HOST not in ('127.0.0.1', 'localhost', '::1'):
        logger.warning("MCP_AUTH_TOKEN is not set and the server listens on %s: anyone who can reach "
                       "this port can use your NetEase account. Set MCP_AUTH_TOKEN or MCP_HOST=127.0.0.1.", HOST)

def load_extensions():
    """Optional extensions, each switched on by its own env flag. A failing extension is
    logged and skipped; the core tools keep working."""
    if os.environ.get("TOGETHER_ENABLED", "").lower() in ("1", "true", "yes", "on"):
        try:
            import together
            together.setup(sys.modules[__name__])
            logger.info("Listen Together extension loaded")
        except Exception as e:
            logger.error("Listen Together extension not loaded: %s: %s", type(e).__name__, e)

if __name__ == '__main__':
    load_extensions()
    logger.info(f"Starting NetEase Music MCP Server v{VERSION} with {len(TOOLS)} tools on {HOST}:{PORT} (POST /mcp)")
    _startup_checks()
    server = ThreadedHTTPServer((HOST, PORT), MCPHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Server shutting down")
        server.shutdown()
