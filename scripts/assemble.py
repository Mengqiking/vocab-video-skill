import json
import math
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PIL import Image, ImageDraw

FPS = 30


def waveform(path, peaks, count):
    with path.open("wb") as output:
        for frame in range(count):
            image = Image.new("RGB", (244, 38), "#202220")
            draw = ImageDraw.Draw(image)
            draw.line((0, 19, 243, 19), fill="#58522b")
            for i in range(48):
                j = frame*4+i-24
                peak = peaks[j] if 0 <= j < len(peaks) else 0
                height = max(2, min(35, peak*67))
                x = 3+i*5
                draw.rounded_rectangle((x, 19-height/2, x+1, 19+height/2), radius=1, fill="#f9da4a")
            output.write(image.tobytes())


def assemble(item, staging):
    folder = Path(item["folder"])
    props = json.loads((folder/"render-props.json").read_text())
    entries = item["frames"]
    story = math.ceil(props["narrationSeconds"]*FPS)
    count = story+150
    base = staging / folder.name[:3]
    wave = base.with_suffix(".rgb")
    listing = base.with_suffix(".ffconcat")
    lines = ["ffconcat version 1.0"]
    for i, (frame, filename) in enumerate(entries):
        next_frame = entries[i+1][0] if i+1 < len(entries) else count
        relative = Path(filename).relative_to(staging).as_posix()
        lines.extend((f"file '{relative}'", "option framerate 30", f"duration {(next_frame-frame)/FPS:.9f}"))
    last_frame = Path(entries[-1][1]).relative_to(staging).as_posix()
    lines.extend((f"file '{last_frame}'", "option framerate 30"))
    listing.write_text("\n".join(lines)+"\n")
    waveform(wave, props["peaks"], story)
    target = folder / f"{props['lesson']['title']}.mp4"
    stage = folder / ".rendering.mp4"
    try:
        subprocess.run([
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-threads", "1",
            "-f", "concat", "-safe", "0", "-i", str(listing),
            "-f", "rawvideo", "-pixel_format", "rgb24", "-video_size", "244x38", "-framerate", "30", "-i", str(wave),
            "-i", str(folder/"narration.wav"), "-filter_complex_threads", "1",
            "-filter_complex", "[0:v]fps=30,format=yuv420p[base];[base][1:v]overlay=782:1012:eof_action=pass[v]",
            "-map", "[v]", "-map", "2:a:0", "-c:v", "libx264", "-threads", "2", "-preset", "veryfast", "-crf", "22",
            "-c:a", "aac", "-b:a", "192k", "-af", "apad", "-t", f"{count/FPS:.9f}",
            "-movflags", "+faststart", str(stage),
        ], check=True)
        os.replace(stage, target)
    finally:
        wave.unlink(missing_ok=True)
        listing.unlink(missing_ok=True)
        stage.unlink(missing_ok=True)
    print(f"完成 {folder.name}", flush=True)


def main():
    path = Path(sys.argv[1])
    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(lambda item: assemble(item, path.parent), json.loads(path.read_text())))


if __name__ == "__main__":
    main()
