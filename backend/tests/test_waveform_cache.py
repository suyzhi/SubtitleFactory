"""Fingerprint reuse must not hide replacements or same-size audio edits."""
import hashlib
import os
from unittest.mock import patch

from app.services.waveform import _cached_fingerprint, audio_fingerprint


def test_fingerprint_reuses_content_hash_and_invalidates_edits(tmp_path):
    audio = tmp_path / 'audio.wav'
    audio.write_bytes(b'aaaa')
    _cached_fingerprint.cache_clear()
    first = audio_fingerprint(str(audio))
    with patch('builtins.open', side_effect=AssertionError('unchanged audio reread')):
        assert audio_fingerprint(str(audio)) == first
    old = audio.stat()
    audio.write_bytes(b'bbbb')
    os.utime(audio, ns=(old.st_atime_ns, old.st_mtime_ns))
    import sys
    if sys.platform != 'win32':
        assert audio_fingerprint(str(audio)) != first
    replacement = tmp_path / 'new.wav'
    replacement.write_bytes(b'cccc')
    replacement.replace(audio)
    assert audio_fingerprint(str(audio)) not in {first, hashlib.sha256(b'bbbb').hexdigest()}


def test_native_peaks_match_reference_for_pcm_extremes_and_uneven_buckets():
    import math
    import random
    from array import array

    from app.services.waveform import _peaks
    rng = random.Random(19)
    for size in (0, 1, 7, 101, 16001):
        samples = array('h', [rng.randint(-32768, 32767) for _ in range(size)])
        if size:
            samples[0] = -32768
        for requested in (1, 4, 1000, 4000, 16000):
            count = max(1, min(requested, size))
            expected = [round(max(abs(v) for v in samples[math.floor(i * size / count):max(math.floor(i * size / count) + 1, math.floor((i + 1) * size / count))]) / 32768, 4) for i in range(count)] if size else []
            assert _peaks(samples, requested) == expected
