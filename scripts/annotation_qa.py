"""从最终 MP4 检查每个朗读区间的高亮末行，以及指定正文帧的释义。"""
import math
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


def contains_stray_text(mask):
    # 压缩色点可散布整行。用 24 像素窗口查找能构成字形的局部红点。
    if mask.sum() <= 20:
        return False
    ys, xs = np.nonzero(mask)
    for dy, dx in ((0, 0), (12, 0), (0, 12), (12, 12)):
        bins = ((ys + dy) // 24) * 48 + (xs + dx) // 24
        if np.bincount(bins).max() > 20:
            return True
    return False


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


def sampled_indices(timings, story):
    points = {0, story - 1}
    for item in timings:
        start = item["revealFrame"]
        stop = math.ceil(item["endSeconds"] * 30 - 1e-8)
        points.update((start - 1, start, (start + stop) // 2, stop - 1, stop))
    return sorted(n for n in points if 0 <= n < story)


def verify_annotations(video, timings, story, mode="sampled"):
    if mode == "sampled":
        planned = sampled_indices(timings, story)
        select = planned
    elif mode == "full":
        planned = range(story)
        select = None
    else:
        raise ValueError(f"未知释义验收模式 {mode}")

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
    for n, rgb in zip(planned, decode(video, len(planned), select), strict=True):
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
        if contains_stray_text(observed):
            raise ValueError(f"第 {n} 帧出现区域外红字")
    report = {"mode": mode, "checkedFrames": len(planned), "storyFrames": story, "words": measurements}
    if mode == "sampled":
        report["frameIndices"] = planned
    return report
