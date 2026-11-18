# 情境词汇视频制作

用一份简体词表和情境稿生成带旁白、高亮、释义和两张复习卡的方形 MP4。仅对八个英文目标词或短语保音高降速，按实际音轨重算逐词时间轴。可用作 Codex Skill，也可在终端单独运行。 [Skill 入口](SKILL.md)

## 安装

需要 Python 3.12、Node.js 22、带 `libx264` 和 `atempo` 的 FFmpeg、FFprobe、可用的 Chrome 或 Chromium，以及能显示简体中文和 IPA 的字体。Linux 推荐安装 `fonts-noto-cjk`，在非默认位置安装浏览器时通过 `CHROME_BIN` 指定其可执行文件。依赖安装需联网。

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
npm ci
```

## 一条命令生成原创示例

```sh
python scripts/build.py examples/library.json --output out/library
```

示例《图书馆的自习座位怎么订？》是虚构情境，适用于词汇练习。命令用 Edge TTS 合成台湾华语 HsiaoChen，语速为 `+8%`。按 WordBoundary 将八个完整英文目标项降速至约 `0.88×`。Remotion 生成画面，FFmpeg 合成 1080×1080、30 fps 的 MP4。片尾有单列词汇汇总和八空填空，分别停留 2.5 秒，各次运行时长随源音轨变化。

输出新目录中有 MP4、封面、原声、最终 WAV、原始和校准后的词边界、词表、渲染参数及验收记录。只有完成验收才交付输出目录。为保护已有成片，每次指定新目录。

`lesson.json` 以 [原创示例](examples/library.json) 为格式参考。将八个目标项各写一次，由 `parts` 内的 `{"word":"w1"}` 等引用，并与词表顺序一致。标题和画面文字使用简体，英文保留原文。用 `--tts-text` 单独传入同文意、同英文顺序的 UTF-8 口播稿。

## 导入本地音轨

```sh
python scripts/build.py examples/library.json \
  --audio input/original.wav --boundaries input/words.jsonl --output out/local
```

提供配套词边界 JSONL，每行包含 `type`、`text`、`offset` 和 `duration`，后两者以 100 纳秒为单位，`type` 取 `WordBoundary`。时间以原声开头为零点。支持将多词短语分成多个事件。仅需本地音轨时使用此模式，首次准备仍需安装渲染依赖和浏览器。

## 离线回归

```sh
python -m unittest discover -s tests -v
VOCAB_RENDER_TEST=1 python -m unittest discover -s tests -v
```

测试现场生成短音频和词边界，不依赖在线配音。第一条命令覆盖局部变速、区间外采样一致性、时间轴移动和错配拦截。第二条额外跑完整的本地渲染和验收。验收报告记录文件哈希和实测速率。发音和手机阅读体验以人工抽听、抽看为准。

本仓库自有代码按 MIT 授权。安装的 Remotion、Edge TTS 等依赖适用各自条款。在线合成会把口播稿提交给 Edge TTS 服务。 [Remotion 授权](https://www.remotion.dev/docs/license/pricing) · [Edge TTS 上游](https://github.com/rany2/edge-tts)

[画面尺寸、字体规格、跨行示例的验收要求与错位复盘](references/画面与逐帧验收.md)记录了模板的视觉标准。开启完整离线测试会同时渲染普通示例和跨行示例，并逐帧验证成片。
