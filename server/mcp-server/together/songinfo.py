"""What NetEase itself knows about a song: the "音乐百科" block (style, tags, language, BPM,
awards) and the chorus position. Pure parsing; the requests live in api.py."""


def _title(element):
    return ((element or {}).get('mainTitle') or {}).get('title')


def parse_wiki(resp):
    """Response of /api/song/play/about/block/page -> {styles, tags, language, bpm, awards}."""
    info = {'styles': [], 'tags': [], 'language': None, 'bpm': None, 'awards': []}
    blocks = ((resp or {}).get('data') or {}).get('blocks') or [] if isinstance(resp, dict) else []
    for block in blocks:
        if not isinstance(block, dict) or block.get('code') != 'SONG_PLAY_ABOUT_SONG_BASIC':
            continue
        for creative in block.get('creatives') or []:
            if not isinstance(creative, dict):
                continue
            kind = creative.get('creativeType')
            names = [t for t in (_title(r.get('uiElement')) for r in creative.get('resources') or [] if isinstance(r, dict)) if t]
            texts = [t.get('text') for t in ((creative.get('uiElement') or {}).get('textLinks') or []) if isinstance(t, dict) and t.get('text')]
            if kind == 'songTag':
                info['styles'] = names
            elif kind == 'songBizTag':
                info['tags'] = names
            elif kind == 'language' and texts:
                info['language'] = texts[0]
            elif kind == 'bpm' and texts:
                try:
                    info['bpm'] = int(float(texts[0]))
                except ValueError:
                    pass
            elif kind == 'songAward':
                info['awards'] = names
    return info


def parse_chorus(resp, song_id):
    """Response of /api/song/chorus -> (start_ms, end_ms) or None."""
    for item in ((resp or {}).get('chorus') or []) if isinstance(resp, dict) else []:
        if isinstance(item, dict) and str(item.get('id')) == str(song_id):
            start, end = item.get('startTime'), item.get('endTime')
            if isinstance(start, (int, float)) and isinstance(end, (int, float)) and end > start >= 0:
                return int(start), int(end)
    return None


def _clock(ms):
    return f'{ms // 60000}:{ms // 1000 % 60:02d}'


def describe(info):
    """One short line, e.g. '嘻哈说唱 · 活力 · 156 BPM · 英语 · 副歌 0:23–0:49', or None."""
    if not info:
        return None
    parts = list(info.get('styles') or []) + list(info.get('tags') or [])
    if info.get('bpm'):
        parts.append(f"{info['bpm']} BPM")
    if info.get('language'):
        parts.append(info['language'])
    if info.get('chorus'):
        parts.append(f"副歌 {_clock(info['chorus'][0])}–{_clock(info['chorus'][1])}")
    parts += [f'获奖：{a}' for a in (info.get('awards') or [])[:1]]
    return ' · '.join(parts) or None
