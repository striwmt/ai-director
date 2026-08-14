# AI Director — developer documentation

English | [日本語](DEVELOPMENT.ja.md)

The end-user guide is in the top-level [README](../README.md). This
document covers working on AI Director itself. **Read
[AGENT.md](../AGENT.md) first** — it is the project's constitution
(architecture, layer rules, provider boundaries, color principles).

## Pipeline

```
Footage → Media Ingest → Color Management → Perception → Media Memory
        → AI Director → Edit Plan → Timeline Compiler
        → Preview MP4 / FCPXML / OTIO / EDL / SRT → NLE
```

- **Color-managed analysis**: Log footage (DJI D-Log2/D-Log/D-Log M, HLG,
  …) is normalized to a neutral Rec.709 *analysis representation* before
  any AI sees it. Originals are never modified; NLE exports reference them.
- **Media Memory**: every observation (metadata, segments, transcripts,
  VLM analysis, embeddings, technical features, provenance) is persisted
  in SQLite and queried — the director reasons over memory, not pixels.
- **Replaceable models**: vision / director / speech / embedding sit
  behind provider interfaces configured in `config/models.yaml`. Business
  logic never imports model libraries. Phase execution keeps a single
  16 GB GPU sufficient.

Reference model set (verified on RTX 5060 Ti 16GB):

| Role | Model | Provider |
|---|---|---|
| Vision | Qwen3-VL-4B-Instruct | `transformers` (bf16) |
| Director | Qwen3-8B Q4_K_M | `llama-server` (managed) or `openai-compatible` |
| Embedding | Qwen3-VL-Embedding-2B | `sentence-transformers` |
| Speech | faster-whisper large-v3-turbo | `faster-whisper` |

## Setup

Python 3.11+, `uv`, and `ffmpeg`/`ffprobe` on PATH.

```bash
uv sync                      # core only
uv sync --extra speech       # + faster-whisper (local ASR)
uv sync --extra vision       # + transformers/torch (local VLM)
uv sync --extra embedding    # + sentence-transformers (retrieval)
uv sync --extra web          # + review/edit web UI
```

Model endpoints live in `config/models.yaml`; never hardcode model names
in code. An external OpenAI-compatible director server can be started with:

```bash
llama-server -hf Qwen/Qwen3-8B-GGUF:Q4_K_M --port 8102 -ngl 99 \
  -c 16384 -fa on --jinja --reasoning-budget 0
```

or use `provider: llama-server` and let the runtime manage the process.

## CLI reference

```bash
aidirector ingest ./footage [--color-profile dji-dlog2]
aidirector analyze ./footage            # segments, ASR, VLM, embeddings
aidirector edit ./footage --duration 90 --profile travel_vlog \
    --prompt "..." --captions beats --caption-format "{HH}:{MM} {PLACE}" \
    --subtitles --canvas landscape
aidirector search ./footage "夕焼け"     # semantic search over media memory
aidirector preview <plan-id|latest> [--canvas ...]
aidirector export <plan-id|latest> --format fcpxml|otio|edl|srt
aidirector web                          # UI on :8484
aidirector app [--no-window]            # desktop mode (app window)
```

State lives in `./.aidirector/` (SQLite media memory, proxies, frames,
renders, plans). `AIDIRECTOR_CONFIG` points at an override YAML (used by
Docker).

## Web UI

FastAPI backend (`src/aidirector/web/`) + a single-file vanilla-JS
frontend (`web/static/index.html`). API docs at `/api/docs`. Creation runs
as a single-slot background job with phase/log polling; edits are saved as
new validated plan versions and user actions are recorded as feedback.

## Docker

```bash
docker compose up -d aidirector          # web UI → :8484
docker compose --profile llm up -d       # + Director LLM (llama.cpp)
docker compose run --rm aidirector aidirector analyze /footage
```

`AIDIRECTOR_FOOTAGE` / `AIDIRECTOR_DATA` set the host paths. GPU use needs
the NVIDIA Container Toolkit and the `gpus: all` lines uncommented. On a
single 16 GB GPU run analyze before starting the `llm` profile. Trim the
image with `--build-arg EXTRAS="--extra web --extra speech"`.

## Testing

```bash
uv run pytest        # unit + golden + integration (ffmpeg required)
```

AI output text is never exact-match tested; mock providers return
schema-valid objects (see `tests/conftest.py`). Golden tests cover
Edit Plan → FCPXML/EDL/OTIO.

## Installers & desktop

- `installer/appimage/build.sh` — Linux AppImage (local build, no root)
- `installer/windows/` — NSIS setup.exe (built by CI)
- `.github/workflows/installers.yml` — builds both + the license bundle;
  attaches them to `v*` tag releases
- `scripts/generate_third_party_licenses.py` — regenerates
  `THIRD_PARTY_LICENSES.md` from the current environment
- `desktop/` — `bootstrap.py` (stdlib-only launcher) and the Tauri v2
  shell template; see [desktop/README.md](../desktop/README.md)

Do not bundle: vendor LUTs, Windows system fonts, ffmpeg (without GPL
compliance), NVIDIA runtime libraries (fetched from PyPI instead).

## Repository layout

```
src/aidirector/
├── media/        ingest, ffprobe, metadata, proxy, segmentation, frames
├── color/        profiles, detection, transform registry, LUTs, pipeline
├── perception/   speech, technical CV, vision, embeddings, interpretation
├── ai/           schemas, services facade, runtime manager, providers/
├── memory/       SQLite media memory, repository, search, migrations
├── director/     story/beat planners, selector, editor, critic, prompts/
├── tools/        media/transcript search, similarity, quality
├── timeline/     model, validation, compiler, preview, exports, captions
└── web/          FastAPI app, API routes, jobs, static UI, app window
```

Dependency direction is strictly Media → Color → Perception → AI →
Memory → Director → Timeline (no cycles; see AGENT.md §6/§76).
