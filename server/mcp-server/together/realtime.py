"""Room chat and live playback events.

The room chat runs on NetEase IM ("Yunxin"), which has no Python SDK. A small Node
process (together/nim/sidecar.mjs) wraps the official `node-nim` SDK and does only
transport: connect, send a prepared message, forward raw incoming messages. All
decoding and message building happen here in Python. The two talk JSON lines over
stdin/stdout.
"""
import json
import logging
import os
import shutil
import subprocess
import threading
import time
import uuid

logger = logging.getLogger("mcp-netease.together")

NIM_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'nim')
MAX_CHAT_TEXT = 500


# --- decoding (pure functions, unit-tested) ---

def _maybe_json(value):
    if isinstance(value, str):
        v = value.strip()
        if v[:1] in ('{', '[') and len(v) <= 65536:
            try:
                return json.loads(v)
            except ValueError:
                pass
    return value


def _find_envelope(value, depth=0):
    """Playback events are wrapped in an object with event_type/type 20000."""
    if depth > 7:
        return None
    value = _maybe_json(value)
    if isinstance(value, list):
        for item in value:
            found = _find_envelope(item, depth + 1)
            if found is not None:
                return found
        return None
    if not isinstance(value, dict) or not value:
        return None
    for key in ('event_type', 'type'):
        try:
            if int(value.get(key)) == 20000:
                return value
        except (TypeError, ValueError):
            pass
    for child in value.values():
        found = _find_envelope(child, depth + 1)
        if found is not None:
            return found
    return None


def _find_command(value, depth=0):
    if depth > 7:
        return None
    value = _maybe_json(value)
    if isinstance(value, list):
        for item in value:
            found = _find_command(item, depth + 1)
            if found is not None:
                return found
        return None
    if not isinstance(value, dict) or not value:
        return None
    if any(k in value for k in ('commandType', 'playStatus', 'targetSongId', 'progress')):
        return value
    for key in ('command', 'commandInfo', 'config', 'content', 'data', 'msg_attach_', 'msg_body_', 'ext_'):
        if key in value:
            found = _find_command(value[key], depth + 1)
            if found is not None:
                return found
    return None


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def decode_playback(raw):
    """A NIM room message -> playback event dict, or None if it is not one."""
    envelope = _find_envelope(raw)
    if envelope is None:
        return None
    cmd = _find_command(envelope)
    if cmd is None:
        return None
    status = str(cmd.get('playStatus') or '').upper()
    return {
        'command': (str(cmd['commandType']).upper() if cmd.get('commandType') else None),
        'song_id': None if cmd.get('targetSongId') in (None, '') else str(cmd['targetSongId']),
        'former_song_id': None if cmd.get('formerSongId') in (None, '') else str(cmd['formerSongId']),
        'progress': max(0, _int(cmd.get('progress')) or 0),
        'status': status if status in ('PLAY', 'PAUSE') else 'UNKNOWN',
        'server_seq': _int(cmd.get('serverSeq')) if cmd.get('serverSeq') is not None else _int(envelope.get('serverSeq')),
        'client_seq': _int(cmd.get('clientSeq')),
        'sender_id': None if not isinstance(raw, dict) or raw.get('from_id_') in (None, '') else str(raw['from_id_']),
    }


def _chat_text(value):
    if not isinstance(value, str) or not value:
        return None
    parsed = _maybe_json(value)
    if isinstance(parsed, str):
        return value
    if isinstance(parsed, dict):
        for key in ('text', 'content', 'body', 'msg'):
            if isinstance(parsed.get(key), str) and parsed[key]:
                return parsed[key]
    return None


def _server_ext(raw):
    """The NetEase app leaves from_nick_ empty and puts the sender profile in
    msg_setting_.ext_ -> serverExt {nickname, userId, avatarUrl}."""
    ext = _maybe_json((raw.get('msg_setting_') or {}).get('ext_')) if isinstance(raw.get('msg_setting_'), dict) else None
    server = ext.get('serverExt') if isinstance(ext, dict) else None
    return server if isinstance(server, dict) else {}


def decode_chat(raw):
    """A NIM room message -> chat dict (text messages only), or None."""
    if not isinstance(raw, dict) or _int(raw.get('msg_type_')) != 0:
        return None
    text = _chat_text(raw.get('msg_body_')) or _chat_text(raw.get('msg_attach_'))
    if not text:
        return None
    return {'text': text, 'sender_id': None if raw.get('from_id_') in (None, '') else str(raw['from_id_']),
            'sender_nick': raw.get('from_nick_') or _server_ext(raw).get('nickname') or None,
            'message_id': raw.get('client_msg_id_'),
            'time_ms': _int(raw.get('timetag_'))}


def build_text_message(text, room_id, message_id, profile=None):
    """The NIM message object NetEase's own client sends for a room chat line."""
    text = text.strip()
    if not text:
        raise ValueError('empty chat message')
    if len(text) > MAX_CHAT_TEXT:
        raise ValueError(f'chat message longer than {MAX_CHAT_TEXT} characters')
    ext = {'appName': 'music',
           'clientExt': {'bizType': 'listenTogether', 'ltType': 'FRIEND', 'roomId': room_id, 'clientMsgId': message_id}}
    p = profile or {}
    if str(p.get('id') or '').isdigit() and p.get('nickname') and p.get('avatar'):
        server_ext = {'userId': int(p['id']), 'nickname': p['nickname'], 'avatarUrl': p['avatar'],
                      'msgId': int(message_id.replace('-', '')[:12], 16) % 90_000_000_000 + 10_000_000_000}
        if isinstance(p.get('gender'), int):
            server_ext['gender'] = p['gender']
        ext = dict(serverExt=server_ext, **ext)
    return {'msg_type_': 0, 'msg_attach_': text, 'msg_body_': '', 'client_msg_id_': message_id, 'sub_type_': 0,
            'msg_setting_': {'ext_': json.dumps(ext, ensure_ascii=False, separators=(',', ':')),
                             'anti_spam_enable_': False, 'history_save_': True,
                             'anti_spam_using_yidun_': 1, 'route_enabled_': True}}


# --- the Node process ---

def node_available(node=None):
    node = node or os.environ.get('TOGETHER_NODE') or 'node'
    return shutil.which(node) is not None and os.path.isdir(os.path.join(NIM_DIR, 'node_modules', 'node-nim'))


class NimSidecar:
    """Owns the Node process. on_event(dict) is called from a reader thread."""

    def __init__(self, on_event, node=None):
        self.on_event = on_event
        self.node = node or os.environ.get('TOGETHER_NODE') or 'node'
        self.proc = None
        self.lock = threading.Lock()
        self.waiters = {}  # request id -> [threading.Event, result]
        self.connected = False

    def _start(self):
        if self.proc and self.proc.poll() is None:
            return
        self.connected = False
        self.proc = subprocess.Popen([self.node, os.path.join(NIM_DIR, 'sidecar.mjs')], cwd=NIM_DIR,
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                     text=True, encoding='utf-8', bufsize=1)
        threading.Thread(target=self._read, args=(self.proc,), daemon=True).start()
        threading.Thread(target=self._log_stderr, args=(self.proc,), daemon=True).start()

    def _read(self, proc):
        for line in proc.stdout:
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if event.get('ev') in ('connected',):
                self.connected = True
            elif event.get('ev') in ('exit', 'disconnected') or (event.get('ev') == 'link' and event.get('condition') == 2):
                self.connected = False
            rid = event.get('rid')
            if rid and rid in self.waiters:
                waiter = self.waiters[rid]
                waiter[1] = event
                waiter[0].set()
            try:
                self.on_event(event)
            except Exception as e:  # never let a handler kill the reader
                logger.error("together event handler failed: %s: %s", type(e).__name__, e)
        self.connected = False
        self.on_event({'ev': 'process_exit', 'code': proc.poll()})

    def _log_stderr(self, proc):
        for line in proc.stderr:
            logger.debug("nim: %s", line.rstrip()[:300])

    def request(self, op, timeout, **fields):
        """Send a command and wait for the event that answers it (same rid)."""
        rid = uuid.uuid4().hex
        waiter = [threading.Event(), None]
        self.waiters[rid] = waiter
        try:
            with self.lock:
                self._start()
                self.proc.stdin.write(json.dumps(dict(fields, op=op, rid=rid), ensure_ascii=False) + '\n')
                self.proc.stdin.flush()
            if not waiter[0].wait(timeout):
                return {'ev': 'timeout', 'op': op}
            return waiter[1]
        except OSError as e:
            return {'ev': 'error', 'op': op, 'message': f'sidecar unavailable: {type(e).__name__}'}
        finally:
            self.waiters.pop(rid, None)

    def connect(self, chat_room_id, credentials, profile):
        return self.request('connect', 45, chatRoomId=str(chat_room_id), accId=credentials['acc_id'],
                            token=credentials['token'],
                            profile={'nick': (profile or {}).get('nickname') or '',
                                     'avatar': (profile or {}).get('avatar') or ''})

    def send_message(self, msg):
        return self.request('send', 12, msg=msg)

    def disconnect(self):
        if self.proc and self.proc.poll() is None:
            self.request('disconnect', 5)
        self.connected = False

    def stop(self):
        proc = self.proc
        if proc and proc.poll() is None:
            try:
                proc.stdin.write('{"op":"shutdown"}\n')
                proc.stdin.flush()
                proc.wait(3)
            except (OSError, subprocess.TimeoutExpired):
                proc.kill()
        self.connected = False


def wait_until(predicate, timeout, step=0.25):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(step)
    return predicate()
