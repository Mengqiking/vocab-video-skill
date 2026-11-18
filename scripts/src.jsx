import React from "react";
import {AbsoluteFill, Composition, registerRoot, useCurrentFrame} from "remotion";
import "./video.css";

const fps = 30;
const cardFrames = 75;

function Passage({lesson, revealed, speakingId, storySize, quiz = false}) {
  const wordMap = new Map(lesson.words.map(word => [word.id, word]));
  let punctuation = "";
  return (
    <div className={quiz ? "passage quiz" : "passage"} lang="zh-CN"
      style={quiz ? undefined : {fontSize: storySize}}>
      {lesson.parts.map((part, index) => {
        if (typeof part === "string") {
          const text = part.slice(punctuation.length);
          punctuation = "";
          return text;
        }
        const word = wordMap.get(part.word);
        punctuation = lesson.parts[index + 1]?.match(/^[，。！？；、]/)?.[0] ?? "";
        if (!quiz) {
          const split = word.text.lastIndexOf(" ");
          const head = split < 0 ? "" : word.text.slice(0, split + 1);
          const tail = word.text.slice(split + 1);
          return (
            <span className="flow-phrase" key={word.id} data-word={word.id}>
              <mark lang="en-US" className={word.id === speakingId ? "speaking" : undefined}>
                {head}<span className="phrase-tail">{tail}
                  <span className="annotation" style={{visibility: revealed.has(word.id) ? "visible" : "hidden"}}>
                    {word.meaning}
                  </span>
                </span>
              </mark>{punctuation}
            </span>
          );
        }
        return (
          <span className="word-group" key={word.id} data-word={word.id}>
            <span className="blank" /> <span className="hint">（{word.pos} {word.meaning}）</span>
            {punctuation}
          </span>
        );
      })}
    </div>
  );
}

function Waveform({peaks, frame}) {
  const center = frame * 4;
  return (
    <svg className="waveform" width="244" height="38" viewBox="0 0 244 38" aria-hidden="true">
      <path d="M 0 19 H 244" stroke="#f9da4a" strokeOpacity=".26" />
      {Array.from({length: 48}, (_, i) => {
        const peak = peaks[center + i - 24] ?? 0;
        const height = Math.max(2, Math.min(35, peak * 67));
        return <rect key={i} x={3 + i * 5} y={19 - height / 2} width="2.2" height={height} rx="1.1" fill="#f9da4a" />;
      })}
    </svg>
  );
}

function Review({lesson, quiz}) {
  return (
    <>
      <h1>{quiz ? "完形填空" : "单词汇总"}</h1>
      {quiz ? <Passage lesson={lesson} quiz /> : (
        <ol className="recap">
          {lesson.words.map(word => (
            <li key={word.id}>
              <strong lang="en-US">{word.text}</strong>
              <span className="ipa">/{word.ipa}/</span>
              <span className="definition">{word.pos} {word.meaning}</span>
            </li>
          ))}
        </ol>
      )}
    </>
  );
}

function Video({lesson, narrationSeconds, timings, peaks, visualFrame, separateWave = false}) {
  const currentFrame = useCurrentFrame();
  const frame = visualFrame ?? currentFrame;
  const storyFrames = Math.ceil(narrationSeconds * fps);
  const reading = frame < storyFrames;
  const heard = timings.filter(item => item.revealFrame <= frame);
  const revealed = new Set(heard.map(item => item.id));
  const lastHeard = heard.at(-1);
  const active = lesson.words.find(word => word.id === lastHeard?.id);
  const speakingEnd = lastHeard && Math.ceil(lastHeard.endSeconds * fps - 1e-8);
  const speakingId = frame < speakingEnd ? lastHeard.id : undefined;
  const quiz = frame >= storyFrames + cardFrames;

  return (
    <AbsoluteFill className="video">
      <header>
        <span>情境记单词</span>
        <strong className="scene-title">{lesson.title}</strong>
        <span className="exam-label">四级/六级/雅思</span>
      </header>
      <main className={reading ? "story" : "review"}>
        {reading ? <Passage lesson={lesson} revealed={revealed} speakingId={speakingId} storySize={lesson.storySize ?? 37} /> : <Review lesson={lesson} quiz={quiz} />}
      </main>
      <footer>
        {!reading && lesson.sourceLabel && <div className="exam-source">{lesson.sourceLabel}</div>}
        {reading && (
          <>
            <div className="current-word" data-active={active?.id}>
              {active && (
                <>
                  <strong lang="en-US">{active.text}</strong>
                  <div className="word-meta"><span>/{active.ipa}/</span><span>{active.pos} {active.meaning}</span></div>
                </>
              )}
            </div>
            <div className="listening">
              <span className="progress">已解锁 {heard.length} / {lesson.words.length}</span>
              {separateWave ? <div style={{width: 244, height: 38}} /> : <Waveform peaks={peaks} frame={frame} />}
            </div>
          </>
        )}
      </footer>
    </AbsoluteFill>
  );
}

function PackedFrames({scenes, frames}) {
  const frame = useCurrentFrame();
  const target = frames[frame];
  return <Video {...scenes[target.scene]} visualFrame={target.frame} separateWave />;
}

registerRoot(() => (
  <Composition
    id="PackedFrames"
    component={PackedFrames}
    width={1080}
    height={1080}
    fps={fps}
    durationInFrames={1}
    defaultProps={{scenes: [], frames: []}}
    calculateMetadata={({props}) => ({durationInFrames: Math.max(1, props.frames.length)})}
  />
));
