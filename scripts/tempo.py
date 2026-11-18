"""生成 HsiaoChen 配音和情境背单词模板所需的逐词时间轴。"""

import subprocess

import aiohttp
import edge_tts
import numpy as np

VOICE = "zh-TW-HsiaoChenNeural"
RATE = "+8%"
ENGLISH_TEMPO = 0.88
TICKS_PER_SECOND = 10_000_000


def stretch_phrase(pcm, sample_rate):
    target_samples = round(len(pcm) / ENGLISH_TEMPO)
    tempo = ENGLISH_TEMPO
    best = None
    for _ in range(5):
        result = subprocess.run(
            ["ffmpeg", "-nostdin", "-v", "error", "-f", "s16le", "-ar", str(sample_rate),
             "-ac", "1", "-i", "pipe:0", "-af", f"atempo={tempo:.7f}",
             "-f", "s16le", "pipe:1"],
            input=pcm.tobytes(), capture_output=True, check=True,
        )
        candidate = np.frombuffer(result.stdout, dtype="<i2")
        if not len(candidate):
            raise ValueError("英文变速未生成音频")
        if best is None or abs(len(candidate) - target_samples) < abs(len(best) - target_samples):
            best = candidate
        if abs(len(candidate) - target_samples) <= sample_rate * 0.003:
            break
        tempo *= len(candidate) / target_samples
    if not 0.87 <= len(pcm) / len(best) <= 0.89:
        raise ValueError("英文片段的实测速度偏离 0.88 倍")

    # 只在英文区间内做短过渡，保留接点两侧的原始中文和停顿。
    edited = best.copy()
    edge = min(round(sample_rate * 0.009), len(pcm) // 2, len(edited) // 2)
    if edge >= 2:
        ramp = np.linspace(0, 1, edge)
        edited[:edge] = np.rint(pcm[:edge] * (1 - ramp) + edited[:edge] * ramp)
        edited[-edge:] = np.rint(edited[-edge:] * (1 - ramp) + pcm[-edge:] * ramp)
    return edited


def edit_phrases(pcm, timings, sample_rate):
    sections = []
    chunks = []
    source_cursor = output_cursor = 0
    for item in timings:
        begin = round(item["startSeconds"] * sample_rate)
        end = round(item["endSeconds"] * sample_rate)
        if not source_cursor <= begin < end <= len(pcm):
            raise ValueError(f"目标词边界无效：{item['text']}")
        edited = stretch_phrase(pcm[begin:end], sample_rate)
        chunks.extend((pcm[source_cursor:begin], edited))
        new_begin = output_cursor + begin - source_cursor
        new_end = new_begin + len(edited)
        sections.append((begin, end, new_begin, new_end))
        source_cursor, output_cursor = end, new_end
    chunks.append(pcm[source_cursor:])
    return np.concatenate(chunks), sections


def remap_events(events, sections, sample_rate, trim_seconds):
    def remap_offset(ticks):
        sample = (ticks / TICKS_PER_SECOND - trim_seconds) * sample_rate
        shift = 0
        for begin, end, new_begin, new_end in sections:
            if sample < begin:
                break
            if sample <= end:
                corrected = new_begin + (sample - begin) * (new_end - new_begin) / (end - begin)
                return round((corrected / sample_rate + trim_seconds) * TICKS_PER_SECOND)
            shift = new_end - end
        return round(((sample + shift) / sample_rate + trim_seconds) * TICKS_PER_SECOND)

    remapped = []
    for event in events:
        start = remap_offset(event["offset"])
        end = remap_offset(event["offset"] + event["duration"])
        remapped.append({**event, "offset": start, "duration": end - start})
    return remapped


async def synthesize(text, audio, boundaries):
    for attempt in range(3):
        try:
            await edge_tts.Communicate(
                text, VOICE, rate=RATE, boundary="WordBoundary", connect_timeout=15, receive_timeout=30,
            ).save(str(audio), str(boundaries))
            return
        except (aiohttp.ClientError, edge_tts.exceptions.NoAudioReceived, TimeoutError):
            if attempt == 2:
                raise
            await asyncio.sleep(2 ** attempt)
