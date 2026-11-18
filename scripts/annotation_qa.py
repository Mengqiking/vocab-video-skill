"""从最终 MP4 检查每个朗读区间的高亮末行，以及正文每帧的释义。"""
import subprocess
import sys
import numpy as np

WIDTH, TOP, BOTTOM = 1080, 165, 949
HEIGHT = BOTTOM - TOP


def red_mask(rgb):
    r, g, b = rgb.astype(np.int16).transpose(2, 0, 1)
    return (r > 102) & (r < 207) & (r - g > 56) & (r - b > 54) & (g < 138) & (np.abs(g - b) < 32)


def groups(indices, gap=1):
    if not indices.size:
        return []
    return np.split(indices, np.flatnonzero(np.diff(indices) > gap) + 1)


def label_slot(active_rgb, reference, word):
    r, g, b = active_rgb.transpose(2, 0, 1)
    orange = (r > 216) & (r < 248) & (g > 168) & (g < 200) & (b < 75)
    rows = [y for y in groups(np.flatnonzero(orange.sum(axis=1) > 8)) if len(y) > 15]
    if not rows:
        raise ValueError(f"{word} 首次朗读帧缺少深黄高亮")
    last = rows[-1]
    columns = np.flatnonzero(orange[last].sum(axis=0) > 4)
    xs = max(groups(columns, gap=4), key=len)
    center = (int(xs[0]) + int(xs[-1])) / 2
    top = int(last[-1]) + 1
    band = reference[top:top + 38]
    ink = groups(np.flatnonzero(band.any(axis=0)), gap=14)
    for cols in ink:
        error = abs((int(cols[0]) + int(cols[-1])) / 2 - center)
        if error > 2:
            continue
        left, right = max(0, int(cols[0]) - 2), min(WIDTH, int(cols[-1]) + 3)
        ys = np.flatnonzero(band[:, left:right].any(axis=1))
        gap = int(ys[0])
        if not 3 <= gap <= 6:
            raise ValueError(f"{word} 释义间距为 {gap} 像素")
        return np.s_[top:top + 38, left:right], {
            "word": word, "highlightLines": len(rows), "lastLineWidthPx": int(xs[-1] - xs[0] + 1),
            "centerErrorPx": error, "gapPx": gap,
        }
    raise ValueError(f"{word} 高亮末行中心 {center} 附近没有居中的释义")


def decode(video, count, select=None):
    filters = [f"crop={WIDTH}:{HEIGHT}:0:{TOP}"]
    if select:
        filters.insert(0, "select=" + "+".join(f"eq(n\\,{n})" for n in select))
    cmd = ["ffmpeg", "-nostdin", "-v", "error", "-xerror", "-i", str(video), "-an",
           "-vf", ",".join(filters), "-fps_mode", "passthrough", "-frames:v", str(count),
           "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    with subprocess.Popen(cmd, stdout=subprocess.PIPE) as proc:
        try:
            for _ in range(count):
                data = proc.stdout.read(WIDTH * HEIGHT * 3)
                if len(data) != WIDTH * HEIGHT * 3:
                    raise ValueError("逐帧解码提前结束")
                yield np.frombuffer(data, dtype=np.uint8).reshape(HEIGHT, WIDTH, 3)
        finally:
            proc.stdout.close()
            if proc.wait() != 0 and not sys.exc_info()[0]:
                raise ValueError("逐帧解码失败")


def verify_annotations(video, timings, story):
    reveals = [t["revealFrame"] for t in timings]
    indices = sorted(set([*reveals, story - 1]))
    grabs = dict(zip(indices, list(decode(video, len(indices), indices)), strict=True))
    reference = red_mask(grabs[story - 1])
    slots = []
    measurements = []
    for t in timings:
        region, result = label_slot(grabs[t["revealFrame"]], reference, t["text"])
        slots.append(region)
        measurements.append(result)
    for n, rgb in enumerate(decode(video, story)):
        observed = red_mask(rgb)
        for t, region in zip(timings, slots, strict=True):
            actual = observed[region]
            target = reference[region]
            if n >= t["revealFrame"]:
                valid = (actual & target).sum() >= 0.88 * (actual | target).sum()
            else:
                valid = actual.sum() <= 10
            if not valid:
                raise ValueError(f"第 {n} 帧 {t['text']} 释义错位、提前或丢失")
            observed[region] = False
        if observed.sum() > 20:
            raise ValueError(f"第 {n} 帧出现区域外红字")
    return {"checkedFrames": story, "words": measurements}
