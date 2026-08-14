# AI Director — AGENT.md

## 1. Project Vision

**AI Director** は、撮影素材を単純にスコアリングして自動カットするツールではない。

目的は、

> **映像・音声・写真をAIが理解し、ユーザーの意図に基づいて「何を、なぜ、どの順番で、どれくらい見せるか」を考え、人間が仕上げ可能な高品質な編集案を生成するローカルAI Directorを構築すること。**

主な入力:

* DJI Osmo Pocket 系
* DJI D-Log2 / D-Log / D-Log M
* Canon EOS 系
* スマートフォン
* Rec.709
* HLG
* 将来的な C-Log / S-Log / V-Log 等
* 外部音声
* 写真

主な出力:

```text
Edit Plan
Preview MP4
FCPXML
OTIO
EDL
```

最終的なカラーグレーディングや細かな仕上げは DaVinci Resolve / Final Cut Pro 等で人間が行う。

---

# 2. Core Principle

本プロジェクトは **AI-first architecture** とする。

```text
Footage
   ↓
Media Ingest
   ↓
Color Management
   ↓
Perception
   ↓
Media Memory
   ↓
AI Director
   ↓
Edit Plan
   ↓
Timeline Compiler
   ↓
Preview / FCPXML / OTIO / Resolve
```

役割:

```text
Signal Processing / CV
    = 観測する

Color Management
    = AIが正しく見られる映像へ正規化する

VLM
    = 見て理解する

ASR
    = 聞いて文字・時間情報へ変換する

Embedding
    = 記憶を検索可能にする

LLM Director
    = 編集を考える

Deterministic Code
    = 事実・制約・実行を保証する
```

最重要原則:

> **AIは意味を判断し、コードは事実と制約を保証する。**

---

# 3. What AI Director Is Not

以下を最終編集エンジンにしてはならない。

```text
score =
    aesthetic * w1
  + sharpness * w2
  + exposure * w3
  + face * w4
```

高得点順に映像を並べるだけの設計は禁止する。

スコアリングは、

* 明らかな失敗素材の排除
* Candidate ranking
* Duplicate detection
* Retrieval補助
* Technical information

には使用してよい。

しかし、

* どの素材を使うか
* どの部分を使うか
* 何秒見せるか
* どの順番で見せるか
* 動画として何を語るか

は原則 **AI Director** の責務とする。

---

# 4. Primary Use Cases

## 4.1 Travel / Vlog

目標:

* 大量素材から意味のある場面を探す
* 旅の時間・場所・体験の流れを理解する
* 視覚的ハイライトを発見する
* 同じような映像を繰り返さない
* 移動・到着・発見・食事等をストーリーとして扱う
* 環境音を活かす
* 景色だけのPVにならないようにする

---

## 4.2 Talk / Explanation

目標:

* 不要な無音を除去
* フィラーを発見
* 言い直しを検出
* テイク違いを理解
* 発話内容を壊さずテンポを改善
* 意味のある「間」は残す

単純な、

```text
silence > 0.5 sec → delete
```

だけで完成させない。

---

# 5. Architecture

```text
                         User Intent
                              │
                              ▼
                         AI Director
                              ▲
                              │
Footage                       │
   ↓                          │
Media Ingest                  │
   ↓                          │
Color Management              │
   ↓                          │
Perception                    │
   ↓                          │
Media Memory ─────────────────┘
                              │
                              ▼
                          Edit Plan
                              │
                              ▼
                      Timeline Compiler
                         │    │    │
                         ▼    ▼    ▼
                      Preview XML OTIO
                              │
                              ▼
                           Resolve
```

---

# 6. Repository Structure

```text
ai-director/
├── AGENT.md
├── README.md
├── pyproject.toml
├── uv.lock
│
├── config/
│   ├── default.yaml
│   ├── models.yaml
│   ├── color_profiles.yaml
│   └── director_profiles/
│       ├── travel_vlog.yaml
│       ├── cinematic_travel.yaml
│       └── talk.yaml
│
├── assets/
│   └── luts/
│
├── src/aidirector/
│   ├── cli.py
│   ├── config.py
│
│   ├── media/
│   │   ├── ingest.py
│   │   ├── probe.py
│   │   ├── metadata.py
│   │   ├── proxy.py
│   │   ├── frames.py
│   │   └── segment.py
│   │
│   ├── color/
│   │   ├── profile.py
│   │   ├── detect.py
│   │   ├── registry.py
│   │   ├── transforms.py
│   │   ├── lut.py
│   │   └── pipeline.py
│   │
│   ├── perception/
│   │   ├── speech.py
│   │   ├── audio.py
│   │   ├── technical.py
│   │   ├── vision.py
│   │   ├── embeddings.py
│   │   └── interpretation.py
│   │
│   ├── ai/
│   │   ├── services.py
│   │   ├── schemas.py
│   │   ├── runtime.py
│   │   │
│   │   └── providers/
│   │       ├── base.py
│   │       ├── director.py
│   │       ├── vision.py
│   │       ├── speech.py
│   │       └── embedding.py
│   │
│   ├── memory/
│   │   ├── database.py
│   │   ├── models.py
│   │   ├── repository.py
│   │   ├── search.py
│   │   └── migrations.py
│   │
│   ├── director/
│   │   ├── orchestrator.py
│   │   ├── story_planner.py
│   │   ├── beat_planner.py
│   │   ├── selector.py
│   │   ├── editor.py
│   │   ├── critic.py
│   │   ├── schemas.py
│   │   └── prompts/
│   │
│   ├── tools/
│   │   ├── media_search.py
│   │   ├── transcript_search.py
│   │   ├── similarity.py
│   │   └── quality.py
│   │
│   ├── timeline/
│   │   ├── model.py
│   │   ├── validate.py
│   │   ├── compiler.py
│   │   ├── preview.py
│   │   ├── fcpxml.py
│   │   ├── otio.py
│   │   └── edl.py
│   │
│   └── web/
│       ├── app.py
│       ├── api/
│       └── jobs.py
│
├── frontend/
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── fixtures/
│   └── golden/
│
└── cache/
```

依存方向:

```text
Media
 ↓
Color
 ↓
Perception
 ↓
AI Services / Providers
 ↓
Memory
 ↓
Director
 ↓
Timeline
```

循環依存を作らない。

---

# 7. Non-Destructive Media Principle

元素材は絶対に変更しない。

特に、

* D-Log2
* D-Log
* D-Log M
* HDR
* 10-bit素材

をRec.709へ変換したファイルで置き換えてはならない。

```text
Original Camera File
       │
       ├── Analysis Representation
       ├── Preview Proxy
       └── Final NLE Reference
```

FCPXML等では元素材を参照する。

---

# 8. Media Ingest

対象:

```text
Video:
.mp4
.mov
.mts
.m2ts

Audio:
.wav
.mp3
.m4a

Image:
.jpg
.jpeg
.png
.heic
.cr2
.cr3
.dng
```

DJI `.lrf` は本編素材として扱わない。

ただし本編との関連を記録可能な設計にする。

---

# 9. Metadata

ffprobe / ExifTool等で可能な限り取得:

* duration
* codec
* bit depth
* resolution
* frame rate
* time base
* audio streams
* creation time
* color primaries
* transfer characteristics
* matrix coefficients
* HDR metadata
* camera model
* lens
* GPS
* focal length
* ISO
* shutter speed
* aperture
* manufacturer-specific metadata

欠損は正常系として扱う。

---

# 10. Asset Identity

ファイルパスだけをIDにしない。

最低限:

```text
file size
mtime
partial hash
```

必要に応じfull hashを使用可能にする。

---

# 11. Color Management

Color Managementは第一級機能とする。

Log映像をそのままVLMへ入力すると、

* contrast
* saturation
* exposure perception
* atmosphere
* aesthetic judgment

が歪む可能性がある。

そのためAI解析用representationを生成する。

---

# 12. Color Profiles

データモデルは最低限以下を表現可能にする。

```python
class ColorProfile(Enum):
    REC709 = "rec709"

    DJI_DLOG2 = "dji_dlog2"
    DJI_DLOG = "dji_dlog"
    DJI_DLOG_M = "dji_dlog_m"

    HLG = "hlg"

    CANON_CLOG = "canon_clog"
    CANON_CLOG2 = "canon_clog2"
    CANON_CLOG3 = "canon_clog3"

    SONY_SLOG2 = "sony_slog2"
    SONY_SLOG3 = "sony_slog3"

    PANASONIC_VLOG = "panasonic_vlog"

    UNKNOWN = "unknown"
```

初期実装はDJI中心でよい。

---

# 13. DJI Log Processing

```text
Original D-Log2 / D-Log
       │
       ├───────────────────┐
       │                   │
       ▼                   ▼
Original Reference   Analysis Representation
                            │
                            ▼
                       Neutral Transform
                            │
                            ▼
                          Rec.709
                            │
                     ┌──────┴──────┐
                     ▼             ▼
                    VLM       Technical CV
```

LUTを元素材へbakeしない。

---

# 14. Analysis vs UI Preview

二種類を区別可能にする。

```text
Original Log
   │
   ├── Analysis Representation
   │      neutral / consistent
   │
   └── UI Preview
          human-friendly look
```

VLM・Embedding・AI aesthetic interpretationには原則Analysis Representationを使用する。

---

# 15. Color Transform Registry

```python
@dataclass
class ColorTransform:
    id: str
    source_profile: ColorProfile
    destination_profile: ColorProfile
    type: str
    path: Path | None
    vendor: str | None
    version: str | None
```

```python
class ColorTransformRegistry:
    def resolve(
        self,
        source: ColorProfile,
        destination: ColorProfile,
        purpose: str,
    ) -> ColorTransform | None:
        ...
```

purpose:

```text
analysis
preview
```

---

# 16. LUT Distribution

メーカーLUTをライセンス確認なしにrepositoryへ含めない。

必要ならユーザー配置方式:

```text
assets/luts/
```

設定ファイルから参照する。

LUT hashも保存可能にする。

---

# 17. Color Profile Detection

以下を組み合わせる:

```text
container metadata
codec metadata
camera model
color metadata
manufacturer metadata
sidecar metadata
user override
```

自動判定結果にはconfidenceを持たせる。

```json
{
  "profile": "dji_dlog2",
  "confidence": 0.94
}
```

confidenceが低ければ `UNKNOWN` を許容する。

---

# 18. Manual Color Override

```bash
aidirector ingest ./footage \
  --color-profile auto
```

または:

```bash
aidirector ingest ./footage \
  --color-profile dji-dlog2
```

明示指定は自動判定より優先する。

---

# 19. Semantic Segmentation

単純なhard cutを編集単位にしない。

**Semantic Segment** を基本単位とする。

境界候補:

* hard cut
* semantic change
* camera motion change
* speech boundary
* silence
* technical quality change
* long take subdivision
* recording boundary

例:

```text
00:00-00:06  店の外観
00:06-00:14  店へ近づく
00:14-00:21  入店
00:21-00:35  店内
00:35-00:44  料理
```

---

# 20. Perception Layers

```text
Signal
  ↓
Perception
  ↓
Interpretation
```

---

# 21. Signal Layer

決定論的処理:

* sharpness
* blur
* clipping
* exposure anomaly
* shake
* optical flow
* camera motion
* loudness
* silence
* VAD
* shot boundary
* duration

AIを使う必要のない仕事をAIへ任せない。

---

# 22. AI Architecture

モデル固有ライブラリをbusiness logicから隔離する。

三層構造:

```text
Director / Perception Business Logic
                ↓
            AI Services
                ↓
             Providers
                ↓
             Runtime
```

---

# 23. Critical Provider Rule

以下は禁止:

```python
# director/story_planner.py
from transformers import AutoModel
```

または:

```python
# director/editor.py
from faster_whisper import WhisperModel
```

AIライブラリ固有objectをDirector層へ漏らしてはならない。

必ずProviderを通す。

---

# 24. AI Services Facade

business logicからはできるだけtask-oriented APIを使用する。

例:

```python
await ai.understand_segment(segment)

await ai.transcribe(asset)

await ai.embed_segment(segment)

await ai.plan_story(project)

await ai.plan_beats(story)

await ai.select_clips(...)

await ai.critique_edit(edit_plan)
```

AI Servicesは、

* Provider選択
* runtime acquisition
* schema validation
* retry

等を隠蔽する。

---

# 25. Director Provider

```python
class DirectorProvider(Protocol):

    async def generate_structured(
        self,
        messages: list[Message],
        response_model: type[T],
        *,
        thinking: bool | None = None,
    ) -> T:
        ...
```

Directorは特定モデル名を知らない。

---

# 26. Preferred LLM Interface

LLM/VLMのremote/runtime interfaceは原則、

> **OpenAI-compatible API**

を第一候補とする。

理由:

* vLLM
* llama.cpp server
* SGLang
* その他local inference server

と交換しやすい。

ただしCore architectureはHTTPを必須にしない。

---

# 27. OpenAI-Compatible Director Provider

例:

```python
class OpenAICompatibleDirectorProvider:
    ...
```

概念上:

```text
POST /v1/chat/completions
```

または対応runtimeのstructured output APIを利用する。

business logicではHTTP detailsを扱わない。

---

# 28. Structured Output

自由文をregexで解析する設計は禁止。

Pydantic等を利用する。

例:

```python
class StoryPlan(BaseModel):
    concept: str
    tone: str
    pace: str
    story_arc: list[str]
```

呼び出し:

```python
story = await director.generate_structured(
    messages,
    StoryPlan,
)
```

Provider内部で:

1. schemaを与える
2. structured response取得
3. validation
4. 必要ならrepair/retry

を行う。

---

# 29. Vision Provider

```python
class VisionProvider(Protocol):

    async def analyze_segment(
        self,
        images: list[ImageInput],
        context: VisionContext,
    ) -> VisionAnalysis:
        ...
```

入力は原則Color Management済みanalysis representation。

特定Qwen API等をPerceptionから直接呼ばない。

---

# 30. Vision Output

最低限:

```python
class VisionAnalysis(BaseModel):
    description: str

    subjects: list[str]
    actions: list[str]
    mood: list[str]

    camera_motion: str | None

    notable_events: list[str]

    story_roles: list[str]
    narrative_values: list[str]
```

VLMは最終的なclip採用判断を確定しない。

---

# 31. Speech Provider

WhisperをChat APIへ無理に統合しない。

専用interface:

```python
class SpeechProvider(Protocol):

    async def transcribe(
        self,
        audio: Path,
        options: TranscriptionOptions,
    ) -> Transcript:
        ...
```

---

# 32. Standard Transcript Schema

ライブラリ固有ASR objectをDBへ保存しない。

```python
class TranscriptWord(BaseModel):
    start: float
    end: float
    text: str
    probability: float | None = None


class TranscriptSegment(BaseModel):
    start: float
    end: float
    text: str
    words: list[TranscriptWord]


class Transcript(BaseModel):
    language: str
    duration: float
    segments: list[TranscriptSegment]
```

---

# 33. Faster Whisper Provider

初期実装:

```text
FasterWhisperProvider
```

対応:

* local CUDA
* CPU fallback
* word timestamps
* VAD
* language detection

faster-whisperの内部classを他layerへ漏らさない。

---

# 34. Embedding Provider

```python
class EmbeddingProvider(Protocol):

    async def embed_text(
        self,
        texts: list[str],
    ) -> list[Embedding]:
        ...

    async def embed_images(
        self,
        images: list[Path],
    ) -> list[Embedding]:
        ...

    async def embed_video(
        self,
        video: Path,
    ) -> Embedding:
        ...
```

マルチモーダルモデルでは必要に応じ:

```python
class MultimodalEmbeddingInput(BaseModel):
    text: str | None = None
    images: list[Path] = []
    video: Path | None = None
```

を使用してよい。

---

# 35. Provider Implementations

アーキテクチャ上、複数backendを実装可能にする。

## Director

```text
OpenAICompatibleDirectorProvider
LlamaCppDirectorProvider
DirectTransformersDirectorProvider
```

すべてをMVPで作る必要はない。

---

## Vision

```text
OpenAICompatibleVisionProvider
TransformersVisionProvider
```

---

## Speech

```text
FasterWhisperProvider
WhisperHttpProvider
```

---

## Embedding

```text
TransformersEmbeddingProvider
HttpEmbeddingProvider
```

---

# 36. Model Runtime Manager

単一GPU環境を第一級ユースケースとして扱う。

特に16GB GPUではすべてのAIモデルを同時常駐させない。

```python
class ModelRuntimeManager:

    async def acquire(
        self,
        workload: Workload,
    ) -> AIProvider:
        ...

    async def release(
        self,
        workload: Workload,
    ) -> None:
        ...
```

将来的には:

```python
async with runtime.use("vision") as vision:
    ...
```

形式を利用可能にする。

---

# 37. Runtime Responsibilities

Runtime Managerの責務:

* model load
* model unload
* GPU ownership
* lifecycle
* optional server process lifecycle
* VRAM pressure handling
* provider reuse
* cleanup on error

Runtimeはstory logicを知らない。

---

# 38. Single GPU Execution Strategy

RTX 5060 Ti 16GB等では基本的にphase executionを行う。

```text
Vision model load
    ↓
全segmentのVision解析
    ↓
Media Memoryへ保存
    ↓
Vision unload


Whisper load
    ↓
transcription
    ↓
Media Memoryへ保存
    ↓
Whisper unload


Embedding model load
    ↓
embedding生成
    ↓
保存
    ↓
Embedding unload


Director LLM load
    ↓
Story / Beat / Edit / Critic
    ↓
Edit Plan
```

同時常駐を前提としない。

---

# 39. Reference Configuration — RTX 5060 Ti 16GB

初期リファレンス構成:

```text
Vision:
Qwen3-VL-4B class

Director:
8B-class local instruct/reasoning LLM
4-bit quantization where appropriate

Embedding:
Qwen3-VL-Embedding-2B class

Speech:
faster-whisper
large-v3-turbo class

VAD:
Silero VAD or faster-whisper integrated VAD

Technical:
FFmpeg + OpenCV
```

特定モデル名はデフォルト設定として扱い、business logicへハードコードしない。

---

# 40. Example Model Configuration

```yaml
models:

  vision:
    provider: transformers
    model: Qwen/Qwen3-VL-4B-Instruct
    device: cuda

  director:
    provider: openai-compatible
    base_url: http://127.0.0.1:8102/v1
    model: local-director-8b
    context_length: 32768

  speech:
    provider: faster-whisper
    model: large-v3-turbo
    device: cuda
    compute_type: float16

  embedding:
    provider: transformers
    model: Qwen/Qwen3-VL-Embedding-2B
    device: cuda
```

モデル変更でCoreコードを修正してはならない。

---

# 41. Direct vs HTTP Runtime

両方式を許容する。

## Direct

```text
Python
 ↓
Transformers / faster-whisper
 ↓
GPU
```

メリット:

* single-GPU MVPが簡単
* server lifecycle不要

---

## HTTP

```text
AI Director
 ↓
OpenAI-compatible API
 ↓
Inference Server
```

メリット:

* GPUサーバ分離
* 将来的なcluster deployment
* backend交換

AI Director本体はどちらかを前提にしてはならない。

---

# 42. Media Memory

解析結果は単なるcacheではなく、

> **Media Memory**

とする。

AI Directorは元素材を毎回全部読むのではなくMedia Memoryを検索する。

初期DB:

```text
SQLite
```

---

# 43. Database Entities

最低限:

```text
projects

assets
asset_color_profiles
color_transforms

segments
frames
transcripts

technical_features
semantic_annotations
embeddings

director_runs
edit_plans
edit_decisions

user_feedback
```

---

# 44. AI Provenance

AI生成データには:

```text
provider
model
model_version
prompt_version
created_at
```

を保存する。

Visionの場合:

```text
analysis_color_transform
```

も保存する。

ASRでは:

```text
speech_model
options
language
```

を記録可能にする。

---

# 45. Incremental Processing

以下を再利用する:

* metadata
* color detection
* proxies
* frame extraction
* transcript
* technical features
* VLM result
* embeddings
* interpretation
* Edit Plans

invalidate条件:

* source changed
* model changed
* prompt changed
* analysis transform changed
* LUT hash changed
* relevant settings changed

---

# 46. Semantic Search

Media Memoryは検索可能にする。

例:

```text
雨の雰囲気が強い

夕焼け

駅に到着

食事

本人が話している

人のいない風景

移動している

似た構図以外
```

Embedding + metadata filtering + transcript searchを組み合わせる。

---

# 47. AI Director Pipeline

```text
User Intent
    ↓
Story Planner
    ↓
Beat Planner
    ↓
Candidate Retrieval
    ↓
Clip Selector
    ↓
Sequence Editor
    ↓
Critic
    ↓
Revision
    ↓
Edit Plan
```

単一巨大promptは禁止。

同じLLMを複数stageで使うことは問題ない。

---

# 48. Story Planner

目的:

> **この動画は何を語るべきか。**

入力:

* user prompt
* project summary
* location/time summary
* transcript summary
* semantic topic distribution
* major events

出力例:

```json
{
  "concept": "雨だからこそ美しい佐原を歩く",
  "tone": "calm",
  "pace": "slow",
  "story_arc": [
    "arrival",
    "entering town",
    "exploration",
    "food",
    "quiet ending"
  ]
}
```

具体的segment選定は原則まだ行わない。

---

# 49. Beat Planner

Story Arcを時間構造へ変換する。

```json
{
  "target_duration": 90,
  "beats": [
    {
      "name": "hook",
      "duration": 5,
      "purpose": "雰囲気を即座に提示"
    },
    {
      "name": "arrival",
      "duration": 15
    },
    {
      "name": "exploration",
      "duration": 40
    },
    {
      "name": "experience",
      "duration": 20
    },
    {
      "name": "ending",
      "duration": 10
    }
  ]
}
```

---

# 50. Candidate Retrieval

LLMへ全素材を詰め込まない。

Beatごとに検索する。

signals:

* semantic similarity
* transcript
* chronology
* GPS
* technical quality
* visual similarity
* duplicate detection
* lighting
* mood
* camera information

例:

```text
Beat:
arrival

Search:
station / train / arrival / entering town
```

---

# 51. Clip Selector

Directorが候補を比較する。

判断要素:

* story relevance
* chronology
* novelty
* visual variety
* technical quality
* emotion
* speech
* natural audio
* neighboring shots
* uniqueness
* lighting progression
* semantic continuity

---

# 52. Sequence Editor

決定:

* segment
* source in/out
* order
* duration
* audio intent
* transition intent
* story role

必ずreasonを保存する。

```json
{
  "segment_id": "seg_0042_03",
  "source_in": 12.4,
  "source_out": 17.1,
  "story_beat": "opening",
  "audio_intent": "preserve_ambient",
  "transition": "cut",
  "reason": "雨音と歴史的な町並みを冒頭で同時に提示できる"
}
```

---

# 53. Critic

チェック:

* semantic repetition
* visual repetition
* story coherence
* weak hook
* lack of climax
* weak ending
* pacing
* excessive shot similarity
* technical issues
* broken speech
* chronology confusion
* color/lighting monotony

例:

```json
{
  "score": 78,
  "issues": [
    {
      "severity": "medium",
      "type": "repetition",
      "description": "冒頭15秒に類似した広角映像が連続している"
    }
  ],
  "revision_required": true
}
```

MVPではrevision loop最大1〜2回。

---

# 54. Edit Plan

**本プロジェクトの中心データ形式。**

NLE固有formatをmasterにしない。

```text
AI Director
    ↓
Edit Plan
    ↓
Timeline Compiler
```

条件:

* JSON serializable
* schema validated
* DB保存
* versioned
* diff可能
* user editable
* rerender可能

---

# 55. Edit Plan Example

```json
{
  "version": 1,

  "intent": {
    "target_duration": 90,
    "profile": "travel_vlog",
    "user_prompt": "雨の町を静かに歩く旅行Vlog"
  },

  "story": {
    "concept": "雨だからこそ美しい街",
    "tone": "calm"
  },

  "clips": [
    {
      "segment_id": "seg_0042_03",

      "source_in": 12.4,
      "source_out": 17.1,

      "story_beat": "opening",

      "audio": {
        "mode": "original",
        "gain_db": 0
      },

      "transition": {
        "type": "cut"
      },

      "reason": "雨と町並みを同時に提示できる"
    }
  ]
}
```

---

# 56. Validation

AI出力を信用してそのまま実行してはならない。

チェック:

* asset exists
* segment exists
* source bounds
* `source_in < source_out`
* finite duration
* timeline duration
* frame rate sanity
* audio validity
* duplicate references where invalid
* unsupported media state

validation failure時は明確にエラーを返す。

---

# 57. Timeline Compiler

```text
Edit Plan
   ├── FFmpeg Preview
   ├── FCPXML
   ├── OTIO
   └── EDL
```

Timeline Compilerは編集判断を変更しない。

---

# 58. Original Media Reference

```text
AI Analysis:
Normalized proxy

Web Preview:
Proxy

Final NLE Timeline:
Original Camera Media
```

D-Log2なら最終NLEもD-Log2原本を参照する。

---

# 59. Color Normalization vs Creative Grading

明確に分離する。

## Normalization

AIが正しく見るため。

AI Directorの責務。

```text
D-Log2 → neutral Rec.709 analysis representation
```

## Creative Grading

作品のLook。

MVPではNLE/人間の責務。

---

# 60. Talk Editing Tools

Directorへ以下をtoolとして提供可能にする。

```text
get_transcript()

find_silences()

find_fillers()

find_repetitions()

find_speech_segments()

find_bad_audio()
```

Talk Directorはこれらの結果を文脈的に判断する。

---

# 61. Director Tool Interface

将来的にDirectorへ:

```text
search_media(query)

search_transcript(query)

get_segment(id)

find_similar(segment)

find_best_take(query)

find_before(segment)

find_after(segment)

get_project_summary()

get_asset_metadata(asset)

get_color_profile(asset)
```

を提供する。

LLM contextへMedia Memory全部を投入しない。

---

# 62. Director Profiles

編集スタイルは固定アルゴリズムではなくDirector Profileとして表現する。

```yaml
name: travel_vlog

goals:
  - viewer understands progression of the trip
  - preserve memorable atmosphere
  - balance visual beauty with narrative value
  - retain meaningful location transitions

preferences:
  chronology: preferred
  visual_variety: high
  natural_audio: important
  duplicate_shots: avoid

avoid:
  - repeated scenery
  - meaningless long shots
  - excessive transitions
  - excessive jump cuts
```

---

# 63. User Feedback

ユーザー変更を保存:

```text
accept
reject
trim
extend
shorten
reorder
replace
```

可能ならreasonも保存する。

将来的には:

```text
風景を長く残す
人混みを避ける
環境音を好む
トランジションを嫌う
```

等をDirector Contextとして利用する。

---

# 64. CLI First

Web UIなしでCoreが成立すること。

```bash
aidirector ingest ./footage

aidirector analyze ./footage

aidirector edit ./footage \
  --duration 90 \
  --profile travel_vlog \
  --prompt "雨の町を静かに歩く旅行Vlog"

aidirector preview <edit-plan-id>

aidirector export <edit-plan-id> \
  --format fcpxml
```

---

# 65. Web UI

Director品質確認後に実装する。

主要画面:

## Projects

* assets
* progress
* Director runs

## Media Memory

* thumbnails
* descriptions
* transcript
* quality
* source color profile

## Director Proposal

* selected clips
* rejected clips
* reason
* story beat
* duration

## Timeline

* reorder
* trim
* accept/reject
* preview

---

# 66. Error Handling

考慮:

* corrupt media
* ffmpeg failure
* missing metadata
* UNKNOWN color profile
* missing LUT
* GPU OOM
* VLM unavailable
* Director unavailable
* Whisper failure
* malformed structured output
* timeout
* cancelled model runtime

1 asset失敗で全projectを壊さない。

---

# 67. Logging

記録:

```text
asset ingest
metadata
color profile
color transform
proxy generation

model load
model unload

provider
model name

AI invocation
prompt version

transcription

VLM analysis

candidate retrieval

Director run

validation

preview render

export
```

画像や動画本体をログへ埋め込まない。

---

# 68. Testing

AIの自然言語内容を完全一致テストしない。

## Unit

* ffprobe parsing
* metadata
* color detection
* transform registry
* segment models
* transcript schema
* Provider adapters
* structured output validation
* DB
* retrieval
* Edit Plan validation
* timeline conversion

## Golden

```text
Edit Plan → FCPXML

Edit Plan → EDL
```

## Integration

小さい素材で:

```text
Ingest
 ↓
Color
 ↓
Analyze
 ↓
Media Memory
 ↓
Director
 ↓
Edit Plan
 ↓
Preview
```

まで確認する。

---

# 69. Performance Principles

* full-frame analysisを避ける
* representative framesを使う
* proxyを使う
* batch可能ならbatch
* cacheする
  -モデルをsegmentごとにloadしない
* GPU RAMに応じてphase executionする
* embeddingsを再生成しない

将来的なcoarse-to-fine:

```text
coarse:
1 frame / 2 sec

      ↓

interesting region

      ↓

fine:
4-8 frames / sec
```

---

# 70. MVP Scope

最初のProduct Goal:

```bash
aidirector edit ./footage \
  --duration 60 \
  --prompt "落ち着いた旅行Vlog"
```

出力:

```text
edit-plan.json
preview.mp4
```

---

# 71. MVP Implement

* project skeleton
* config
* subprocess wrapper
* SQLite
* media ingest
* ffprobe
* metadata
* color profile model
* DJI Log support structure
* manual color override
* LUT registry
* analysis proxy
* segmentation
* representative frames
* basic CV
* SpeechProvider
* faster-whisper implementation
* VisionProvider
* local VLM implementation
* EmbeddingProvider
* Media Memory
* semantic retrieval
* DirectorProvider
* Story Planner
* Beat Planner
* Candidate Retrieval
* Clip Selector
* Sequence Editor
* Critic
* Edit Plan
* validation
* FFmpeg preview

---

# 72. MVP Do Not Implement

* advanced Web UI
* distributed processing
* Kubernetes
* multi-user architecture
* automatic creative color grading
* Resolve Color Page automation
* music generation
* complex transition generation
* preference model training
* sophisticated photo montage

---

# 73. Development Phases

## Phase 0 — Skeleton

* pyproject
* CLI
* config
* logging
* subprocess wrapper
* SQLite
* Provider interfaces

---

## Phase 1 — Ingest + Color

```text
Camera Media
 ↓
ffprobe
 ↓
metadata
 ↓
Color Detection
 ↓
Analysis Proxy
 ↓
SQLite
```

D-Log2/D-Logでも元素材を変更しない。

---

## Phase 2 — Perception

```text
Asset
 ↓
Segments
 ↓
Frames
 ↓
Technical Analysis
 ↓
Whisper
 ↓
VLM
 ↓
Interpretation
 ↓
Media Memory
```

Deliverable:

各segmentについて人間が理解可能なstructured JSON。

---

## Phase 3 — Retrieval

以下のような検索を成立させる。

```text
夕焼け

雨

駅

食事

歩いている

会話

街並み
```

---

## Phase 4 — Director MVP

```text
Intent
 ↓
Story
 ↓
Beats
 ↓
Retrieval
 ↓
Selection
 ↓
Sequence
 ↓
Critic
 ↓
Edit Plan
```

---

## Phase 5 — Preview

FFmpeg previewを生成。

ここを**最初のProduct Milestone**とする。

編集品質が低ければUIを作らずDirectorを改善する。

---

## Phase 6 — NLE Export

* FCPXML
* OTIO
* EDL

DaVinci Resolveで実地検証する。

元素材reference維持も確認する。

---

## Phase 7 — Web UI

人間がAI編集を確認・修正可能にする。

---

## Phase 8 — Learning From Edits

人間の変更履歴をDirector Contextとして利用する。

---

# 74. Immediate Implementation Order

最初に:

1. project skeleton
2. config
3. common subprocess wrapper
4. DB
5. media ingest
6. ffprobe
7. metadata
8. ColorProfile
9. ColorTransformRegistry
10. analysis proxy
11. Segment models
12. AI schemas
13. DirectorProvider interface
14. VisionProvider interface
15. SpeechProvider interface
16. EmbeddingProvider interface
17. ModelRuntimeManager
18. FasterWhisperProvider
19. Vision implementation
20. Media Memory repository

ここまでで:

```bash
aidirector analyze ./sample-footage
```

から、

```text
asset
camera metadata
color profile
segments
transcript
technical features
semantic analysis
```

をDBへ保存可能にする。

その後:

21. Embedding
22. Retrieval
23. Story Planner
24. Beat Planner
25. Clip Selector
26. Sequence Editor
27. Critic
28. Edit Plan
29. Preview Renderer

を実装する。

---

# 75. Coding Rules

* Python 3.11+
* `uv`
* type hints
* Pydantic
* `pathlib.Path`
* external commandsは共通wrapper経由
* SQLを各moduleへ散らさない
* Provider以外でAI libraryを直接importしない
* model名をbusiness logicへハードコードしない
* package import時に巨大modelをloadしない
* global mutable model instanceを無計画に作らない
* AI出力は必ずschema validation
* asyncは必要なI/O境界で使う
* async化自体を目的にしない

---

# 76. Architecture Rule: AI Libraries Must Not Leak

以下を強く禁止する。

```text
Director
  ↓
transformers directly
```

または:

```text
Director
  ↓
faster-whisper directly
```

正しい構造:

```text
Director
   ↓
AI Services
   ↓
Provider Interface
   ↓
Provider Implementation
   ↓
Runtime / Library
```

これにより、

```text
RTX 5060 Ti local

        ↓ later

DGX / H100 / GPU server
```

へ移行してもbusiness logicを変更しない。

---

# 77. Architecture Rule: Models Are Replaceable

AI Directorの価値は特定モデルではない。

モデルは交換可能な部品とする。

```text
VisionProvider
DirectorProvider
SpeechProvider
EmbeddingProvider
```

の契約を守る限り、

* Qwen
* 別VLM
* 別LLM
* Whisper
* 別ASR

へ置換可能であること。

---

# 78. Architecture Rule: Media Memory Is the Boundary

巨大モデルへ撮影素材全部を直接投入する設計へ逃げない。

基本:

```text
Observe
 ↓
Understand
 ↓
Persist
 ↓
Retrieve
 ↓
Reason
```

これにより、

* 小GPUで動く
* 再解析を減らせる
* モデル交換が容易
* 編集理由を追跡できる
* 長時間素材へスケールできる

---

# 79. Important Color Principle

> **AIはLog原本ではなく、意味理解に適した正規化representationを見る。**

同時に、

> **最終NLEはLog原本を参照する。**

Creative gradingとAI解析用normalizationを混同しない。

---

# 80. Important Runtime Principle

特にsingle-GPU環境では、

> **全モデルを同時にGPUへ載せる必要はない。**

Media Memoryを境界としてphase processingを行う。

---

# 81. Most Important Product Metric

最重要評価指標:

> **AIが作った最初のタイムラインを見た人が「素材の意味を理解して編集している」と感じるか。**

評価対象は、

* モデルサイズ
* AIモデル数
* UI機能数
* 自動処理率

ではない。

---

# 82. Final Guiding Principle

実装判断に迷った場合は、

> **これはAI Directorを賢い映像編集者に近づけるか、それとも動画処理システムを複雑にしているだけか？**

を問う。

さらにAI実装では、

> **このモデル固有処理はProviderの外へ漏れていないか？**

を確認する。

Color処理では、

> **これはAI理解のためのNormalizationか、作品Lookを作るCreative Gradingか？**

を区別する。

本プロジェクトの中心的価値は、

* FFmpeg wrapper
* Whisper wrapper
* LUT処理
* Scene Detection
* Web UI
* 特定LLM

ではない。

> **異なるカメラ・色空間・映像・音声から撮影素材の意味を記憶し、必要な情報を検索し、人間の意図に沿った物語と編集を考え、その判断理由まで説明できるローカルAI Directorそのもの**

である。

