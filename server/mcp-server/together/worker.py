"""Listen Together state machine.

waiting_invite -> (invite found) joining -> listening -> (room ends) waiting_invite

Every few seconds: check room status, accept a fresh invite, send the room heartbeat,
and reconcile playback over HTTP. The room chat and live playback changes come in
through the Node transport when it is available; without it the worker still joins,
follows playback over HTTP and keeps the room alive, it just cannot chat.
"""
import hashlib
import json
import logging
import threading
import time
import uuid
from collections import deque

from .api import NeteaseApiError
from .invite import collect_contact_ids, parse_latest_invite
from .lyrics import lines_near, merge_translation, parse_lrc
from .songinfo import describe
from .realtime import build_text_message, decode_chat, decode_playback, wait_until

logger = logging.getLogger("mcp-netease.together")

ROOM_ENDED = 488  # NetEase's code when the room no longer exists


# a song stopped further than this from its end was cut short, not run out
SKIP_MARGIN_MS = 15000

class Config:
    def __init__(self, env):
        num = lambda key, default: float(env.get(key) or default)
        self.inviter_uid = (env.get('TOGETHER_INVITER_UID') or '').strip() or None
        self.accept_any = (env.get('TOGETHER_ACCEPT_ANY') or '').lower() in ('1', 'true', 'yes', 'on')
        self.poll_s = num('TOGETHER_POLL_SECONDS', 4)
        self.heartbeat_s = num('TOGETHER_HEARTBEAT_SECONDS', 10)
        self.reconcile_s = num('TOGETHER_RECONCILE_SECONDS', 30)
        # debugging aid: append every raw room-transport event to this file
        self.raw_log = (env.get('TOGETHER_RAW_LOG') or '').strip() or None
        self.song_debounce_s = num('TOGETHER_SONG_DEBOUNCE_SECONDS', 5)
        # append NetEase's own notes on the song (style, tags, BPM, chorus) to the song notice
        self.song_info = (env.get('TOGETHER_SONG_INFO') or 'true').lower() in ('1', 'true', 'yes', 'on')
        self.notify_pause = (env.get('TOGETHER_NOTIFY_PAUSE') or 'true').lower() in ('1', 'true', 'yes', 'on')
        # a skip wakes the AI at most this often; skips in between are passed on quietly
        self.skip_wake_gap_s = num('TOGETHER_SKIP_WAKE_GAP_SECONDS', 180)
        self.control_timeout_s = num('TOGETHER_CONTROL_TIMEOUT_SECONDS', 8)


class TogetherWorker:
    def __init__(self, api, config, sidecar=None):
        self.api, self.config, self.sidecar = api, config, sidecar
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.wake = threading.Event()
        self.sinks = []                     # callables receiving every room event
        self.events = deque(maxlen=200)     # recent events, for the together_events tool
        self.event_seq = 0
        self.event_cond = threading.Condition()
        self.phase = 'starting'
        self.account = None
        self.nim_acc_id = None
        self.realtime_note = None if sidecar else 'Node transport not installed: room chat unavailable'
        self.last_error = None
        self.error_streak = 0
        self.handled_invites = deque(maxlen=50)
        self.handled_chat = deque(maxlen=512)
        self._reset_room()

    def _reset_room(self):
        self.room_id = self.chat_room_id = self.peer_id = None
        self.playing = None             # {song_id, status, progress, observed_at, server_seq}
        self.song = None                # {id, name, artist, album, duration_ms}
        self.lyric_lines = []
        self.prev_status = 'UNKNOWN'
        self.pending_song_notice = None  # (due_time, song) while a song change settles
        self.song_change_by_self = False  # the last song change was the AI's own command
        self.song_change_cut = False      # she cut the previous song short (rather than it running out)
        self.last_skip_wake = 0.0
        self.loading_song = None
        self.joined_pending = False
        self.last_heartbeat = self.last_reconcile = self.last_realtime_try = 0
        self.client_seq = 1
        self.pending_control = None

    # ------------------------------------------------------------------ lifecycle
    def start(self):
        threading.Thread(target=self._run, name='together-worker', daemon=True).start()

    def stop(self):
        self.stop_event.set()
        self.wake.set()
        if self.sidecar:
            self.sidecar.stop()

    def _run(self):
        while not self.stop_event.is_set():
            started = time.time()
            self.tick()
            base = self.config.poll_s
            delay = min(60, base * 2 ** min(self.error_streak, 4)) if self.error_streak else base
            next_tick = started + max(0.25, delay)
            # Between polls, wake up exactly when a pending song notice is due, so the
            # notice goes out on time instead of on the next poll.
            while not self.stop_event.is_set():
                pending = self.pending_song_notice
                deadline = min(next_tick, pending[0]) if pending else next_tick
                self.wake.wait(max(0.05, deadline - time.time()))
                self.wake.clear()
                self._flush_song_notice()
                if time.time() >= next_tick:
                    break

    def tick(self):
        """One poll with error handling; a 488 while in a room means the room is gone."""
        try:
            if not self.account:
                self.account = self.api.account_profile()
            self.poll_once()
            self.error_streak, self.last_error = 0, None
        except NeteaseApiError as e:
            if e.code == ROOM_ENDED and self.room_id:
                self._leave_room('一起听的房间已经结束了。')
            else:
                self._record_error(e)
        except Exception as e:
            self._record_error(e)
            logger.exception("together poll failed")

    def _record_error(self, e):
        self.error_streak += 1
        self.last_error = f'{type(e).__name__}: {e}'[:300]
        if self.error_streak in (1, 5) or self.error_streak % 30 == 0:
            logger.warning("together: %s", self.last_error)

    # ------------------------------------------------------------------ polling
    def poll_once(self):
        remote = self.api.room_status()
        if remote['in_room'] and remote['room_id']:
            if remote['room_id'] != self.room_id:
                self._enter_room(remote['room_id'], remote['chat_room_id'])
            elif remote['chat_room_id']:
                self.chat_room_id = remote['chat_room_id']
        elif self.room_id:
            self._leave_room('一起听已经结束了。')

        if not self.room_id:
            self.phase = 'waiting_invite'
            invite = self._find_invite()
            if invite:
                self._accept(invite)
        if not self.room_id:
            return
        self.phase = 'listening'
        self._ensure_realtime()
        now = time.time()
        realtime_live = bool(self.sidecar and self.sidecar.connected)
        if not self.playing or not realtime_live or now - self.last_reconcile >= self.config.reconcile_s:
            state = self.api.sync_state(self.room_id)
            self.last_reconcile = time.time()
            self.apply_playback(state['playing'], self.last_reconcile, 'http')
        self._heartbeat()
        self._flush_song_notice()

    def _find_invite(self):
        own = self.account['id']
        if self.config.inviter_uid:
            contacts = [self.config.inviter_uid]
        elif self.config.accept_any:
            contacts = collect_contact_ids(self.api.recent_contacts(), own)
        else:
            return None  # nobody is allowed to pull us into a room
        for uid in contacts:
            invite = parse_latest_invite(self.api.private_history(uid), uid)
            if invite and invite['room_id'] not in self.handled_invites:
                if self.config.inviter_uid and invite['inviter_id'] != self.config.inviter_uid:
                    continue
                return invite
        return None

    def _accept(self, invite):
        self.phase = 'joining'
        self.handled_invites.append(invite['room_id'])
        joined = self.api.accept_invite(invite['room_id'], invite['inviter_id'])
        logger.info("together: joined a room (inviter ...%s)", str(invite['inviter_id'])[-4:])
        self._enter_room(joined['room_id'], joined['chat_room_id'], invite['inviter_id'])

    def _enter_room(self, room_id, chat_room_id, peer_id=None):
        with self.lock:
            if self.room_id and self.room_id != room_id and self.sidecar:
                self.sidecar.disconnect()
            self._fail_pending_control('room changed')
            self._reset_room()
            self.room_id, self.chat_room_id = room_id, chat_room_id
            self.peer_id = peer_id or self.config.inviter_uid
            # 'invited' = just accepted her invitation; 'resumed' = found ourselves already in a room (restart)
            self.joined_pending = 'invited' if peer_id else 'resumed'
            self.phase = 'listening'

    def _leave_room(self, message):
        with self.lock:
            room_id, chat_room_id = self.room_id, self.chat_room_id
            self._fail_pending_control('room ended')
            self._reset_room()
            self.phase = 'waiting_invite'
        if self.sidecar:
            self.sidecar.disconnect()
        if room_id:
            self._emit('room', message, '一起听 · 结束了', room_id, chat_room_id)

    def _heartbeat(self):
        playing = self.playing
        if not playing or time.time() - self.last_heartbeat < self.config.heartbeat_s:
            return
        self.api.heartbeat(self.room_id, playing['song_id'], playing['status'] == 'PLAY', self.progress_ms())
        self.last_heartbeat = time.time()

    def _ensure_realtime(self):
        if not self.sidecar or not self.chat_room_id or self.sidecar.connected:
            return
        if time.time() - self.last_realtime_try < 10:
            return
        self.last_realtime_try = time.time()
        try:
            creds = self.api.realtime_credentials()
        except NeteaseApiError as e:
            self.realtime_note = f'IM credentials failed: {e}'
            return
        self.nim_acc_id = creds['acc_id']
        answer = self.sidecar.connect(self.chat_room_id, creds, self.account)
        if answer.get('ev') == 'connected':
            self.realtime_note = None
            logger.info("together: room chat connected")
        else:
            self.realtime_note = f"room chat connect failed: {answer.get('message') or answer.get('ev')}"
            logger.warning("together: %s", self.realtime_note)

    # ------------------------------------------------------------------ playback
    def progress_ms(self, now=None):
        p = self.playing
        if not p:
            return 0
        elapsed = max(0, (now or time.time()) - p['observed_at']) * 1000 if p['status'] == 'PLAY' else 0
        progress = p['progress'] + elapsed
        if self.song and self.song['id'] == p['song_id'] and self.song['duration_ms']:
            progress = min(progress, self.song['duration_ms'])
        return int(progress)

    def apply_playback(self, state, observed_at, origin):
        with self.lock:
            if not self.room_id:
                return
            seq, last = state.get('server_seq'), (self.playing or {}).get('server_seq')
            if seq is not None and last is not None and seq < last:
                return  # older than what we already know
            song_id = state.get('song_id') or (self.playing or {}).get('song_id')
            status = state.get('status') or 'UNKNOWN'
            if status == 'UNKNOWN' and self.playing:
                status = self.playing['status']
            changed_song = (bool(song_id) and song_id != (self.song or {}).get('id')
                            and song_id != self.loading_song)
            if changed_song:
                self.loading_song = song_id
                # Did she cut the last song short (a choice worth a word), or did it simply run out?
                if self.song and self.playing and self.song.get('id') == self.playing.get('song_id'):
                    left = (self.song.get('duration_ms') or 0) - self.progress_ms(observed_at)
                    cut = (state.get('command') == 'GOTO' or self.playing.get('status') == 'PAUSE'
                           or left > SKIP_MARGIN_MS)
                else:
                    cut = state.get('command') == 'GOTO'
                # a quick run of changes counts as a skip if any of them was one
                self.song_change_cut = cut or (bool(self.pending_song_notice) and self.song_change_cut)
            self.playing = {'song_id': song_id, 'status': status, 'progress': state.get('progress') or 0,
                            'observed_at': observed_at, 'server_seq': seq if seq is not None else last}
            prev_status, self.prev_status = self.prev_status, status
            # The room echoes the AI's own commands back; keep the state but don't tell it what it just did.
            own = origin == 'realtime' and state.get('sender_id') in self._own_ids()
            if changed_song:
                self.song_change_by_self = own
        if changed_song:
            self._load_song(song_id)
        elif (not own and prev_status != 'UNKNOWN' and status != prev_status and self.config.notify_pause
              and self.song and not self.pending_song_notice):  # a pending song notice already says whether it is paused
            name = f"《{self.song['name']}》— {self.song['artist']}"
            if status == 'PAUSE':
                self._emit('playback', f'（一起听）{name} 暂停了。', '一起听 · 暂停', wake=False)
            elif status == 'PLAY':
                self._emit('playback', f'（一起听）继续播放 {name}。', '一起听 · 继续播放', wake=False)

    def _load_song(self, song_id):
        try:
            song = self.api.song(song_id)
            lrc, tlrc = self.api.lyrics(song_id)
            song['about'] = describe(self.api.song_info(song_id)) if self.config.song_info else None
        finally:
            with self.lock:
                if self.loading_song == song_id:
                    self.loading_song = None
        with self.lock:
            if (self.playing or {}).get('song_id') != song_id:
                return  # moved on while we were fetching
            self.song = song
            self.lyric_lines = merge_translation(parse_lrc(lrc), parse_lrc(tlrc))
            # Wait a moment: a quick run of skips becomes one notice about where it landed.
            self.pending_song_notice = (time.time() + self.config.song_debounce_s, song)
        self.wake.set()

    def _flush_song_notice(self):
        with self.lock:
            pending = self.pending_song_notice
            if not pending or time.time() < pending[0]:
                return
            self.pending_song_notice = None
            song = pending[1]
            if (self.song or {}).get('id') != song['id']:
                return
            joined, self.joined_pending = self.joined_pending, False
            paused = (self.playing or {}).get('status') == 'PAUSE'
            if self.song_change_by_self and not joined:
                return
            cut, self.song_change_cut = self.song_change_cut, False
            wake = bool(joined)
            if cut and not joined and time.time() - self.last_skip_wake >= self.config.skip_wake_gap_s:
                wake, self.last_skip_wake = True, time.time()
        name = f"《{song['name']}》— {song['artist']}"
        if joined == 'resumed':
            text = f'（一起听）重新连回了一起听的房间，现在放的是 {name}。'
            line = f'一起听 · 重新连上，正在放{name}'
        elif joined:
            text = f'（一起听）她把你拉进了一起听，现在放的是 {name}。'
            line = f'一起听 · 加入了，正在放{name}'
        elif cut:
            text = f"（一起听）她把上一首切掉了，切到了 {name}{'，目前暂停着' if paused else ''}。"
            line = f'一起听 · 切到{name}'
        else:
            text = f"（一起听）上一首放完了，接着放 {name}{'，目前暂停着' if paused else ''}。"
            line = f'一起听 · 接着放{name}'
        if song.get('album'):
            text += f"专辑《{song['album']}》。"
        if song.get('about'):
            text += f"{song['about']}。"
        if wake:
            text += '想聊就聊，不想说话也可以安静陪着听。'
        self._emit('song', text, line, wake=wake)

    # ------------------------------------------------------------------ realtime events
    def on_realtime(self, event):
        kind = event.get('ev')
        if self.config.raw_log:
            try:
                with open(self.config.raw_log, 'a', encoding='utf-8') as f:
                    f.write(json.dumps({'t': time.time(), **event}, ensure_ascii=False, default=str) + '\n')
            except OSError:
                pass
        if kind == 'message':
            raw = event.get('msg') or {}
            playback = decode_playback(raw)
            if playback:
                self.apply_playback(playback, time.time(), 'realtime')
                self._confirm_control(playback)
                return
            chat = decode_chat(raw)
            if chat:
                self._on_chat(chat)
        elif kind in ('exit', 'process_exit') or (kind == 'link' and event.get('condition') == 2):
            self.realtime_note = f'room chat dropped ({kind}); reconnecting'
            self.last_realtime_try = 0
        elif kind == 'fatal':
            self.realtime_note = event.get('message')

    def _own_ids(self):
        return {(self.account or {}).get('id'), self.nim_acc_id} - {None}

    def _on_chat(self, chat):
        if chat['sender_id'] in self._own_ids() or not self.room_id:
            return
        key = f"{self.room_id}:{chat['message_id'] or (chat['sender_id'], chat['time_ms'], chat['text'])}"
        if key in self.handled_chat:
            return
        self.handled_chat.append(key)
        self._emit('chat', chat['text'], None, key=key, sender=chat['sender_nick'])

    # ------------------------------------------------------------------ events out
    def _emit(self, kind, text, line, room_id=None, chat_room_id=None, key=None, sender=None, wake=True):
        """wake=False: worth knowing, not worth waking the AI for (a song that simply ran out, a pause).
        A bridge passes it on as a quiet event; the AI reads it with the next thing that does wake it."""
        room_id = room_id or self.room_id
        raw_key = key or f'{kind}:{room_id}:{text}:{time.time()}'
        event = {'key': 'netease:' + hashlib.sha256(raw_key.encode()).hexdigest()[:32], 'kind': kind, 'text': text,
                 'line': line, 'sender': sender, 'room_id': room_id, 'chat_room_id': chat_room_id or self.chat_room_id,
                 'account': (self.account or {}).get('id'), 'time': time.time(), 'wake': wake}
        with self.event_cond:
            self.event_seq += 1
            event['seq'] = self.event_seq
            self.events.append(event)
            self.event_cond.notify_all()
        for sink in self.sinks:
            try:
                sink(event)
            except Exception as e:
                logger.error("together sink failed: %s: %s", type(e).__name__, e)

    def wait_events(self, after, timeout):
        deadline = time.time() + timeout
        with self.event_cond:
            while True:
                fresh = [e for e in self.events if e['seq'] > after]
                left = deadline - time.time()
                if fresh or left <= 0:
                    return fresh, self.event_seq
                self.event_cond.wait(left)

    # ------------------------------------------------------------------ actions
    def send_chat(self, text, room_id=None):
        if not self.room_id:
            return {'ok': False, 'error': 'not in a Listen Together room'}
        if room_id and room_id != self.room_id:
            return {'ok': False, 'error': 'room changed'}
        if not (self.sidecar and self.sidecar.connected):
            return {'ok': False, 'error': self.realtime_note or 'room chat not connected'}
        try:
            msg = build_text_message(text, self.room_id, str(uuid.uuid4()), self.account)
        except ValueError as e:
            return {'ok': False, 'error': str(e)}
        answer = self.sidecar.send_message(msg)
        if answer.get('ev') == 'sent' and answer.get('code') == 200:
            return {'ok': True}
        if answer.get('ev') == 'timeout':
            return {'ok': False, 'uncertain': True, 'error': 'send timed out; it may or may not have arrived'}
        return {'ok': False, 'error': answer.get('message') or f"send failed code={answer.get('code')}"}

    def control(self, action, song_id=None):
        """pause / resume / next / goto. Waits until the room confirms the change."""
        with self.lock:
            if not self.room_id or not self.playing or not self.playing.get('song_id'):
                return {'ok': False, 'error': 'not in a room or playback state unknown yet'}
            if self.pending_control:
                return {'ok': False, 'error': 'another playback command is still pending'}
            room_id, current = self.room_id, self.playing['song_id']
        if action in ('next', 'goto'):
            state = self.api.sync_state(room_id)
            queue = state['display']
            if action == 'next':
                if current not in queue:
                    return {'ok': False, 'error': 'current song is not in the room queue'}
                song_id = queue[(queue.index(current) + 1) % len(queue)]
            elif str(song_id) not in queue:
                return {'ok': False, 'error': 'that song is not in the room queue; use together_queue_next first'}
            command, target, status, progress = 'GOTO', str(song_id), 'PLAY', 0
        elif action in ('pause', 'resume'):
            command = 'PAUSE' if action == 'pause' else 'PLAY'
            target, status, progress = current, command, self.progress_ms()
        else:
            return {'ok': False, 'error': f'unknown action {action}'}
        with self.lock:
            seq, self.client_seq = self.client_seq, self.client_seq + 1
            self.pending_control = {'seq': seq, 'command': command, 'song_id': target, 'status': status,
                                    'done': threading.Event(), 'result': None}
            pending = self.pending_control
        try:
            self.api.play_command(room_id, command, progress, status, current, target, seq)
        except NeteaseApiError as e:
            with self.lock:
                self.pending_control = None
            return {'ok': False, 'error': str(e)}
        confirmed = pending['done'].wait(self.config.control_timeout_s)
        if not confirmed:
            # No live event (e.g. no chat transport): ask the room directly.
            state = self.api.sync_state(room_id)['playing']
            confirmed = state['song_id'] == target and state['status'] == status
        with self.lock:
            if self.pending_control is pending:
                self.pending_control = None
        if pending['result'] and pending['result'].startswith('failed'):
            return {'ok': False, 'error': pending['result']}
        return {'ok': True, 'confirmed': bool(confirmed), 'command': command, 'song_id': target}

    def _confirm_control(self, playback):
        with self.lock:
            p = self.pending_control
            if (p and playback.get('client_seq') == p['seq'] and playback.get('command') == p['command']
                    and playback.get('song_id') == p['song_id'] and playback.get('status') == p['status']):
                p['result'] = 'confirmed'
                p['done'].set()

    def _fail_pending_control(self, reason):
        p = self.pending_control
        if p:
            p['result'] = f'failed: {reason}'
            p['done'].set()
        self.pending_control = None

    def queue_next(self, song_id):
        """Put a song right after the current one in the room queue."""
        song_id = str(song_id)
        with self.lock:
            if not self.room_id or not self.playing or not self.account:
                return {'ok': False, 'error': 'not in a room or playback state unknown yet'}
            room_id, current, uid = self.room_id, self.playing['song_id'], self.account['id']
        if song_id == current:
            return {'ok': False, 'error': 'that song is already playing'}
        state = self.api.sync_state(room_id)
        if current not in state['display']:
            return {'ok': False, 'error': 'current song is not in the room queue'}
        display = [i for i in state['display'] if i != song_id]
        display.insert(display.index(current) + 1, song_id)
        version = next((v['version'] for v in state['versions'] if v['userId'] == uid), 0) + 1
        random_list = state['random'] if song_id in state['random'] else state['random'] + [song_id]
        try:
            self.api.replace_queue(room_id, uid, version, display, random_list)
        except NeteaseApiError as e:
            return {'ok': False, 'error': str(e)}

        def landed():
            q = self.api.sync_state(room_id)['display']
            return current in q and q[(q.index(current) + 1) % len(q)] == song_id
        return {'ok': True, 'confirmed': wait_until(landed, self.config.control_timeout_s, 0.5),
                'song_id': song_id, 'after': current}

    def leave(self):
        room_id = self.room_id
        if not room_id:
            return {'ok': False, 'error': 'not in a room'}
        self.api.end_room(room_id)
        self._leave_room('一起听结束了。')
        return {'ok': True}

    # ------------------------------------------------------------------ status
    def status(self, lyric_context=True):
        with self.lock:
            out = {'phase': self.phase, 'room_id': self.room_id, 'chat_connected': bool(self.sidecar and self.sidecar.connected),
                   'chat_note': self.realtime_note, 'last_error': self.last_error,
                   'accepting_invites_from': self.config.inviter_uid or ('recent contacts' if self.config.accept_any else 'nobody (set TOGETHER_INVITER_UID)')}
            if self.song:
                progress = self.progress_ms()
                out['song'] = {k: self.song.get(k) for k in ('id', 'name', 'artist', 'album', 'about')}
                out['play_status'] = (self.playing or {}).get('status')
                out['position'] = f"{progress // 60000}:{progress // 1000 % 60:02d} / {self.song['duration_ms'] // 60000}:{self.song['duration_ms'] // 1000 % 60:02d}"
                if lyric_context:
                    out['lyrics_now'] = lines_near(self.lyric_lines, progress) or None
        return out
