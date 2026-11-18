"""生成独立的情境词汇短视频和验收记录。"""

import argparse
import asyncio
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import wave

import numpy as np

from assemble import assemble
from audio_base import align, ffmpeg, tokens, trim_opening, write_json
from tempo import ENGLISH_TEMPO, RATE, VOICE, edit_phrases, remap_events, synthesize
from verify import verify

SAMPLE_RATE = 48000


def read_lesson(path):
    lesson = json.loads(path.read_text(encoding="utf-8"))
    if not lesson["title"].strip() or re.search(r'[\\/:*?"<>|\x00-\x1f]', lesson["title"]):
        raise ValueError("标题含不适合用作文件名的字符")
    words = lesson["words"]
    ids = [word["id"] for word in words]
    parts = lesson["parts"]
    if len(ids) != 8 or len(set(ids)) != 8 or len({w["text"].casefold() for w in words}) != 8:
        raise ValueError("需要八个不同的目标项")
    if [p["word"] for p in parts if isinstance(p, dict)] != ids:
        raise ValueError("正文必须按词表顺序引用八个目标项")
    if any(not all(w.get(k) for k in ("text", "ipa", "pos", "meaning")) for w in words):
        raise ValueError("词条须含英文、音标、词性和释义")
    mapping = {word["id"]: f" {word['text']} " for word in words}
    lesson["text"] = "".join(mapping[p["word"]] if isinstance(p, dict) else p for p in parts)
    return lesson


def prepare(lesson, folder, args):
    words = lesson["words"]
    expected = [token for word in words for token in tokens(word["text"])]
    spoken = args.tts_text.read_text(encoding="utf-8") if args.tts_text else lesson["text"]
    if any(not tokens(word["text"]) for word in words) or tokens(spoken) != expected or tokens(lesson["text"]) != expected:
        raise ValueError("口播稿与正文的英文必须与八个目标项一致")
    stored_audio = folder / ("original" + args.audio.suffix.lower() if args.audio else "original.mp3")
    edge_path = folder / "原始词边界.jsonl"
    if args.audio is None:
        asyncio.run(synthesize(spoken, stored_audio, edge_path))
    else:
        shutil.copyfile(args.audio, stored_audio)
        shutil.copyfile(args.boundaries, edge_path)
    events = [json.loads(line) for line in edge_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if any(event["type"] != "WordBoundary" for event in events):
        raise ValueError("需要 WordBoundary JSONL")
    if [token for event in events for token in tokens(event["text"])] != expected:
        raise ValueError("词边界中的英文与词表不符")

    analysis = ffmpeg("-i", stored_audio, "-af",
                      "loudnorm=I=-18:TP=-1.5:LRA=11:print_format=json", "-f", "null", "-")
    stats = json.loads(analysis[analysis.rindex("{"):analysis.rindex("}") + 1])
    gain = min(-18 - float(stats["input_i"]), -1.5 - float(stats["input_tp"]))
    if not np.isfinite(gain):
        raise ValueError("源音轨没有可用的语音")
    decoded = folder / "decoded.wav"
    final = folder / "narration.wav"
    ffmpeg("-loglevel", "error", "-i", stored_audio, "-af", f"volume={gain:.3f}dB",
           "-ar", SAMPLE_RATE, "-ac", 1, "-c:a", "pcm_s16le", decoded)
    pcm, trim = trim_opening(decoded, final)
    decoded.unlink()
    source_pcm = np.asarray(pcm, dtype="<i2")
    old_timings = align(words, events, len(pcm) / SAMPLE_RATE, trim)
    edited, sections = edit_phrases(source_pcm, old_timings, SAMPLE_RATE)
    mapped = remap_events(events, sections, SAMPLE_RATE, trim)
    duration = len(edited) / SAMPLE_RATE
    timings = align(words, mapped, duration, trim)
    with wave.open(str(final), "wb") as wav:
        wav.setparams((1, 2, SAMPLE_RATE, 0, "NONE", "not compressed"))
        wav.writeframes(edited.tobytes())
    (folder / "boundaries.jsonl").write_text(
        "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in mapped), encoding="utf-8"
    )
    pcm32 = edited.astype(np.int32)
    peaks = [round(float(np.max(np.abs(pcm32[i:i + 400]))) / 32768, 4)
             for i in range(0, len(pcm32), 400)]
    write_json(folder / "lesson.json", lesson)
    write_json(folder / "timeline.json", {
        "provider": "导入音频" if args.audio else "Edge TTS",
        "voice": None if args.audio else VOICE, "rate": None if args.audio else RATE,
        "fps": 30, "englishTempo": ENGLISH_TEMPO, "narrationSeconds": duration,
        "leadingTrimSeconds": trim, "gainDb": round(gain, 3), "timings": timings,
    })
    write_json(folder / "render-props.json", {
        "lesson": lesson, "narrationSeconds": duration, "timings": timings, "peaks": peaks,
    })
    return source_pcm, sections


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("lesson", type=Path)
    parser.add_argument("--output", type=Path, required=True, help="新建的成片目录")
    parser.add_argument("--tts-text", type=Path, help="可选的独立口播稿")
    parser.add_argument("--audio", type=Path, help="离线模式的原声")
    parser.add_argument("--boundaries", type=Path, help="离线原声对应的 WordBoundary JSONL")
    args = parser.parse_args()
    if bool(args.audio) != bool(args.boundaries) or (args.audio and args.tts_text):
        parser.error("--audio 和 --boundaries 必须一起提供，且不能和 --tts-text 同用")
    output = args.output.absolute()
    if output.exists() or output.is_symlink():
        parser.error("输出目录已存在，请指定新的目录")
    output.parent.mkdir(parents=True, exist_ok=True)
    lesson = read_lesson(args.lesson)
    with tempfile.TemporaryDirectory(dir=output.parent) as scratch:
        work = Path(scratch)
        folder = work / "video"
        folder.mkdir()
        pcm, sections = prepare(lesson, folder, args)
        subprocess.run(["node", str(Path(__file__).with_name("render.mjs")), str(folder)], check=True)
        frame_list = folder / "frames.json"
        frames = json.loads(frame_list.read_text())
        ffmpeg("-loglevel", "error", "-i", frames[0][1], "-vf",
               "pad=1080:1440:0:180:0xf9f6ec", "-frames:v", 1, "-q:v", 2, folder/"封面.jpg")
        assemble({"folder": str(folder), "frames": frames}, work)
        report = verify(folder, pcm, sections)
        write_json(folder / "验收记录.json", report)
        for _, frame in frames:
            Path(frame).unlink()
        frame_list.unlink()
        folder.rename(output)
        print(f"{output / (lesson['title'] + '.mp4')} · {report['durationSeconds']:.2f} 秒")


if __name__ == "__main__":
    main()
