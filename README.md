<p align="center">
  <img src="assets/icon/icon.png" width="128" alt="AI Director icon">
</p>

# AI Director

English | [日本語](README.ja.md) | [Developer docs](docs/DEVELOPMENT.md)

**Hand it your footage and tell it what kind of video you want — a fully
local AI that understands your material and drafts the edit for you.**

- Analyzes your video and audio locally and remembers what happens where
- From a prompt like *"a calm 60-second travel vlog of walking through a
  rainy town"*, an LLM director decides the story, the cuts, their order
  and their length
- **Every decision comes with a written reason**
- Review the result as a preview video and a timeline, and adjust
  everything in your browser
- Hand off to DaVinci Resolve or Final Cut Pro for finishing — your
  original camera files are referenced untouched
- **Everything runs on your own PC.** No footage ever leaves your machine

## Requirements

| | |
|---|---|
| OS | Windows 10/11 or Linux |
| GPU | NVIDIA GPU (16 GB VRAM recommended, e.g. RTX 5060 Ti). Works CPU-only, but slowly |
| Disk | ~35 GB free for AI models |
| ffmpeg | Required for all media processing (see below) |

## Install

### Windows

1. Download and run `AIDirector-Setup.exe` from [Releases](../../releases)
   (no admin rights needed)
2. Install ffmpeg: `winget install Gyan.FFmpeg`
3. Launch **AI Director** from the Start menu — **the first run downloads
   several GB of AI models**, so give it time

> The installer is not code-signed yet, so SmartScreen may warn you
> (choose "More info" → "Run anyway"). If **Smart App Control** is on,
> Windows blocks all unsigned apps with no per-app override — it can only
> be turned off in Windows Security → App & browser control (turning it
> back on requires reinstalling Windows, so decide carefully).

### Linux

1. Download `AIDirector-x86_64.AppImage` from [Releases](../../releases)
2. Make it executable and run:

```bash
chmod +x AIDirector-*.AppImage
./AIDirector-*.AppImage
```

3. Install ffmpeg from your distribution
   (`sudo apt install ffmpeg` / `sudo zypper in ffmpeg`)

### Without an installer

Any Python 3.9+ will do:

```bash
git clone <this repo> && cd ai-director
python desktop/bootstrap.py
```

### About the Director LLM

Editing decisions are made by a local LLM (Qwen3-8B). By default it
**downloads and loads in-process exactly like the other AI models**
(~16 GB on first use, run in 4-bit) — no extra software needed.

For faster generation, install llama.cpp (`winget install ggml.llamacpp`
on Windows, [official releases](https://github.com/ggml-org/llama.cpp/releases)
on Linux) and set the `director` to `provider: llama-server` in
`config/models.yaml`: AI Director then starts and stops a server with the
quantized model (~5 GB) exactly when it is needed. To run your own
OpenAI-compatible server, use `provider: openai-compatible`.

## How to use

Launching opens an app window (closing the window quits the app).

### 1. Create a video

1. Click **“+ 新規作成” (New)**
2. Point **素材パス** (footage path) at the folder with your clips — the
   number of videos found is shown immediately
3. Write what you want in the **指示** (prompt) field. The optional
   **流れ** (flow) field pins the structure: write
   `departure → train → restaurant → winery 1 → winery 2 → walk → train`
   and the video follows exactly those chapters in that order, with the
   AI picking the footage for each (CLI: `--flow`)
4. Pick a target length and a style (travel vlog / cinematic / talk)
5. Optional:
   - **Captions** — time & place shown at scene changes (format
     configurable, e.g. `{HH}:{MM} {PLACE}`)
   - **Spoken-word subtitles** — transcribed speech burned in as subtitles
   - **BGM** — point **BGMフォルダ** at a folder of music files
     (`.mp3/.wav/.m4a`); the AI picks the track that fits the story and
     mixes it in with fades and **automatic ducking** (music dips while
     people talk). CLI: `--music-dir`. With the `music` extra installed
     the tracks themselves are analyzed once (tempo/key/energy, mood &
     genre tags via CLAP, lyrics via Whisper, and a description from an
     audio LLM) so the pick is based on how the music actually sounds,
     not just its file name. Pre-analyze a big library with
     `aidirector music-analyze <folder>` — results are cached per file
     and shared across projects.
6. Hit **作成開始 (Create)** and watch the phase-by-phase progress

Pressing **+ 新規作成 (New)** while a project is open pre-fills the form
with that project's previous settings (prompt, flow, duration, captions,
BGM folder, …), so re-creating with one tweak is quick.

The first pass over new footage takes a while (the AI actually watches and
listens to it); **analysis is remembered**, so re-creating with a different
prompt takes only minutes.

### 2. Adjust

The draft appears as a timeline; every cut shows **why the AI chose it**.

- **Reorder** with the ↑↓ buttons
- **Trim** by dragging the green window on each clip's filmstrip
  (edges = in/out, grab the middle to slide)
- **Remove** with ✕
- **Edit captions and subtitles** in place
- **Tune the BGM** on its card above the timeline — the card shows the
  track's analysis facts (BPM, key, energy, tags, vocal/instrumental)
  next to the AI's reason; adjust volume and ducking, or remove it
- **Swap the BGM** with 曲を変更… (or + BGMを追加… when there is none):
  a library panel lists every track in your music folder with its
  analysis facts and description — one click replaces the track, and
  unanalyzed folders can be analyzed right there in the background
- **Preview any source video** in place — the ▶ button on each cut (plays
  from that cut's position) or on a Media Memory thumbnail opens a player;
  the whole file is seekable
- **Add cuts** from the Media Memory panel — everything the AI understood
  about your footage, one click to append

**Save** stores a new version (the original draft is kept);
**プレビュー生成 (Render preview)** rebuilds the video.

### 3. Hand off to your editor

Exports always reference **your original camera files** (Log footage stays
Log — grade it yourself in your NLE):

```bash
aidirector export latest --format fcpxml   # Final Cut Pro / DaVinci Resolve
aidirector export latest --format otio     # OpenTimelineIO
aidirector export latest --format edl      # CMX3600 EDL
aidirector export latest --format srt      # subtitle file
```

Captions and subtitles carry over as editable titles (FCPXML) and SRT.
BGM carries over too, referencing your original music file with the
volume applied (FCPXML connected clip / OTIO audio track). Fades and
ducking are preview-only — recreate them in your NLE if you need them.
Note: if the track is shorter than the timeline it loops in the preview,
but NLEs can't loop a clip, so the exported track simply ends there.

## Where your data lives

| Data | Location |
|---|---|
| Analysis, drafts, previews | `.aidirector/` in the working folder |
| AI models | `~/.cache/huggingface`, `~/.cache/llama.cpp` |
| **Your camera files** | **never modified** |

## FAQ / Troubleshooting

**Out of GPU memory / analysis stalls** — on 16 GB GPUs the models run one
at a time by design; close other GPU apps (games, other LLMs) first.

**Colors look washed out during analysis** — Log footage (D-Log etc.) is
analyzed most accurately with the vendor's official LUT: download the
`.cube` from the manufacturer and drop it into `assets/luts/` (licensing
forbids bundling them). Without one, a neutral fallback is used.

**Wrong color profile detected** — override it explicitly:
`aidirector ingest ./footage --color-profile dji-dlog2`

**No app window opens** — without Chrome/Edge/Chromium it falls back to a
normal browser tab; force that with `aidirector app --no-window`.

**The drafting phase fails ("llama-server binary not found" / "cannot
reach the LLM server")** — the default setup needs no extra software;
these errors only appear when `director` in `config/models.yaml` was
switched to `llama-server` / `openai-compatible`, which need llama.cpp
installed or your server running. Analysis results are already saved, so
just re-create and it resumes at the drafting step.

**Clips are out of shooting order / the same video appears twice** —
profiles like travel_vlog guarantee recording-time order and one clip per
source video in code (cinematic keeps the AI's order for dramatic
freedom; talk allows multiple cuts from one video). Only footage without
recording-time metadata (creation_time) stays where the AI placed it,
since there is no fact to sort it by. Embedded **timecode** (the tmcd
track written by e.g. the DJI Osmo Pocket 3) is used too: when it agrees
with the file's clock it becomes the frame-accurate recording start time
(record-run timecodes are ignored automatically).

**The same videos keep getting picked / some videos never appear** — a
60-second edit only holds ~10-20 cuts, so a large library can never be
fully used in one draft. But each re-creation gives priority candidate
slots to the footage your saved plans have used least, so repeated
drafts explore the whole library. Want more footage in one video?
Raise the target duration.

**Draft quality is so-so** — be specific in the prompt (order, mood, what
must stay). More footage and intact recording-time metadata both help.

**Music analysis downloads a huge model** — the optional track-description
step uses Qwen2.5-Omni-7B (~22 GB download, ~9 GB VRAM in 4-bit). In
`config/models.yaml` you can switch `music_understanding` to the smaller
`Qwen/Qwen2.5-Omni-3B` or disable it with `provider: none` — tempo/key,
CLAP tags and lyrics detection keep working without it.

## License

MIT. Third-party software is listed in
[THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md).

Contributing? Start with [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) and
[AGENT.md](AGENT.md) (design principles).
