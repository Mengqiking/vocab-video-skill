import json
import math
import re
import subprocess
import wave
from array import array

FPS = 30
SAMPLE_RATE = 48000
OPENING_FORMAT = 1


def write_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def ffmpeg(*args):
    return subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostdin", *map(str, args)],
        capture_output=True, text=True, check=True,
    ).stderr


def tokens(text):
    return re.findall(r"[a-z]+", text.lower())


def align(words, events, duration, trim_seconds):
    flat = [(token, event) for event in events for token in tokens(event["text"])]
    cursor = 0
    timings = []
    for word in words:
        target = tokens(word["text"])
        for index in range(cursor, len(flat) - len(target) + 1):
            span = flat[index:index + len(target)]
            if [token for token, _ in span] == target:
                break
        else:
            raise ValueError(f"缺少词边界：{word['text']}")
        first, last = span[0][1], span[-1][1]
        start = first["offset"] / 1e7 - trim_seconds
        end = (last["offset"] + last["duration"]) / 1e7 - trim_seconds
        if not (timings[-1]["endSeconds"] if timings else 0) <= start < end <= duration:
            raise ValueError(f"词边界超出音轨或重叠：{word['text']} {start}–{end}")
        timings.append({
            "id": word["id"], "text": word["text"], "startSeconds": start,
            "endSeconds": end, "revealFrame": math.ceil(start * FPS - 1e-8),
        })
        cursor = index + len(target)
    return timings


def trim_opening(decoded, output):
    with wave.open(str(decoded)) as audio:
        params = audio.getparams()
        pcm = array("h", audio.readframes(params.nframes))

    step = SAMPLE_RATE // 200
    first_signal = next(
        (i for i in range(0, SAMPLE_RATE // 4, step)
         if max(map(abs, pcm[i:i + step]), default=0) >= 80),
        0,
    )
    # Leave one 5 ms window before the first detectable sound.
    trim_samples = max(0, first_signal - step)
    del pcm[:trim_samples]
    with wave.open(str(output), "wb") as audio:
        audio.setparams(params)
        audio.writeframes(pcm.tobytes())
    return pcm, trim_samples / SAMPLE_RATE
