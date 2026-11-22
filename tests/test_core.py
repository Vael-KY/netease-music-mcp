"""Core tools and crypto. No network: NetEase calls are replaced with fakes."""
import json
import unittest

from helpers import load_server


class CryptoTest(unittest.TestCase):
    def setUp(self):
        self.s = load_server()

    def test_aes_block_matches_fips197(self):
        rks = self.s._expand_key(bytes(range(16)))
        out = bytes(self.s._encrypt_block(list(bytes.fromhex('00112233445566778899aabbccddeeff')), rks))
        self.assertEqual(out.hex(), '69c4e0d86a7b0430d8cdb78070b4c55a')

    def test_eapi_matches_reference_implementation(self):
        # Reference value produced by Node's crypto (aes-128-ecb) with the same inputs.
        expected = ('6E4330E883BF6CFF05B9097AD729A4561410EB53BCFA26F342976D596502675F4370AE03D9228133'
                    'D8A3DCB0FDD7988F9B1225E215B37AFB24350DB88AD541088AA4DD0EE6B9F90792EE2BAD8B5EA7C2'
                    'E6EDD15794F366CCE8CA9A1D5C3395A875741AA0B8D6E94190905327510D2568FB9F9A617E778886'
                    '114A459E08ED7B327A10D1F07B4A20DA107581564D033F1E')
        got = self.s.eapi_encrypt('/api/listen/together/heartbeat', {'roomId': 'r1', 'songId': '42', 'progress': 1000})
        self.assertEqual(got['params'], expected)

    def test_weapi_shape(self):
        out = self.s.weapi_encrypt({'a': 1})
        self.assertEqual(len(out['encSecKey']), 256)
        self.assertTrue(out['params'])


def record(name, artist, plays, album='A', sid=1):
    return {'playCount': plays, 'score': 100, 'song': {'id': sid, 'name': name, 'ar': [{'name': artist}], 'al': {'name': album}}}


class ToolTest(unittest.TestCase):
    def setUp(self):
        self.s = load_server()
        self.s.get_uid = lambda: '1'

    def test_play_history_shows_play_count_not_score(self):
        self.s.netease_weapi = lambda path, payload=None: {'code': 200, 'weekData': [record('Song', 'Singer', 7)]}
        out = self.s.get_play_history({})
        self.assertIn('plays: 7', out['history'][0])

    def test_listening_profile_aggregates(self):
        records = [record('a', 'X', 5, 'Al1', 1), record('b', 'X', 3, 'Al2', 2), record('c', 'Y', 4, 'Al1', 3)]
        out = self.s.summarise_play_records(records, top_n=2)
        self.assertEqual(out['total_plays'], 12)
        self.assertEqual(out['top_artists'], ['X (8 plays)', 'Y (4 plays)'])
        self.assertEqual(out['top_albums'][0], 'Al1 (9 plays)')
        self.assertTrue(out['top_songs'][0].startswith('a - X'))

    def test_reorder_refuses_incomplete_list(self):
        self.s.netease_request = lambda path, data=None, method='POST': {
            'code': 200, 'playlist': {'trackIds': [{'id': 1}, {'id': 2}, {'id': 3}]}}
        writes = []
        self.s.write_call = lambda path, payload: writes.append(payload) or {'code': 200}
        out = self.s.reorder_playlist_tracks({'playlist_id': 9, 'song_ids': '3,1'})
        self.assertIn('error', out)
        self.assertEqual(out['missing'], [2])
        out = self.s.reorder_playlist_tracks({'playlist_id': 9, 'song_ids': '3,1,1,2'})
        self.assertEqual(out['duplicates'], [1])
        self.assertEqual(writes, [])
        out = self.s.reorder_playlist_tracks({'playlist_id': 9, 'song_ids': '3,1,2'})
        self.assertNotIn('error', out)
        self.assertEqual(json.loads(writes[0]['trackIds']), [3, 1, 2])

    def test_playlist_songs_uses_track_ids_for_big_playlists(self):
        def fake(path, data=None, method='POST'):
            if 'playlist/detail' in path:
                return {'code': 200, 'playlist': {'name': 'P', 'trackIds': [{'id': i} for i in (1, 2, 3)],
                                                  'tracks': [{'id': 1, 'name': 's1', 'ar': []}]}}
            ids = [c['id'] for c in json.loads(data['c'])]
            return {'code': 200, 'songs': [{'id': i, 'name': f's{i}', 'ar': [{'name': 'z'}]} for i in reversed(ids)]}
        self.s.netease_request = fake
        out = self.s.get_playlist_songs({'playlist_id': 5})
        self.assertEqual(out['total'], 3)
        self.assertEqual([line.split('. ')[1].split(' -')[0] for line in out['songs']], ['s1', 's2', 's3'])

    def test_private_text(self):
        self.assertEqual(self.s._private_text(json.dumps({'msg': 'hi'})), 'hi')
        self.assertIn('song: X', self.s._private_text(json.dumps({'msg': '', 'song': {'name': 'X', 'id': 1}})))
        self.assertEqual(self.s._private_text(json.dumps({'nativeUrl': 'orpheus://listenTogether?roomId=1'})),
                         '[Listen Together invitation]')

    def test_send_private_message_validates(self):
        self.assertIn('error', self.s.send_private_message({'user_id': 1, 'text': ' '}))
        self.assertIn('error', self.s.send_private_message({'user_id': 1, 'text': 'x' * 501}))
        sent = []
        self.s.netease_eapi = lambda path, payload=None: sent.append((path, payload)) or {'code': 200}
        self.assertNotIn('error', self.s.send_private_message({'user_id': 12, 'text': 'hello'}))
        self.assertEqual(sent[0], ('/api/msg/private/send', {'type': 'text', 'msg': 'hello', 'userIds': '[12]'}))

    def test_every_tool_is_registered(self):
        names = [t['name'] for t in self.s.TOOLS]
        self.assertEqual(len(names), len(set(names)))
        self.assertEqual(set(names), set(self.s.TOOL_DISPATCH))


if __name__ == '__main__':
    unittest.main()
