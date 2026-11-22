import unittest

import helpers  # noqa: F401  (puts server/mcp-server on sys.path)

try:
    import numpy as np
except ImportError:  # the sketch extension is optional; so are its tests
    np = None


def tone(freqs, seconds, sr, amp=0.2):
    t = np.arange(int(seconds * sr)) / sr
    return sum(amp * np.sin(2 * np.pi * f * t) for f in freqs).astype(np.float32)


@unittest.skipUnless(np, "numpy not installed")
class SketchTest(unittest.TestCase):
    def setUp(self):
        from sketch import analysis
        self.a = analysis
        self.sr = analysis.SAMPLE_RATE

    def song(self):
        """A-minor chords (Am - Dm - E - Am), a click every 0.5 s (120 BPM), a 3 s gap, a loud ending."""
        sr = self.sr
        A, C, D, E, F, Gs, B = 220.0, 261.63, 293.66, 329.63, 349.23, 415.30, 246.94
        chords = [(A, C, E), (D, F, A * 2), (E, Gs, B), (A, C, E)]
        body = np.concatenate([tone(c, 4, sr) for c in chords * 3])            # 48 s
        clicks = np.zeros_like(body)
        for i in range(0, len(body) - 200, sr // 2):
            clicks[i:i + 200] += np.hanning(200).astype(np.float32) * 0.8
        gap = np.zeros(3 * sr, dtype=np.float32)
        ending = tone((A, C, E), 8, sr, amp=0.6)
        return np.concatenate([body + clicks, gap, ending])

    def test_measurements(self):
        r = self.a.analyse(self.song(), self.sr)
        self.assertEqual((r['key']['tonic'], r['key']['mode']), ('A', 'minor'))
        self.assertTrue(115 <= r['tempo_bpm'] <= 125, r['tempo_bpm'])
        self.assertEqual(len(r['energy']['bars']), 8)
        self.assertEqual(r['energy']['loudest_section'], 7)          # the loud ending
        self.assertTrue(any(47 <= a <= 49 and 50 <= b <= 52 for a, b in r['quiet']), r['quiet'])
        lines = self.a.describe(r, trial=True)
        self.assertTrue(lines[0].startswith('调性：A 小调'))
        self.assertIn('试听片段', lines[-1])

    def test_silence(self):
        r = self.a.analyse(np.zeros(self.sr * 5, dtype=np.float32), self.sr)
        self.assertEqual(self.a.describe(r), ['这段音频几乎没有声音。'])


if __name__ == '__main__':
    unittest.main()
