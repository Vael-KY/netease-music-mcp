"""Song sketch extension (optional; SKETCH_ENABLED=true). One tool, song_sketch: fetch a song's
audio into memory, measure key, tempo, energy arc, brightness and quiet stretches, return a
few lines of text, and drop the audio. Needs numpy and miniaudio (pip install numpy miniaudio);
without them the tool is simply not listed.

Nothing is written to disk and nothing is cached: each call holds one song in memory while it
is measured, and only one song is measured at a time.
"""
import gc
import json
import logging
import threading
import urllib.request

logger = logging.getLogger('netease-mcp')

MAX_AUDIO_BYTES = 25 * 1024 * 1024
STATE = {}
_busy = threading.Lock()

TOOL = {
    "name": "song_sketch",
    "description": ("Listen to a song's audio once and sketch how it is built: key (major/minor), tempo, "
                    "energy arc across the song, brighter or darker sound, quiet stretches, plus NetEase's own "
                    "style/tag/BPM/chorus notes. Takes a few seconds; one song at a time."),
    "inputSchema": {"type": "object", "properties": {"song_id": {"type": "integer", "description": "Song ID"}},
                    "required": ["song_id"]},
}


def _audio_url(core, song_id):
    r = core.netease_weapi('/song/enhance/player/url/v1',
                           {'ids': json.dumps([int(song_id)]), 'level': 'standard', 'encodeType': 'mp3'})
    item = ((r or {}).get('data') or [{}])[0] if isinstance(r, dict) else {}
    return item.get('url'), bool(item.get('freeTrialInfo'))


def _download(url):
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0', 'Referer': 'https://music.163.com/'})
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = resp.read(MAX_AUDIO_BYTES + 1)
    if len(data) > MAX_AUDIO_BYTES:
        raise ValueError('audio larger than expected')
    return data


def _netease_notes(core, song_id):
    try:
        from together.songinfo import describe, parse_chorus, parse_wiki
        info = parse_wiki(core.netease_eapi('/api/song/play/about/block/page', {'songId': str(song_id)}))
        chorus = parse_chorus(core.netease_eapi('/api/song/chorus', {'ids': json.dumps([str(song_id)])}), song_id)
        if chorus:
            info['chorus'] = chorus
        return describe(info)
    except Exception:
        return None


def _title(core, song_id):
    try:
        r = core.netease_request('/api/v3/song/detail', data={'c': json.dumps([{'id': int(song_id)}])})
        s = (r.get('songs') or [{}])[0]
        from together.api import _clean   # drops invisible direction marks NetEase leaves in names
        artists = ' / '.join(_clean(a.get('name')) or '?' for a in s.get('ar') or [])
        return f"《{_clean(s.get('name'))}》— {artists}" if s.get('name') else None
    except Exception:
        return None


def song_sketch(params):
    core, np, miniaudio = STATE['core'], STATE['np'], STATE['miniaudio']
    from .analysis import SAMPLE_RATE, analyse, describe
    try:
        song_id = int(params.get('song_id'))
    except (TypeError, ValueError):
        return {"error": "song_id required"}
    if not _busy.acquire(blocking=False):
        return {"error": "正在听另一首，等几秒再试"}
    try:
        # title and NetEase's notes are fetched while the audio downloads
        side = {}
        helper = threading.Thread(target=lambda: side.update(song=_title(core, song_id), notes=_netease_notes(core, song_id)),
                                  daemon=True)
        helper.start()
        url, trial = _audio_url(core, song_id)
        if not url:
            return {"error": "拿不到这首歌的音频（可能需要会员、已下架或地区限制）", "song_id": song_id}
        data = _download(url)
        decoded = miniaudio.decode(data, output_format=miniaudio.SampleFormat.FLOAT32,
                                   nchannels=1, sample_rate=SAMPLE_RATE)
        del data
        samples = np.frombuffer(decoded.samples, dtype=np.float32)
        result = analyse(samples, SAMPLE_RATE)
        del samples, decoded
        helper.join(timeout=20)
        out = {"song": side.get('song'), "sketch": describe(result, trial)}
        if side.get('notes'):
            out["netease"] = side['notes']
        return out
    except Exception as e:
        logger.warning("song_sketch failed for %s: %s", song_id, type(e).__name__)
        return {"error": f"这首没听成（{type(e).__name__}）", "song_id": song_id}
    finally:
        gc.collect()
        _busy.release()


def setup(core):
    """Called by the core server when SKETCH_ENABLED is on."""
    try:
        import numpy
        import miniaudio
    except ImportError as e:
        logger.warning("Song sketch: %s is not installed (pip install numpy miniaudio); song_sketch not listed", e.name)
        return False
    STATE.update(core=core, np=numpy, miniaudio=miniaudio)
    core.TOOLS.append(TOOL)
    core.TOOL_DISPATCH['song_sketch'] = song_sketch
    return True
