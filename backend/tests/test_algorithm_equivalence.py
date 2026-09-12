"""Regression guards for the O(n) replacements of former scalar loops."""

import random
import wave
from array import array

import numpy as np

from app.services.diarization import _segment_speaker_labels
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


def _brute(segments, turns):
    assignments = []
    uncertain = set()
    for segment in segments:
        overlaps = {}
        duration = max(0.001, float(segment["end"]) - float(segment["start"]))
        for turn in turns:
            overlap = max(0, min(float(segment["end"]), turn["end"]) - max(float(segment["start"]), turn["start"]))
            overlaps[turn["speaker"]] = overlaps.get(turn["speaker"], 0) + overlap
        label, amount = max(overlaps.items(), key=lambda item: item[1])
        assignments.append(label if amount / duration >= 0.55 else None)
        if amount / duration < 0.75 or len([value for value in overlaps.values() if value > duration * .2]) > 1:
            uncertain.add(segment["id"])
    return assignments, uncertain


def test_segment_speaker_labels_matches_brute_force():
    rng = random.Random(11)
    for _ in range(300):
        segments = []
        for index in range(rng.randint(0, 25)):
            start = rng.uniform(-2, 40)
            segments.append({"id": f"s{index}", "start": start, "end": start + rng.choice([0.2, 1.0, 3.5, 9.0])})
        turns = []
        for _index in range(rng.randint(1, 25)):
            start = rng.uniform(-2, 40)
            turns.append({"start": start, "end": start + rng.uniform(0, 6), "speaker": rng.randint(0, 3)})
        rng.shuffle(segments)
        rng.shuffle(turns)
        got_labels, got_uncertain = _segment_speaker_labels(segments, turns)
        want_labels, want_uncertain = _brute(segments, turns)
        assert got_labels == want_labels
        assert {row["id"] for row in got_uncertain} == want_uncertain
