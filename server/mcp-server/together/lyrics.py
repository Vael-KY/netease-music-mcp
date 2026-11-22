"""LRC parsing: which lyric lines are around the current playback position."""
import re

_TAG = re.compile(r'\[(\d+):(\d+)(?:[.:](\d+))?\]')


def parse_lrc(text):
    """'[01:02.30]line' -> sorted [(ms, line)]. Metadata/empty lines are dropped."""
    lines = []
    for raw in (text or '').splitlines():
        tags = list(_TAG.finditer(raw))
        if not tags:
            continue
        words = raw[tags[-1].end():].strip()
        if not words:
            continue
        for t in tags:
            frac = t.group(3) or '0'
            ms = int(t.group(1)) * 60000 + int(t.group(2)) * 1000 + int(frac.ljust(3, '0')[:3])
            lines.append((ms, words))
    lines.sort(key=lambda item: item[0])
    return lines


def merge_translation(lines, translated):
    """Attach translation lines (same timestamps) as 'original / translation'."""
    by_ms = dict(translated or [])
    return [(ms, f"{words} / {by_ms[ms]}" if by_ms.get(ms) and by_ms[ms] != words else words) for ms, words in lines]


def lines_near(lines, progress_ms, before=2, after=4):
    """The current line (marked) with a few lines of context on each side."""
    if not lines:
        return []
    current = -1
    for i, (ms, _) in enumerate(lines):
        if ms <= progress_ms:
            current = i
        else:
            break
    start = max(0, current - before) if current >= 0 else 0
    end = (current + after + 1) if current >= 0 else after
    out = []
    for i in range(start, min(end, len(lines))):
        ms, words = lines[i]
        out.append(('> ' if i == current else '  ') + f"[{ms // 60000:02d}:{ms // 1000 % 60:02d}] {words}")
    return out
