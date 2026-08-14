# AI Director

[日本語版 README はこちら](README.ja.md)

**AI Director** is a local-first AI that *understands* your footage — video,
audio, photos, across cameras and color spaces — plans a story from your
intent, and produces a high-quality, human-finishable edit plan. It is not a
"score clips and auto-cut" tool: an LLM director decides what to show, why,
in what order, and for how long, and can explain every decision.

See [AGENT.md](AGENT.md) for the full architecture and design principles.

## Pipeline

```
Footage → Media Ingest → Color Management → Perception → Media Memory
        → AI Director → Edit Plan → Timeline Compiler
        → Preview MP4 / FCPXML / OTIO / EDL → DaVinci Resolve / FCP
```

- **Color-managed analysis**: Log footage (DJI D-Log2/D-Log/D-Log M, HLG, …)
  is normalized to a neutral Rec.709 *analysis representation* before any
  AI sees it. Original camera files are never modified; NLE exports
  reference the originals.
- **Media Memory**: every observation (metadata, segments, transcripts,
  VLM analysis, embeddings, technical features) is persisted in SQLite and
  searched — the director reasons over memory, not raw pixels.
- **Replaceable models**: vision / director / speech / embedding providers
  are configured in `config/models.yaml` (OpenAI-compatible servers,
  faster-whisper, transformers). Business logic never touches model
  libraries. Single-GPU phase execution is the default runtime strategy.

Reference model set (RTX 5060 Ti 16GB class, all local):

| Role | Model |
|---|---|
| Vision | Qwen3-VL-4B-Instruct (transformers, bf16) |
| Director | Qwen3-8B Q4_K_M (llama.cpp server, OpenAI-compatible) |
| Embedding | Qwen3-VL-Embedding-2B (sentence-transformers) |
| Speech | faster-whisper large-v3-turbo |

## Setup

Requires Python 3.11+, `uv`, and `ffmpeg`/`ffprobe` on PATH.

```bash
uv sync                      # core
uv sync --extra speech       # + faster-whisper (local ASR)
uv sync --extra vision       # + transformers/torch (local VLM)
uv sync --extra embedding    # + sentence-transformers (retrieval)
uv sync --extra web          # + review/edit web UI
```

Configure model endpoints in `config/models.yaml`. Start the Director LLM,
e.g.:

```bash
llama-server -hf Qwen/Qwen3-8B-GGUF:Q4_K_M --port 8102 -ngl 99 \
  -c 16384 -fa on --jinja --reasoning-budget 0
```

Place vendor LUTs under `assets/luts/` (see `assets/luts/README.md`).

## Usage

```bash
aidirector ingest ./footage                      # scan + metadata + color detection
aidirector ingest ./footage --color-profile dji-dlog2   # manual override

aidirector analyze ./footage                     # segments, ASR, VLM, embeddings

aidirector edit ./footage \
  --duration 90 \
  --profile travel_vlog \
  --prompt "雨の町を静かに歩く旅行Vlog" \
  --captions beats \
  --caption-format "{HH}:{MM} {PLACE}"           # → edit-plan.json + preview.mp4

aidirector search ./footage "夕焼け"              # semantic search over media memory

aidirector preview latest --canvas landscape     # re-render preview
aidirector export latest --format fcpxml         # NLE export (original media refs)
```

State lives in `./.aidirector/` (SQLite media memory, proxies, frames,
renders, plans).

### Scene captions

`--captions beats|clips` overlays a centered time/place caption after scene
changes. The place comes from the Director (only when clearly identifiable),
the time from recording metadata — no facts, no caption. Layout is
templatable via `--caption-format`, e.g. `"{HH}:{MM} {PLACE}"`
(tokens: `{PLACE} {DATE} {TIME} {YYYY} {MO} {DD} {HH} {MM}`, `\n` starts a
smaller second line). Captions live in the edit plan (editable JSON) and are
carried into NLE exports as FCPXML titles / OTIO markers / EDL comments.

### Review / edit UI

```bash
aidirector web            # → http://127.0.0.1:8484/
aidirector app            # desktop mode: free port + opens your browser
```

Create edits from the browser (footage path + prompt + settings, with live
phase/log progress), then reorder, trim (graphical filmstrip with draggable
in/out handles), remove clips, edit captions, add segments from Media
Memory, save as a new plan version (user actions are recorded as feedback),
and re-render the preview.

### Managed Director LLM (no manual server)

Set `provider: llama-server` in `config/models.yaml` and AI Director
starts/stops `llama-server` itself around the director phase — it never
holds VRAM during vision analysis, and an already-running server on the
port is reused instead. Works on Windows and Linux (caption fonts and the
CUDA runtime shims are resolved per-platform).

## Docker

```bash
docker compose up -d aidirector          # web UI → http://localhost:8484/
docker compose --profile llm up -d       # + Director LLM (llama.cpp server)

# CLI (footage is mounted read-only at /footage):
docker compose run --rm aidirector aidirector analyze /footage
docker compose run --rm aidirector aidirector edit /footage \
  --duration 60 --prompt "落ち着いた旅行Vlog"
```

- Host paths: set `AIDIRECTOR_FOOTAGE` (default `./footage`) and
  `AIDIRECTOR_DATA` (default `./data`, holds the media memory and renders).
- GPU: install the [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html),
  uncomment the `gpus: all` lines in `docker-compose.yaml`, and switch
  `director-llm` to the `:server-cuda` image. Without a GPU everything
  falls back to CPU (slow but functional).
- On a single 16GB GPU run `analyze` first, then start the `llm` profile —
  phase execution keeps models from fighting over VRAM (AGENT.md §38).
- Image size: the default build bundles torch/transformers (several GB).
  Trim with `--build-arg EXTRAS="--extra web --extra speech"`.

## Development

```bash
uv run pytest             # unit + golden + integration tests (ffmpeg required)
```

Layout follows AGENT.md §6: `src/aidirector/{media,color,perception,ai,memory,director,tools,timeline,web}`.
