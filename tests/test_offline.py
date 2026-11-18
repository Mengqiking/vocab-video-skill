"""合成可重复的音频夹具，离线验证变速、对齐和可选的完整渲染。"""

import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import wave

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from build import prepare, read_lesson
from annotation_qa import contains_stray_text
from types import SimpleNamespace

RATE = 48000


def fixture(directory, lesson):
    audio = directory / "fixture.wav"
    metadata = directory / "fixture.jsonl"
    events = []
    chunks = []
    cursor = 0
    for i, word in enumerate(lesson["words"]):
        gap = round(RATE * 0.4)
        span = round(RATE * 0.45)
        samples = np.arange(gap + span)
        tone = np.rint(6000 * np.sin(samples * (2 * math.pi * (200 + 17 * i) / RATE)))
        chunks.append(tone.astype("<i2"))
        events.append({"type": "WordBoundary", "text": word["text"],
                       "offset": round((cursor + gap) * 1e7 / RATE),
                       "duration": round(span * 1e7 / RATE)})
        cursor += gap + span
    chunks.append(np.zeros(RATE // 4, dtype="<i2"))
    with wave.open(str(audio), "wb") as wav:
        wav.setparams((1, 2, RATE, 0, "NONE", "not compressed"))
        wav.writeframes(np.concatenate(chunks).tobytes())
    metadata.write_text("".join(json.dumps(e) + "\n" for e in events))
    return audio, metadata


class OfflineTests(unittest.TestCase):
    def test_stray_annotation_ignores_scattered_compression_speckles(self):
        mask = np.zeros((784, 1080), dtype=bool)
        mask[100, ::30] = True
        self.assertFalse(contains_stray_text(mask))
        mask[150:165, 145:147] = True
        self.assertTrue(contains_stray_text(mask))

    def test_prepare_preserves_audio_outside_targets_and_shifts_frames(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            lesson = read_lesson(ROOT / "examples/library.json")
            audio, metadata = fixture(root, lesson)
            out = root / "prepared"
            out.mkdir()
            source, sections = prepare(lesson, out, SimpleNamespace(audio=audio, boundaries=metadata, tts_text=None))
            with wave.open(str(out / "narration.wav")) as wav:
                edited = np.frombuffer(wav.readframes(wav.getnframes()), dtype="<i2")
            timing = json.loads((out / "timeline.json").read_text())
            self.assertEqual(len(sections), 8)
            old_end = new_end = 0
            for i, (a, b, c, d) in enumerate(sections):
                self.assertTrue(np.array_equal(source[old_end:a], edited[new_end:c]))
                self.assertTrue(0.87 <= (b-a)/(d-c) <= 0.89)
                self.assertGreaterEqual(c, a)
                item = timing["timings"][i]
                self.assertEqual(round(item["startSeconds"] * RATE), c)
                self.assertEqual(round(item["endSeconds"] * RATE), d)
                self.assertEqual(item["revealFrame"], math.ceil(item["startSeconds"] * 30 - 1e-8))
                old_end, new_end = b, d
            self.assertTrue(np.array_equal(source[old_end:], edited[new_end:]))
            self.assertGreater(len(edited), len(source))

    def test_rejects_mismatched_word_boundaries(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            lesson = read_lesson(ROOT / "examples/library.json")
            audio, metadata = fixture(root, lesson)
            metadata.write_text(metadata.read_text().replace("study space", "changed"))
            with self.assertRaisesRegex(ValueError, "英文与词表不符"):
                prepare(lesson, root, SimpleNamespace(audio=audio, boundaries=metadata, tts_text=None))

    @unittest.skipUnless(os.environ.get("VOCAB_RENDER_TEST") == "1", "显式启用完整渲染检查")
    def test_build_and_verify_offline(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            example = ROOT / "examples/library.json"
            audio, metadata = fixture(root, read_lesson(example))
            out = root / "out"
            subprocess.run([sys.executable, str(ROOT / "scripts/build.py"), str(example),
                            "--audio", str(audio), "--boundaries", str(metadata),
                            "--output", str(out)], check=True)
            report = json.loads((out / "验收记录.json").read_text())
            self.assertEqual(report["verifiedWords"], 8)
            self.assertEqual(report["recapRows"], 8)
            self.assertEqual(report["clozeBlanks"], 8)
            self.assertTrue(report["unchangedOutsideEnglish"])
            self.assertEqual(len(list(out.glob("*.mp4"))), 1)

    @unittest.skipUnless(os.environ.get("VOCAB_RENDER_TEST") == "1", "显式启用完整渲染检查")
    def test_wrapped_phrase_centers_under_last_line(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            lesson = json.loads((ROOT / "examples/library.json").read_text())
            lesson["parts"][0] = "为了赶上周末的读书会我提前到图书馆去预订"
            lesson["words"][0]["text"] = "shared library study space"
            script = root / "wrap.json"
            script.write_text(json.dumps(lesson, ensure_ascii=False))
            audio, metadata = fixture(root, lesson)
            out = root / "out"
            subprocess.run([sys.executable, str(ROOT / "scripts/build.py"), str(script),
                            "--audio", str(audio), "--boundaries", str(metadata),
                            "--output", str(out)], check=True)
            qa = json.loads((out / "验收记录.json").read_text())["annotationQA"]
            first = qa["words"][0]
            self.assertEqual(first["highlightLines"], 2)
            # 末行必须含多个英文词，避免退化为只测末词居中。
            self.assertGreater(first["lastLineWidthPx"], 160)
            self.assertLessEqual(first["centerErrorPx"], 2)
            self.assertGreater(qa["checkedFrames"], 200)
