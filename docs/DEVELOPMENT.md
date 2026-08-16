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
- **Deterministic sequence guarantees**: the director profiles'
  `chronology` and `duplicate_shots` preferences are not just prompt
  advice — after every draft, code reorders clips with known recording
  times oldest-first (unless `chronology: flexible`) and drops second
  uses of the same source video (unless `duplicate_shots: allow`, e.g.
  the talk profile). AI judges meaning; code guarantees facts.
  Recording times come from `creation_time`, refined to frame precision
  by the SMPTE timecode (tmcd) when the two clocks agree —
  `refined_creation_time` rejects record-run/untrusted timecodes.
- **Coverage across re-creations**: per-beat retrieval over-fetches
  semantic hits and reserves half of each beat's candidate slots for the
  source videos least used by the project's saved plans
  (`asset_usage_counts`), so repeatedly re-creating explores the whole
  library instead of resurfacing the same favorites.
- **Replaceable models**: vision / director / speech / embedding /
  music-embedding / music-understanding sit behind provider interfaces
  configured in `config/models.yaml`. Business logic never imports model
  libraries. Phase execution keeps a single 16 GB GPU sufficient.
- **Music library analysis** (BGM feature): tracks in the user's music
  folder are analyzed once — BPM/key/energy (librosa; Essentia is used
  automatically if the user installs it themselves — it is AGPL-3.0, so
  it is never a dependency), CLAP zero-shot genre/mood/instrument tags + a
  stored audio embedding, lyrics/vocal detection via faster-whisper, and
  an optional audio-LLM description — and cached globally in the
  `music_tracks` table keyed by content hash (rename-safe, shared across
  projects). Selection itself never runs a model: the director LLM gets
  an annotated track list; libraries >60 tracks are ranked by a CLAP
  text-query embedding computed on the CPU.

Reference model set (verified on RTX 5060 Ti 16GB):

| Role | Model | Provider |
|---|---|---|
| Vision | Qwen3-VL-4B-Instruct | `transformers` (bf16), `llama-server` (multimodal GGUF + mmproj) or `openai-compatible` |
| Director | Qwen3-8B (NF4 4-bit, in-process) | `transformers` (default), `llama-server` (managed, faster) or `openai-compatible` |
| Embedding | Qwen3-VL-Embedding-2B | `sentence-transformers` |
| Speech | faster-whisper large-v3-turbo | `faster-whisper` |
| Music embedding | CLAP (laion/clap-htsat-unfused) | `transformers` |
| Music understanding | Qwen2.5-Omni-7B, Thinker-only 4-bit (~9 GB peak VRAM) | `transformers` (or `none`) |

## Setup

Python 3.11+, `uv`, and `ffmpeg`/`ffprobe` on PATH.

```bash
uv sync                      # core only
uv sync --extra speech       # + faster-whisper (local ASR)
uv sync --extra vision       # + transformers/torch (local VLM)
uv sync --extra embedding    # + sentence-transformers (retrieval)
uv sync --extra web          # + review/edit web UI
uv sync --extra music        # + BGM analysis (librosa, CLAP, audio LLM)
```

Optional, at your own discretion (AGPL-3.0, Linux x86_64 only — not a
project dependency): `uv pip install essentia==2.1b6.dev1389` upgrades
BPM/key extraction; it is detected and preferred automatically.

Model endpoints live in `config/models.yaml`; never hardcode model names
in code. The default director provider is `transformers`: Qwen3-8B loads
in-process (bitsandbytes NF4) exactly like the other local models — no
external software. For faster generation install llama.cpp and set
`provider: llama-server` (the runtime starts/stops the server around the
director phase, reusing an already-healthy server on the port), or run
any OpenAI-compatible server yourself (`provider: openai-compatible` +
`base_url`), e.g.:

```bash
llama-server -hf Qwen/Qwen3-8B-GGUF:Q4_K_M --port 8102 -ngl 99 \
  -c 16384 -fa on --jinja --reasoning-budget 0
```

## CLI reference

```bash
aidirector ingest ./footage [--color-profile dji-dlog2]
aidirector analyze ./footage            # segments, ASR, VLM, embeddings
aidirector analyze ./footage --reanalyze  # force VLM + embeddings re-run
aidirector edit ./footage --duration 90 --profile travel_vlog \
    --prompt "..." --captions beats --caption-format "{HH}:{MM} {PLACE}" \
    --subtitles --canvas landscape --music-dir ./bgm \
    --flow "出発,電車移動,レストラン"   # beats follow this order verbatim
aidirector music-analyze ./bgm          # pre-analyze a BGM library (cached)
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
The BGM library modal is backed by `GET /api/music/tracks` (scan + hash +
DB lookup only, no probing) and `POST /api/music/analyze` (a second job
slot, mutually exclusive with the create job for the GPU). The plan-save
endpoint keeps `music` when the field is omitted; an explicit `null`
removes it. `#project=…&plan=…` deep links restore the view on reload.

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
Edit Plan → FCPXML/EDL/OTIO (with and without a music track). Music
feature extraction is tested against synthesized click tracks and pure
tones (BPM/key assertions with harmonic tolerance).

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
compliance), NVIDIA runtime libraries (fetched from PyPI instead), and
Essentia (AGPL-3.0 — user-installed only, auto-detected).

## Repository layout

```
src/aidirector/
├── media/        ingest, ffprobe, metadata, proxy, segmentation, frames
├── color/        profiles, detection, transform registry, LUTs, pipeline
├── perception/   speech, technical CV, vision, embeddings, music analysis
├── ai/           schemas, services facade, runtime manager, providers/
├── memory/       SQLite media memory, repository, search, migrations
├── director/     story/beat planners, selector, editor, critic, prompts/
├── tools/        media/transcript search, similarity, quality
├── timeline/     model, validation, compiler, preview, exports, captions
└── web/          FastAPI app, API routes, jobs, static UI, app window
```

Dependency direction is strictly Media → Color → Perception → AI →
Memory → Director → Timeline (no cycles; see AGENT.md §6/§76).
