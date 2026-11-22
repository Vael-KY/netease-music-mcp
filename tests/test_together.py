"""Listen Together extension with a fake NetEase API and fake room transport."""
import json
import threading
import time
import unittest
import urllib.parse

import helpers  # noqa: F401  (puts server/mcp-server on sys.path)
from together.api import NeteaseApiError
from together.invite import collect_contact_ids, parse_latest_invite
from together.lyrics import lines_near, merge_translation, parse_lrc
from together.realtime import build_text_message, decode_chat, decode_playback
from together.relay import RelayBridge, split_reply
from together.store import Store
from together.worker import Config, TogetherWorker


def invite_message(room='R1', inviter='99', age_ms=0, encoded=True):
    url = f'orpheus://rnpage?component=listenTogether&roomId={room}&inviterId={inviter}'
    if encoded:
        url = 'orpheus://nm/web?url=' + urllib.parse.quote(url, safe='')
    return {'type': 23, 'time': int(time.time() * 1000) - age_ms,
            'msg': json.dumps({'type': 23, 'generalMsg': {'nativeUrl': url}})}


class InviteTest(unittest.TestCase):
    def test_parses_encoded_invite(self):
        inv = parse_latest_invite({'msgs': [invite_message()]}, '99')
        self.assertEqual((inv['room_id'], inv['inviter_id']), ('R1', '99'))

    def test_ignores_old_and_other_messages(self):
        old = invite_message(age_ms=6 * 60 * 1000)
        text = {'type': 6, 'time': int(time.time() * 1000), 'msg': json.dumps({'msg': 'hi'})}
        self.assertIsNone(parse_latest_invite({'msgs': [old, text]}, '99'))

    def test_newest_wins(self):
        newer, older = invite_message('NEW', age_ms=1000), invite_message('OLD', age_ms=60000)
        self.assertEqual(parse_latest_invite({'msgs': [older, newer]}, '99')['room_id'], 'NEW')

    def test_contact_ids(self):
        contacts = {'data': {'list': [{'fromUser': {'userId': 1}}, {'toUser': {'userId': 2}}, {'userId': 1}]}}
        self.assertEqual(collect_contact_ids(contacts, own_uid=2), ['1'])


class DecodeTest(unittest.TestCase):
    def test_playback_event_nested_in_attach(self):
        attach = json.dumps({'type': 20000, 'data': json.dumps({'commandInfo': json.dumps({
            'commandType': 'goto', 'targetSongId': 5, 'formerSongId': 4, 'progress': 1200,
            'playStatus': 'play', 'clientSeq': 3, 'serverSeq': 17})})})
        ev = decode_playback({'msg_type_': 100, 'msg_attach_': attach, 'from_id_': '77'})
        self.assertEqual(ev, {'command': 'GOTO', 'song_id': '5', 'former_song_id': '4', 'progress': 1200,
                              'status': 'PLAY', 'server_seq': 17, 'client_seq': 3, 'sender_id': '77'})

    def test_chat_is_not_playback(self):
        raw = {'msg_type_': 0, 'msg_attach_': '下一首放水星记', 'from_id_': '9', 'client_msg_id_': 'm1', 'timetag_': 5}
        self.assertIsNone(decode_playback(raw))
        self.assertEqual(decode_chat(raw)['text'], '下一首放水星记')
        self.assertIsNone(decode_chat({'msg_type_': 5, 'msg_attach_': 'joined'}))

    def test_build_text_message(self):
        msg = build_text_message(' 嗨 ', 'R1', '0123456789ab-cdef', {'id': '5', 'nickname': 'K', 'avatar': 'u'})
        ext = json.loads(msg['msg_setting_']['ext_'])
        self.assertEqual((msg['msg_type_'], msg['msg_attach_']), (0, '嗨'))
        self.assertEqual(ext['clientExt']['roomId'], 'R1')
        self.assertEqual(ext['serverExt']['userId'], 5)
        with self.assertRaises(ValueError):
            build_text_message('x' * 501, 'R1', 'id')


class LyricsTest(unittest.TestCase):
    def test_parse_and_near(self):
        lines = merge_translation(parse_lrc('[ti:x]\n[00:01.00]one\n[00:05.50]two\n[00:09.0]three'),
                                  parse_lrc('[00:05.50]二'))
        self.assertEqual(lines[1], (5500, 'two / 二'))
        near = lines_near(lines, 6000, before=1, after=1)
        self.assertEqual(near, ['  [00:01] one', '> [00:05] two / 二', '  [00:09] three'])

    def test_split_reply(self):
        self.assertEqual(split_reply('a\n\n b \n' + 'x' * 501), ['a', 'b', 'x' * 500, 'x'])


class FakeApi:
    def __init__(self):
        self.status = {'in_room': False, 'room_id': None, 'chat_room_id': None}
        self.invites = {'99': {'msgs': [invite_message()]}}
        self.state = {'playing': {'song_id': '1', 'status': 'PLAY', 'progress': 0, 'server_seq': 1},
                      'display': ['1', '2', '3'], 'random': ['1', '2', '3'], 'play_mode': 'ORDER_LOOP',
                      'versions': [{'userId': '5', 'version': 2}]}
        self.calls = []

    def account_profile(self):
        return {'id': '5', 'nickname': 'Kael', 'avatar': 'a', 'gender': 1}

    def room_status(self):
        return dict(self.status)

    def private_history(self, uid, limit=8):
        return self.invites.get(uid, {'msgs': []})

    def recent_contacts(self):
        return {}

    def accept_invite(self, room_id, inviter_id):
        self.calls.append(('accept', room_id, inviter_id))
        self.status = {'in_room': True, 'room_id': room_id, 'chat_room_id': '123'}
        return {'room_id': room_id, 'chat_room_id': '123'}

    def sync_state(self, room_id):
        return json.loads(json.dumps(self.state))

    def heartbeat(self, *args):
        self.calls.append(('heartbeat',) + args)

    def song(self, song_id):
        return {'id': song_id, 'name': f'Song{song_id}', 'artist': 'Artist', 'album': 'Album', 'duration_ms': 200000}

    def lyrics(self, song_id):
        return '[00:00.00]la', None

    def play_command(self, room_id, command, progress, status, former, target, seq):
        self.calls.append(('command', command, target, seq))
        self.state['playing'].update(song_id=target, status=status)

    def replace_queue(self, room_id, uid, version, display, random_list):
        self.calls.append(('queue', uid, version, display))
        self.state['display'] = display

    def realtime_credentials(self):
        return {'acc_id': 'nim5', 'token': 't'}

    def end_room(self, room_id):
        self.calls.append(('end', room_id))
        self.status = {'in_room': False, 'room_id': None, 'chat_room_id': None}


class FakeSidecar:
    def __init__(self):
        self.connected = False
        self.sent = []

    def connect(self, chat_room_id, creds, profile):
        self.connected = True
        return {'ev': 'connected'}

    def send_message(self, msg):
        self.sent.append(msg['msg_attach_'])
        return {'ev': 'sent', 'code': 200}

    def disconnect(self):
        self.connected = False

    def stop(self):
        pass


def make_worker(**env):
    env = dict({'TOGETHER_INVITER_UID': '99', 'TOGETHER_SONG_DEBOUNCE_SECONDS': '0',
                'TOGETHER_CONTROL_TIMEOUT_SECONDS': '0.5'}, **env)
    api, sidecar = FakeApi(), FakeSidecar()
    worker = TogetherWorker(api, Config(env), sidecar)
    worker.account = api.account_profile()
    return worker, api, sidecar


class WorkerTest(unittest.TestCase):
    def test_join_follow_and_chat(self):
        worker, api, sidecar = make_worker()
        worker.poll_once()
        self.assertEqual(api.calls[0], ('accept', 'R1', '99'))
        self.assertEqual((worker.phase, worker.room_id), ('listening', 'R1'))
        self.assertTrue(sidecar.connected)
        worker.poll_once()  # the song notice is flushed after its (zero) debounce
        kinds = [e['kind'] for e in worker.events]
        self.assertEqual(kinds, ['song'])
        self.assertIn('Song1', worker.events[0]['text'])
        self.assertIn('拉进了一起听', worker.events[0]['text'])

        worker.on_realtime({'ev': 'message', 'msg': {'msg_type_': 0, 'msg_attach_': '好听', 'from_id_': '99', 'client_msg_id_': 'c1'}})
        worker.on_realtime({'ev': 'message', 'msg': {'msg_type_': 0, 'msg_attach_': '好听', 'from_id_': '99', 'client_msg_id_': 'c1'}})
        worker.on_realtime({'ev': 'message', 'msg': {'msg_type_': 0, 'msg_attach_': 'mine', 'from_id_': 'nim5', 'client_msg_id_': 'c2'}})
        chats = [e for e in worker.events if e['kind'] == 'chat']
        self.assertEqual([e['text'] for e in chats], ['好听'])  # duplicates and our own echo dropped

        self.assertEqual(worker.send_chat('嗯，我也喜欢'), {'ok': True})
        self.assertEqual(sidecar.sent, ['嗯，我也喜欢'])
        self.assertTrue(any(c[0] == 'heartbeat' for c in api.calls))
        status = worker.status()
        self.assertEqual(status['song']['name'], 'Song1')
        self.assertEqual(status['lyrics_now'], ['> [00:00] la'])

    def test_no_invites_without_allowed_inviter(self):
        worker, api, _ = make_worker(TOGETHER_INVITER_UID='')
        worker.poll_once()
        self.assertEqual(api.calls, [])
        self.assertEqual(worker.phase, 'waiting_invite')

    def test_pause_and_song_change_events(self):
        worker, api, _ = make_worker()
        worker.poll_once()
        worker.poll_once()
        worker.apply_playback({'song_id': '1', 'status': 'PAUSE', 'progress': 3000, 'server_seq': 2}, time.time(), 'realtime')
        worker.apply_playback({'song_id': '2', 'status': 'PLAY', 'progress': 0, 'server_seq': 3}, time.time(), 'realtime')
        worker.apply_playback({'song_id': '1', 'status': 'PLAY', 'progress': 0, 'server_seq': 2}, time.time(), 'realtime')  # stale
        worker._flush_song_notice()
        self.assertEqual([e['kind'] for e in worker.events], ['song', 'playback', 'song'])
        self.assertIn('Song2', worker.events[-1]['text'])
        self.assertEqual(worker.playing['song_id'], '2')

    def test_own_commands_are_not_echoed(self):
        worker, api, _ = make_worker()
        worker.poll_once()
        worker.poll_once()
        before = len(worker.events)
        worker.apply_playback({'song_id': '1', 'status': 'PAUSE', 'progress': 3000, 'server_seq': 2,
                               'sender_id': '5'}, time.time(), 'realtime')
        worker.apply_playback({'song_id': '2', 'status': 'PLAY', 'progress': 0, 'server_seq': 3,
                               'sender_id': '5'}, time.time(), 'realtime')
        worker._flush_song_notice()
        self.assertEqual(len(worker.events), before)
        self.assertEqual((worker.playing['song_id'], worker.song['id']), ('2', '2'))  # state still followed
        # her next switch is reported again
        worker.apply_playback({'song_id': '3', 'status': 'PLAY', 'progress': 0, 'server_seq': 4,
                               'sender_id': '99'}, time.time(), 'realtime')
        worker._flush_song_notice()
        self.assertEqual([e['kind'] for e in worker.events][before:], ['song'])

    def test_control_confirmed_by_live_event(self):
        worker, api, _ = make_worker(TOGETHER_CONTROL_TIMEOUT_SECONDS='3')
        worker.poll_once()
        result = {}
        t = threading.Thread(target=lambda: result.update(worker.control('next')))
        t.start()
        for _ in range(100):
            if worker.pending_control:
                break
            time.sleep(0.01)
        seq = worker.pending_control['seq']
        attach = json.dumps({'type': 20000, 'commandType': 'GOTO', 'targetSongId': '2', 'playStatus': 'PLAY',
                             'clientSeq': seq, 'serverSeq': 9})
        worker.on_realtime({'ev': 'message', 'msg': {'msg_type_': 100, 'msg_attach_': attach}})
        t.join(3)
        self.assertEqual(result, {'ok': True, 'confirmed': True, 'command': 'GOTO', 'song_id': '2'})

    def test_queue_next(self):
        worker, api, _ = make_worker()
        worker.poll_once()
        out = worker.queue_next('3')
        self.assertTrue(out['ok'] and out['confirmed'])
        self.assertEqual(api.calls[-1], ('queue', '5', 3, ['1', '3', '2']))

    def test_room_end(self):
        worker, api, sidecar = make_worker()
        worker.poll_once()
        api.status = {'in_room': False, 'room_id': None, 'chat_room_id': None}
        worker.poll_once()
        self.assertEqual((worker.room_id, worker.phase), (None, 'waiting_invite'))
        self.assertFalse(sidecar.connected)
        self.assertEqual(worker.events[-1]['kind'], 'room')

    def test_room_ended_code_while_polling(self):
        worker, api, _ = make_worker()
        worker.poll_once()

        def ended(room_id):
            raise NeteaseApiError('together_playlist', 488)
        api.sync_state = ended
        worker.last_reconcile = 0
        with self.assertRaises(NeteaseApiError) as caught:
            worker.poll_once()
        self.assertEqual(caught.exception.code, 488)
        worker.tick()  # the loop's error handling turns 488 into leaving the room
        self.assertEqual((worker.room_id, worker.phase), (None, 'waiting_invite'))
        self.assertEqual(worker.last_error, None)


class FakeRelay:
    def __init__(self):
        self.sent, self.history, self.next_id = [], [], 100

    def ready(self):
        return True

    def call(self, path, body=None):
        if path.startswith('/app/history?tail'):
            return {'messages': [{'id': 50}]}
        if path.startswith('/app/history'):
            since = int(urllib.parse.parse_qs(path.split('?', 1)[1])['since'][0])
            return {'messages': [m for m in self.history if m['id'] > since]}
        if path == '/app/send':
            self.next_id += 1
            self.sent.append(dict(body, id=self.next_id))
            return {'id': self.next_id}
        raise AssertionError(path)


class RelayBridgeTest(unittest.TestCase):
    def test_round_trip(self):
        worker, api, sidecar = make_worker()
        relay, store = FakeRelay(), Store(':memory:')
        bridge = RelayBridge(relay, store, worker, session='s1')
        worker.sinks.append(bridge.accept)
        worker.poll_once()
        worker.poll_once()
        worker.on_realtime({'ev': 'message', 'msg': {'msg_type_': 0, 'msg_attach_': '在吗', 'from_id_': '99', 'client_msg_id_': 'c9'}})
        bridge.cycle()
        self.assertEqual([s['text'][:4] for s in relay.sent], ['（一起听', '在吗'])
        song, chat = relay.sent
        self.assertEqual(chat['reply_route'], {'channel': 'netease', 'account': '5', 'peer': 'R1'})
        self.assertEqual(chat['api_session'], 's1')
        self.assertEqual(chat['attachments'], [])
        self.assertTrue(song['attachments'][0]['poke'])

        relay.history = [
            {'id': 60, 'from': 'ai', 'kind': 'reply', 'text': '在呢\n这首我也喜欢',
             'meta': {'reply_route': chat['reply_route'], 'in_reply_to': [chat['id']], 'api_session': 's1'}},
            {'id': 61, 'from': 'ai', 'kind': 'reply', 'text': 'not ours',
             'meta': {'reply_route': chat['reply_route'], 'in_reply_to': [999], 'api_session': 's1'}},
        ]
        bridge.cycle()
        self.assertEqual(sidecar.sent, ['在呢', '这首我也喜欢'])
        self.assertEqual(store.reply_counts(), {'sent': 1})
        bridge.cycle()
        self.assertEqual(len(sidecar.sent), 2)  # nothing is said twice


if __name__ == '__main__':
    unittest.main()


class TwoAccountTest(unittest.TestCase):
    """TOGETHER_COOKIE: the extension acts as the AI account, the normal tools stay on hers."""

    def test_extension_requests_use_ai_cookie(self):
        import server
        from together.api import TogetherApi
        seen = []
        original = server.netease_weapi
        server.netease_weapi = lambda path, payload=None: (
            seen.append((server.current_cookie(), server.get_csrf())) or
            {'code': 200, 'account': {'id': 7}, 'profile': {'nickname': 'kael'}})
        saved = (server.NETEASE_COOKIE, server.NETEASE_CSRF)
        server.NETEASE_COOKIE, server.NETEASE_CSRF = 'MUSIC_U=hers', 'hercsrf'
        try:
            api = TogetherApi(server, 'MUSIC_U=his; __csrf=hiscsrf')
            self.assertEqual(api.account_profile()['id'], '7')
            server.netease_weapi('/x')  # a normal tool call, outside the extension
            self.assertEqual(seen, [('MUSIC_U=his; __csrf=hiscsrf', 'hiscsrf'), ('MUSIC_U=hers', 'hercsrf')])
            # her NETEASE_CSRF never leaks into the AI account's cookie
            with server.using_cookie('MUSIC_U=his'):
                self.assertEqual(server.get_csrf(), '')
                self.assertNotIn('hercsrf', server.build_cookie_header())
            self.assertEqual(server.current_cookie(), 'MUSIC_U=hers')
        finally:
            server.netease_weapi = original
            server.NETEASE_COOKIE, server.NETEASE_CSRF = saved


class RealRoomShapesTest(unittest.TestCase):
    """Message shapes captured from a real room on 2026-11-22 (ids shortened, client info removed)."""

    def test_chat_nick_from_server_ext(self):
        raw = {'from_id_': '99', 'from_nick_': '', 'msg_type_': 0, 'msg_attach_': '你之前的消息我看到了。',
               'msg_body_': '', 'client_msg_id_': 'm1', 'timetag_': '1795350526724',
               'msg_setting_': {'ext_': json.dumps({'serverExt': {'nickname': 'listener', 'userId': 99},
                                                   'clientExt': {'bizType': 'listenTogether', 'ltType': 'FRIEND'}})}}
        chat = decode_chat(raw)
        self.assertEqual((chat['text'], chat['sender_nick'], chat['sender_id']), ('你之前的消息我看到了。', 'listener', '99'))

    def test_next_command(self):
        raw = {'from_id_': '99', 'msg_type_': 100, 'msg_attach_': json.dumps({'msgType': 120, 'content': {
            'type': 20000, 'bizType': 3, 'content': {
                'serverSeq': 1795350470838, 'roomId': 'R', 'commandType': 'NEXT', 'formerSongId': '3428528636',
                'targetSongId': '17793578', 'progress': 0, 'playStatus': 'PLAY', 'clientSeq': 11, 'sendUid': 99,
                'operateMsg': {'7': '对方刚刚切歌了'}}}})}
        p = decode_playback(raw)
        self.assertEqual((p['command'], p['song_id'], p['former_song_id'], p['status'], p['server_seq']),
                         ('NEXT', '17793578', '3428528636', 'PLAY', 1795350470838))

    def test_listen_count_is_not_playback(self):
        raw = {'msg_type_': 100, 'msg_attach_': json.dumps({'msgType': 120, 'content': {
            'type': 20010, 'bizType': 3, 'content': {'serverSeq': 1, 'roomId': 'R', 'listenCount': 442, 'duration': 164179}}})}
        self.assertIsNone(decode_playback(raw))
