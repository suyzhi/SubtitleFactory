"""Regression guards for the O(n) replacements of former scalar loops."""

import random
import wave
from array import array

import numpy as np

from app.services.waveform import _mono_samples


def test_mono_downmix_matches_python_round(tmp_path):
    rng = random.Random(7)
    for channels in (1, 2, 5):
        samples = array("h", [rng.randint(-32768, 32767) for _ in range(channels * 777)])
        path = tmp_path / f"c{channels}.wav"
        with wave.open(str(path), "wb") as output:
            output.setnchannels(channels)
            output.setsampwidth(2)
            output.setframerate(16000)
            output.writeframes(samples.tobytes())
        expected = np.array(
            [round(sum(samples[offset:offset + channels]) / channels)
             for offset in range(0, len(samples), channels)],
            dtype=np.int16,
        )
        with wave.open(str(path), "rb") as source:
            got = _mono_samples(source)
        assert np.array_equal(got, expected)
