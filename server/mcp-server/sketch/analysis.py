"""A short sketch of how a song is built, from its audio: key, tempo, energy arc, brightness,
quiet stretches. numpy only, small frames, one pass in chunks, so a 3-minute song stays
within a few tens of MB.

Everything here works on mono float32 samples at SAMPLE_RATE. Nothing is kept afterwards.
"""
import numpy as np

SAMPLE_RATE = 11025          # enough for pitch classes and rhythm; keeps memory and CPU small
NOTE_NAMES = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']
NOTE_NAMES_ZH_FLAT = {'C#': 'C#', 'D#': 'Eb', 'F#': 'F#', 'G#': 'Ab', 'A#': 'Bb'}

# Krumhansl-Kessler key profiles
MAJOR = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
MINOR = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])

BARS = '▁▂▃▄▅▆▇█'


def _frames(x, size, hop):
    """Frame start offsets (no padding beyond the end)."""
    if len(x) < size:
        return np.array([0])
    return np.arange(0, len(x) - size + 1, hop)


def _stft_chunks(x, size, hop, chunk=256):
    """Yield magnitude spectra for consecutive groups of frames (bounded memory)."""
    window = np.hanning(size).astype(np.float32)
    starts = _frames(x, size, hop)
    for i in range(0, len(starts), chunk):
        idx = starts[i:i + chunk, None] + np.arange(size)[None, :]
        seg = x[np.minimum(idx, len(x) - 1)] * window
        yield np.abs(np.fft.rfft(seg, axis=1)).astype(np.float32)


def estimate_key(x, sr=SAMPLE_RATE):
    """(tonic, 'major'|'minor', confidence 0..1) from a long-term pitch-class profile."""
    size, hop = 4096, 2048
    freqs = np.fft.rfftfreq(size, 1 / sr)
    usable = (freqs >= 55) & (freqs <= 2000)
    pcs = (np.round(12 * np.log2(freqs[usable] / 440.0)) + 69).astype(int) % 12
    chroma = np.zeros(12)
    for mag in _stft_chunks(x, size, hop):
        m = np.sqrt(mag[:, usable])                      # soften loud partials
        frame_chroma = np.zeros((m.shape[0], 12))
        for pc in range(12):
            frame_chroma[:, pc] = m[:, pcs == pc].sum(axis=1)
        norms = frame_chroma.sum(axis=1, keepdims=True)
        chroma += (frame_chroma / np.maximum(norms, 1e-9)).sum(axis=0)
    if not chroma.any():
        return None, None, 0.0
    scores = []
    for mode, profile in (('major', MAJOR), ('minor', MINOR)):
        for tonic in range(12):
            scores.append((np.corrcoef(chroma, np.roll(profile, tonic))[0, 1], tonic, mode))
    scores.sort(reverse=True)
    best, second = scores[0], scores[1]
    confidence = float(max(0.0, min(1.0, (best[0] - second[0]) * 5 + max(best[0], 0) * 0.5)))
    return NOTE_NAMES[best[1]], best[2], round(confidence, 2)


def estimate_tempo(x, sr=SAMPLE_RATE, lo=60, hi=180):
    """Beats per minute from the autocorrelation of an onset (spectral flux) envelope."""
    size, hop = 1024, 256
    flux, prev = [], None
    for mag in _stft_chunks(x, size, hop):
        logm = np.log1p(mag * 10)
        if prev is not None:
            logm_full = np.vstack([prev[None, :], logm])
        else:
            logm_full = np.vstack([logm[:1], logm])
        flux.append(np.maximum(np.diff(logm_full, axis=0), 0).sum(axis=1))
        prev = logm[-1]
    env = np.concatenate(flux) if flux else np.zeros(1)
    if len(env) < 64:
        return None
    env = env - np.convolve(env, np.ones(16) / 16, mode='same')   # remove slow loudness drift
    env = np.maximum(env, 0)
    rate = sr / hop
    ac = np.correlate(env, env, mode='full')[len(env) - 1:]
    lags = np.arange(len(ac))
    valid = (lags >= rate * 60 / hi) & (lags <= rate * 60 / lo)
    if not valid.any() or ac[0] <= 0:
        return None
    bpm_at = 60 * rate / np.maximum(lags, 1)
    weight = np.exp(-0.5 * (np.log2(bpm_at / 120.0) / 0.9) ** 2)  # mild preference for ordinary tempos
    score = np.where(valid, ac * weight, -np.inf)
    lag = int(np.argmax(score))
    return int(round(60 * rate / lag))


def frame_levels(x, sr=SAMPLE_RATE, frame_s=0.1):
    """RMS level per frame, in dB relative to the loudest frame."""
    n = max(1, int(sr * frame_s))
    usable = len(x) // n * n
    if usable == 0:
        return np.zeros(1), frame_s
    rms = np.sqrt(np.mean(x[:usable].reshape(-1, n) ** 2, axis=1) + 1e-12)
    return 20 * np.log10(rms / rms.max()), frame_s


def energy_arc(levels, sections=8):
    """Mean level of each section as bar characters, plus the loudest / softest section index."""
    parts = np.array_split(levels, sections)
    means = np.array([p.mean() if len(p) else -80.0 for p in parts])
    span = max(means.max() - means.min(), 1e-6)
    bars = ''.join(BARS[int(round((m - means.min()) / span * (len(BARS) - 1)))] for m in means)
    return bars, int(np.argmax(means)), int(np.argmin(means)), float(span)


def quiet_stretches(levels, frame_s, threshold_db=-35.0, min_s=1.5, top=3):
    """Longest runs below threshold (relative to the loudest frame), as (start_s, end_s)."""
    quiet = levels < threshold_db
    runs, start = [], None
    for i, q in enumerate(np.append(quiet, False)):
        if q and start is None:
            start = i
        elif not q and start is not None:
            if (i - start) * frame_s >= min_s:
                runs.append((start * frame_s, i * frame_s))
            start = None
    runs.sort(key=lambda r: r[0] - r[1])
    return sorted(runs[:top])


def brightness(x, sr=SAMPLE_RATE):
    """Average spectral centroid in Hz (within 0..sr/2)."""
    size, hop = 2048, 2048
    freqs = np.fft.rfftfreq(size, 1 / sr)
    total, weight = 0.0, 0.0
    for mag in _stft_chunks(x, size, hop):
        energy = mag.sum(axis=1)
        loud = energy > energy.max() * 0.05 if energy.max() > 0 else energy > 0
        if loud.any():
            total += float((mag[loud] @ freqs).sum())
            weight += float(energy[loud].sum())
    return total / weight if weight else None


def analyse(samples, sr=SAMPLE_RATE):
    """Mono float32 samples -> dict with the five measurements."""
    x = np.asarray(samples, dtype=np.float32)
    peak = float(np.abs(x).max()) if len(x) else 0.0
    if peak < 1e-4:
        return {'duration_s': len(x) / sr, 'silent': True}
    x = x / peak
    tonic, mode, confidence = estimate_key(x, sr)
    levels, frame_s = frame_levels(x, sr)
    bars, loudest, softest, span = energy_arc(levels)
    return {
        'duration_s': round(len(x) / sr, 1),
        'key': {'tonic': tonic, 'mode': mode, 'confidence': confidence},
        'tempo_bpm': estimate_tempo(x, sr),
        'energy': {'bars': bars, 'loudest_section': loudest, 'softest_section': softest, 'range_db': round(span, 1)},
        'brightness_hz': (lambda b: round(b) if b else None)(brightness(x, sr)),
        'quiet': [(round(a, 1), round(b, 1)) for a, b in quiet_stretches(levels, frame_s)],
    }


def _clock(seconds):
    seconds = int(seconds)
    return f'{seconds // 60}:{seconds % 60:02d}'


def describe(result, trial=False):
    """The sketch as a few short lines of Chinese."""
    if result.get('silent'):
        return ['这段音频几乎没有声音。']
    lines = []
    key = result['key']
    if key['tonic']:
        name = NOTE_NAMES_ZH_FLAT.get(key['tonic'], key['tonic'])
        sure = '比较确定' if key['confidence'] >= 0.6 else ('大致' if key['confidence'] >= 0.3 else '不太确定，调性可能比较模糊或常转调')
        lines.append(f"调性：{name} {'大调' if key['mode'] == 'major' else '小调'}（{sure}）")
    if result['tempo_bpm']:
        lines.append(f"速度：约 {result['tempo_bpm']} BPM（从节拍算出来的，可能是实际的一半或两倍）")
    e = result['energy']
    n = len(e['bars'])
    seg = result['duration_s'] / n
    lines.append(f"能量起伏：{e['bars']}（从头到尾分 {n} 段）")
    lines.append(f"最满的一段在 {_clock(e['loudest_section'] * seg)}–{_clock((e['loudest_section'] + 1) * seg)}，"
                 f"最收的一段在 {_clock(e['softest_section'] * seg)}–{_clock((e['softest_section'] + 1) * seg)}"
                 + ('，整首起伏不大' if e['range_db'] < 4 else ''))
    b = result['brightness_hz']
    if b:
        feel = '偏暗、偏柔' if b < 1100 else ('偏亮、偏利' if b > 1900 else '不亮不暗')
        lines.append(f"音色明暗：{feel}")
    if result['quiet']:
        lines.append('安静段：' + '、'.join(f'{_clock(a)}–{_clock(b)}' for a, b in result['quiet']))
    if trial:
        lines.append(f"（只拿到 {_clock(result['duration_s'])} 的试听片段，以上只代表这一段）")
    return lines
