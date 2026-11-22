"""NetEase endpoints used by Listen Together, built on the core server's request helpers.

Endpoints are the ones the community NeteaseCloudMusicApi project documents
(listentogether_*, msg_*).
"""
import json
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request


class NeteaseApiError(Exception):
    def __init__(self, operation, code, message=None):
        super().__init__(message or f"{operation} failed (code {code})")
        self.operation, self.code = operation, code


_INVISIBLE = dict.fromkeys(map(ord, '​‌‍‎‏‪‫‬‭‮⁦⁧⁨⁩﻿'))


def _clean(text):
    """Names sometimes carry invisible direction marks; drop them."""
    return text.translate(_INVISIBLE).strip() if isinstance(text, str) else text


def _s(value):
    """Ids arrive as int or str; compare them as strings."""
    return None if value is None or value == '' else str(value)


class _AccountCore:
    """The core server's request helpers, but every call made as another account.

    Listen Together is between two accounts: hers invites, the AI's accepts. With
    TOGETHER_COOKIE set, the extension acts as the AI account while the normal tools keep
    using NETEASE_COOKIE (hers)."""
    def __init__(self, core, cookie):
        self._core, self._cookie = core, cookie

    def current_cookie(self):
        return self._cookie

    def __getattr__(self, name):
        attr = getattr(self._core, name)
        if not callable(attr):
            return attr
        def call(*args, **kwargs):
            with self._core.using_cookie(self._cookie):
                return attr(*args, **kwargs)
        return call


class TogetherApi:
    def __init__(self, core, cookie=None):
        self.core = _AccountCore(core, cookie) if cookie else core

    def _ok(self, operation, result):
        if not isinstance(result, dict) or result.get('code') != 200:
            result = result if isinstance(result, dict) else {}
            raise NeteaseApiError(operation, result.get('code'),
                                  result.get('message') or result.get('msg') or result.get('error'))
        return result

    # -- account --
    def account_profile(self):
        r = self._ok('account', self.core.netease_weapi('/nuser/account/get', {}))
        account, profile = r.get('account') or {}, r.get('profile') or {}
        uid = _s(account.get('id') or profile.get('userId'))
        if not uid:
            raise NeteaseApiError('account', 301, 'Not logged in (cookie expired?)')
        return {'id': uid, 'nickname': profile.get('nickname'), 'avatar': profile.get('avatarUrl'),
                'gender': profile.get('gender')}

    # -- room --
    def room_status(self):
        r = self._ok('together_status', self.core.netease_weapi('/listen/together/status/get', {}))
        data = r.get('data') or {}
        info = data.get('roomInfo') or r.get('roomInfo') or {}
        return {'in_room': data.get('inRoom') is True or r.get('inRoom') is True,
                'room_id': _s(info.get('roomId') or data.get('roomId')),
                'chat_room_id': _s(info.get('chatRoomId') or data.get('chatRoomId'))}

    def accept_invite(self, room_id, inviter_id):
        r = self._ok('together_accept', self.core.netease_eapi(
            '/api/listen/together/play/invitation/accept',
            {'refer': 'inbox_invite', 'roomId': room_id, 'inviterId': inviter_id}))
        info = (r.get('data') or {}).get('roomInfo') or {}
        return {'room_id': _s(info.get('roomId')) or room_id, 'chat_room_id': _s(info.get('chatRoomId'))}

    def end_room(self, room_id):
        self._ok('together_end', self.core.netease_eapi('/api/listen/together/end/v2', {'roomId': room_id}))

    def heartbeat(self, room_id, song_id, playing, progress_ms):
        self._ok('together_heartbeat', self.core.netease_eapi('/api/listen/together/heartbeat', {
            'roomId': room_id, 'songId': song_id or '0', 'playStatus': bool(playing),
            'progress': int(max(0, progress_ms))}))

    def sync_state(self, room_id):
        """Current play command and queue of the room."""
        r = self._ok('together_playlist', self.core.netease_eapi(
            '/api/listen/together/sync/playlist/get', {'roomId': room_id}))
        data = r.get('data') or {}
        cmd = data.get('playCommand') or {}
        status = str(cmd.get('playStatus') or '').upper()
        playlist = data.get('playlist') or {}
        versions = [{'userId': _s(v.get('userId')), 'version': int(v.get('version') or 0)}
                    for v in playlist.get('version') or [] if isinstance(v, dict) and v.get('userId') is not None]
        return {
            'playing': {'song_id': _s(cmd.get('targetSongId')),
                        'status': status if status in ('PLAY', 'PAUSE') else 'UNKNOWN',
                        'progress': max(0, int(cmd.get('progress') or 0)),
                        'server_seq': cmd.get('serverSeq')},
            'display': [_s(i) for i in (playlist.get('displayList') or {}).get('result') or [] if i is not None],
            'random': [_s(i) for i in (playlist.get('randomList') or {}).get('result') or [] if i is not None],
            'play_mode': playlist.get('playMode'),
            'versions': versions,
        }

    def play_command(self, room_id, command_type, progress_ms, play_status, former_song_id, target_song_id, client_seq):
        info = json.dumps({'commandType': command_type, 'progress': int(max(0, progress_ms)),
                           'playStatus': play_status, 'formerSongId': former_song_id,
                           'targetSongId': target_song_id, 'clientSeq': client_seq}, separators=(',', ':'))
        self._ok('together_command', self.core.netease_eapi(
            '/api/listen/together/play/command/report', {'roomId': room_id, 'commandInfo': info}))

    def replace_queue(self, room_id, user_id, version, display, random_list):
        param = json.dumps({'commandType': 'REPLACE', 'version': [{'userId': user_id, 'version': version}],
                            'anchorSongId': '', 'anchorPosition': -1,
                            'randomList': random_list, 'displayList': display}, separators=(',', ':'))
        self._ok('together_queue', self.core.netease_eapi(
            '/api/listen/together/sync/list/command/report', {'roomId': room_id, 'playlistParam': param}))

    # -- invites arrive as private messages (type 23) --
    def recent_contacts(self):
        return self._ok('recent_contacts', self.core.netease_weapi('/msg/recentcontact/get', {}))

    def private_history(self, uid, limit=8):
        return self._ok('private_history', self.core.netease_weapi(
            '/msg/private/history', {'userId': str(uid), 'limit': limit, 'time': 0, 'total': 'true'}))

    # -- songs --
    def song(self, song_id):
        r = self.core.netease_request('/api/v3/song/detail', data={'c': json.dumps([{'id': int(song_id)}])})
        songs = r.get('songs') or [] if isinstance(r, dict) else []
        if not songs:
            return {'id': str(song_id), 'name': f'歌曲 {song_id}', 'artist': '未知歌手', 'album': None, 'duration_ms': 0}
        s = songs[0]
        return {'id': str(s.get('id')), 'name': _clean(s.get('name')) or f'歌曲 {song_id}',
                'artist': ' / '.join(_clean(a.get('name')) or '?' for a in s.get('ar') or []) or '未知歌手',
                'album': _clean((s.get('al') or {}).get('name')), 'duration_ms': int(s.get('dt') or 0)}

    def song_info(self, song_id):
        """NetEase's own notes on the song (style, tags, BPM, language, chorus). Best effort: {} on failure."""
        from .songinfo import parse_chorus, parse_wiki
        info = {}
        try:
            info = parse_wiki(self.core.netease_eapi('/api/song/play/about/block/page', {'songId': str(song_id)}))
        except Exception:
            pass
        try:
            chorus = parse_chorus(self.core.netease_eapi('/api/song/chorus', {'ids': json.dumps([str(song_id)])}), song_id)
            if chorus:
                info['chorus'] = chorus
        except Exception:
            pass
        return info

    def lyrics(self, song_id):
        r = self.core.netease_request(f'/api/song/lyric?id={int(song_id)}&lv=1&tv=1', method='GET')
        if not isinstance(r, dict) or r.get('code') != 200:
            return None, None
        return (r.get('lrc') or {}).get('lyric'), (r.get('tlyric') or {}).get('lyric')

    # -- realtime (NetEase IM / "Yunxin") login for the room chat --
    _CLIENT_COOKIE_KEYS = {'os', 'osver', 'appver', 'channel', 'versioncode', 'buildver', 'resolution', 'requestid'}

    def realtime_credentials(self):
        now_ms = int(time.time() * 1000)
        kept = [p.strip() for p in self.core.current_cookie().split(';')
                if p.strip() and p.strip().split('=', 1)[0].strip().lower() not in self._CLIENT_COOKIE_KEYS]
        cookie = '; '.join(kept + ['osver=15.5', 'os=osx', 'appver=3.1.10.5100', 'channel=netease',
                                   'versioncode=140', f'buildver={now_ms // 1000}', 'resolution=1920x1080',
                                   f'requestId={now_ms}_{secrets.randbelow(1000):04d}'])
        url = 'https://interface3.music.163.com/api/middle/im/token/get?' + urllib.parse.urlencode(
            {'bizName': 'music_listenTogether'})
        req = urllib.request.Request(url, headers={
            'Cookie': cookie, 'Accept': 'application/json', 'Accept-Language': 'zh-CN,zh;q=0.9',
            'User-Agent': ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 '
                           '(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36')})
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                body = json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            raise NeteaseApiError('im_token', e.code)
        except (OSError, ValueError) as e:
            raise NeteaseApiError('im_token', None, f'im token request failed: {type(e).__name__}')
        data = self._ok('im_token', body).get('data') or {}
        if not data.get('accId') or not data.get('token'):
            raise NeteaseApiError('im_token', 200, 'IM credentials missing accId/token')
        return {'acc_id': str(data['accId']), 'token': str(data['token'])}
