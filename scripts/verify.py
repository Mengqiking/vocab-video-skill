import json
import math
import subprocess
import wave

import numpy as np
from PIL import Image
from audio_base import align
import hashlib


def require(condition, message):
    if not condition:
        raise ValueError(message)


def run(args):
    result = subprocess.run(args, capture_output=True)
    if result.returncode:
        raise ValueError(result.stderr.decode(errors="replace").strip())
    return result.stdout


def read_json(path):
    return json.loads(path.read_text())


def frames(video, indexes):
    indexes = sorted(set(indexes))
    expression = "+".join(f"eq(n\\,{frame})" for frame in indexes)
    data = run([
        "ffmpeg", "-v", "error", "-xerror", "-threads", "1", "-i", str(video),
        "-vf", f"select={expression},scale=270:270,format=rgb24",
        "-fps_mode", "passthrough", "-an", "-threads", "1", "-f", "rawvideo", "-",
    ])
    count = 270*270*3
    require(len(data) == count*len(indexes), "抽取的帧数不符")
    return dict(zip(indexes,np.frombuffer(data,dtype=np.uint8).reshape(-1,270,270,3)))


def red_mask(frame):
    body = frame[24:234, 9:261].astype(np.int16)
    r,g,b=body.transpose(2,0,1)
    return (r>g+32) & (r>b+23) & (g<140) & (r<220)


def active_count(frame):
    body = frame[24:234, 9:261]
    r,g,b=body.transpose(2,0,1)
    return int(np.count_nonzero((r>213) & (g>145) & (g<198) & (b<83)))


def body_rows(frame):
    ink=np.max(frame[26:231,9:261], axis=2)<113
    rows=np.where(np.count_nonzero(ink,axis=1)>6)[0]
    groups=np.split(rows, np.where(np.diff(rows)>7)[0]+1)
    xs=np.where(np.any(ink[groups[-1][0]:groups[-1][-1]+1],axis=0))[0]
    return len(groups), int(xs[-1]-xs[0]+1)


def count_recap_rows(frame):
    ink = np.max(frame[:, 17:104], axis=2) < 105
    # Each row occupies one 79px card, even when the English text wraps.
    return sum(int(np.count_nonzero(ink[(202+i*85)//4:(277+i*85)//4]) >= 20) for i in range(8))


def count_quiz_blanks(video, start_frame):
    data = run(["ffmpeg", "-v", "error", "-xerror", "-ss", f"{(start_frame+2)/30:.6f}",
                "-i", str(video), "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"])
    frame = np.frombuffer(data, dtype=np.uint8).reshape(1080, 1080, 3)
    mask = np.max(frame[185:934, 45:1038], axis=2) < 160
    lines = []
    for y, row in enumerate(mask):
        xs = np.flatnonzero(row)
        for run_ in np.split(xs, np.flatnonzero(np.diff(xs) > 1) + 1):
            if not 52 <= len(run_) <= 62:
                continue
            x = int(run_[0])
            if not any(abs(y - old_y) <= 5 and abs(x - old_x) <= 5 for old_x, old_y in lines):
                lines.append((x, y))
    return len(lines)


def verify(folder, source_pcm, sections):
    lesson = read_json(folder/"lesson.json")
    props = read_json(folder/"render-props.json")
    timeline = read_json(folder/"timeline.json")
    title = lesson["title"]
    words=lesson["words"]
    terms=[w["text"] for w in words]
    ids=[w["id"] for w in words]
    timings=timeline["timings"]
    require(len(terms)==len(set(terms))==8 and all(w["meaning"] and w["pos"] and w["ipa"] for w in words), "词条缺失、重复或释义不全")
    require([t["text"] for t in timings]==terms and [t["id"] for t in timings]==ids, "时间轴词表不符")
    require([part["word"] for part in lesson["parts"] if isinstance(part,dict)]==ids, "正文词表不符")
    require(props["lesson"]==lesson and props["timings"]==timings, "渲染参数是旧版本")
    narration=timeline["narrationSeconds"]
    require(timeline["fps"] == 30, "帧率不符")
    previous=0
    reveals=[]
    for item in timings:
        start,end=item["startSeconds"],item["endSeconds"]
        frame=item["revealFrame"]
        require(previous<=start<end<=narration and frame==math.ceil(start*30-1e-8), f"{item['text']} 时间轴错误")
        reveals.append(frame)
        previous=end
    story = math.ceil(narration*30)
    video = folder / f"{title}.mp4"
    require(video.stat().st_mtime_ns>= (folder/"render-props.json").stat().st_mtime_ns,"MP4 比渲染参数旧")
    with Image.open(folder/"封面.jpg") as cover:
        require(cover.size==(1080,1440) and cover.format=="JPEG", "封面格式不符")
        cover.load()
    require((folder/"封面.jpg").stat().st_mtime_ns >= (folder/"render-props.json").stat().st_mtime_ns, "封面比渲染参数旧")
    events = [json.loads(line) for line in (folder/"boundaries.jsonl").read_text().splitlines()]
    require(all(x["type"] == "WordBoundary" for x in events), "非 WordBoundary 元数据")
    trim = timeline["leadingTrimSeconds"]
    require(timings == align(words, events, narration, trim), "原始短语词边界与时间轴不符")
    with wave.open(str(folder/"narration.wav")) as audio:
        require(abs(audio.getnframes()/audio.getframerate()-narration)<1e-6, "旁白时长不符")
        samples=np.frombuffer(audio.readframes(audio.getnframes()),dtype=np.int16)
    old_end = new_end = 0
    require(len(sections) == len(timings), "英文区间数与词表不符")
    for (begin, end, new_begin, next_end), timing in zip(sections, timings, strict=True):
        require(abs(round(timing["startSeconds"] * 48000) - new_begin) <= 1 and
                abs(round(timing["endSeconds"] * 48000) - next_end) <= 1, "变速区间与最终时间轴不符")
        require(np.array_equal(source_pcm[old_end:begin], samples[new_end:new_begin]), "区间外采样被改动")
        require(0.87 <= (end - begin) / (next_end - new_begin) <= 0.89, "实测英文语速偏离目标")
        old_end, new_end = end, next_end
    require(np.array_equal(source_pcm[old_end:], samples[new_end:]), "末段采样被改动")
    for item in timings:
        begin, end = (round(item[key]*48000) for key in ("startSeconds", "endSeconds"))
        word_audio = samples[begin:end].astype(np.float32)
        require(len(word_audio) >= 480 and np.sqrt(np.mean(word_audio**2)) >= 60,
                f"{item['text']} 对应区间静音或过短")
    padded = np.pad(samples.astype(np.int32), (0, -len(samples) % 400))
    peaks = np.round(np.max(np.abs(padded.reshape(-1, 400)), axis=1) / 32768, 4)
    require(np.array_equal(peaks, props["peaks"]), "波形不是当前旁白数据")
    playback=np.frombuffer(run([
        "ffmpeg","-v","error","-xerror","-i",str(video),"-map","0:a:0","-ac","1",
        "-ar","48000","-f","s16le","-",
    ]),dtype=np.int16)
    require(playback.size>samples.size+int(48000*4.9), "音轨未保留复习卡时长")
    signal=playback[:samples.size:8].astype(np.float32)
    original=samples[::8].astype(np.float32)
    numerator=np.dot(signal,original)
    denominator=np.sqrt(np.dot(signal,signal)*np.dot(original,original))
    require(numerator/denominator>.95, "成片旁白与生成的 WAV 不同步")
    require(np.max(np.abs(playback[(story+3)*1600:].astype(np.int32)))<130, "复习卡片尾仍有旁白")
    probe=json.loads(run(["ffprobe","-v","error","-show_entries","stream=codec_name,codec_type,width,height,r_frame_rate,nb_frames:format=duration","-of","json",str(video)]))
    v,a=probe["streams"]
    require((v["codec_name"],v["width"],v["height"],v["r_frame_rate"],a["codec_name"])==("h264",1080,1080,"30/1","aac"), "音视频规格错误")
    require(int(v["nb_frames"])==story+150 and abs(float(probe["format"]["duration"])-(story+150)/30)<.002, "片尾时长不符")
    grabs=frames(video,[0,story-1,story,story+74,story+75,story+149,*reveals,*(r-1 for r in reveals)])
    rows,tail=body_rows(grabs[0])
    require(6<=rows<=9 and tail>=24, f"正文排版 {rows} 行，尾行 {tail} 像素")
    require(np.count_nonzero(red_mask(grabs[0]))<8,"首帧红字已提前显示")
    for item,at in zip(timings,reveals):
        before,after=grabs[at-1],grabs[at]
        old,new=red_mask(before),red_mask(after)
        added=int(np.count_nonzero(new & ~old))
        dropped=int(np.count_nonzero(old & ~new))
        require(added>=10 and dropped<=max(16,added*.45),f"{item['text']} 红字变化异常，新增 {added}，减少 {dropped}")
        require(active_count(after)>=40, f"{item['text']} 当帧未深黄高亮")
        # Compare the region that shows the active word and its translation.
        diff=np.abs(after[238:267,9:192].astype(np.int16)-before[238:267,9:192])
        require(np.mean(diff)>0.7,f"{item['text']} 底栏没有同步切换")
    require(np.count_nonzero(red_mask(grabs[story-1])) >= 95,"正文结束前没有保留释义")
    crop=(slice(24,233),slice(10,261))
    require(np.mean(np.abs(grabs[story][crop].astype(np.int16)-grabs[story+75][crop])) > 5,"汇总和完形没有切换")
    recap_rows, blanks = count_recap_rows(grabs[story]), count_quiz_blanks(video, story+75)
    require(recap_rows == 8, f"单列汇总可见 {recap_rows} 行")
    require(blanks == 8, f"完形卡可见 {blanks} 条挖空线")
    for a,b in ((story,story+74),(story+75,story+149)):
        require(np.mean(np.abs(grabs[a][crop].astype(np.int16)-grabs[b][crop])) < 1, "复习卡没有完整停留")
    run(["ffmpeg", "-nostdin", "-v", "error", "-xerror", "-i", str(video), "-f", "null", "-"])
    return {
        "title": title, "rows": rows, "durationSeconds": (story + 150) / 30,
        "verifiedWords": len(timings), "recapRows": recap_rows, "clozeBlanks": blanks,
        "englishTempoByPhrase": [(end-begin)/(new_end-new_begin) for begin,end,new_begin,new_end in sections],
        "unchangedOutsideEnglish": True,
        "files": {name: hashlib.sha256((folder / name).read_bytes()).hexdigest()
                  for name in (video.name, "封面.jpg", "lesson.json", "timeline.json",
                               "render-props.json", "narration.wav", "boundaries.jsonl", "原始词边界.jsonl")},
    }
