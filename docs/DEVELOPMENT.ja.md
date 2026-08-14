# AI Director — 開発者向けドキュメント

[English](DEVELOPMENT.md) | 日本語

エンドユーザー向けの使い方はトップの [README](../README.ja.md) にあります。
本書はAI Director自体を開発するためのドキュメントです。**まず
[AGENT.md](../AGENT.md) を読んでください** — アーキテクチャ、レイヤー規約、
Provider境界、カラー原則を定めたプロジェクトの憲法です。

## パイプライン

```
Footage → Media Ingest → Color Management → Perception → Media Memory
        → AI Director → Edit Plan → Timeline Compiler
        → Preview MP4 / FCPXML / OTIO / EDL / SRT → NLE
```

- **カラーマネジメント済み解析**: Log素材(DJI D-Log2/D-Log/D-Log M、HLG等)
  はAIが見る前にニュートラルなRec.709の*解析用表現*へ正規化。元素材は不変で、
  NLE書き出しは原本を参照します。
- **Media Memory**: すべての観測結果(メタデータ、セグメント、文字起こし、
  VLM解析、embedding、テクニカル特徴、provenance)をSQLiteに永続化して検索。
  ディレクターはピクセルではなく記憶に対して推論します。
- **モデルは交換可能**: vision / director / speech / embedding は
  `config/models.yaml` で設定するProvider interfaceの背後にあり、ビジネス
  ロジックはモデルライブラリをimportしません。phase executionにより
  16GB GPU 1枚で完結します。

リファレンスモデル構成(RTX 5060 Ti 16GBで検証済み):

| 役割 | モデル | Provider |
|---|---|---|
| 映像理解 | Qwen3-VL-4B-Instruct | `transformers`(bf16) |
| Director | Qwen3-8B Q4_K_M | `llama-server`(自動管理)or `openai-compatible` |
| Embedding | Qwen3-VL-Embedding-2B | `sentence-transformers` |
| 音声認識 | faster-whisper large-v3-turbo | `faster-whisper` |

## セットアップ

Python 3.11+、`uv`、PATH上の `ffmpeg`/`ffprobe` が必要です。

```bash
uv sync                      # コアのみ
uv sync --extra speech       # + faster-whisper(ローカルASR)
uv sync --extra vision       # + transformers/torch(ローカルVLM)
uv sync --extra embedding    # + sentence-transformers(検索)
uv sync --extra web          # + レビュー/編集Web UI
```

モデルのエンドポイントは `config/models.yaml` で設定します(コードへの
ハードコード禁止)。外部のOpenAI互換directorサーバを使う場合:

```bash
llama-server -hf Qwen/Qwen3-8B-GGUF:Q4_K_M --port 8102 -ngl 99 \
  -c 16384 -fa on --jinja --reasoning-budget 0
```

または `provider: llama-server` でランタイムにプロセス管理を任せます。

## CLIリファレンス

```bash
aidirector ingest ./footage [--color-profile dji-dlog2]
aidirector analyze ./footage            # セグメント、ASR、VLM、embedding
aidirector edit ./footage --duration 90 --profile travel_vlog \
    --prompt "..." --captions beats --caption-format "{HH}:{MM} {PLACE}" \
    --subtitles --canvas landscape
aidirector search ./footage "夕焼け"     # Media Memoryのセマンティック検索
aidirector preview <plan-id|latest> [--canvas ...]
aidirector export <plan-id|latest> --format fcpxml|otio|edl|srt
aidirector web                          # :8484でUI
aidirector app [--no-window]            # デスクトップモード(アプリウィンドウ)
```

状態は `./.aidirector/`(SQLite Media Memory、proxy、フレーム、レンダリング
結果、編集案)に保存されます。`AIDIRECTOR_CONFIG` で上書き設定YAMLを指定
できます(Dockerで使用)。

## Web UI

FastAPIバックエンド(`src/aidirector/web/`)+ 単一ファイルのvanilla JS
フロントエンド(`web/static/index.html`)。APIドキュメントは `/api/docs`。
作成は単一スロットのバックグラウンドジョブ(フェーズ/ログをポーリング)で、
編集は検証済みの新バージョンとして保存され、ユーザー操作はフィードバックと
して記録されます。

## Docker

```bash
docker compose up -d aidirector          # Web UI → :8484
docker compose --profile llm up -d       # + Director LLM(llama.cpp)
docker compose run --rm aidirector aidirector analyze /footage
```

ホスト側パスは `AIDIRECTOR_FOOTAGE` / `AIDIRECTOR_DATA` で指定。GPU利用には
NVIDIA Container Toolkitの導入と `gpus: all` のコメント解除が必要です。
16GB単一GPUでは analyze 後に `llm` プロファイルを起動してください。イメージは
`--build-arg EXTRAS="--extra web --extra speech"` で削減できます。

## テスト

```bash
uv run pytest        # unit + golden + integration(ffmpeg必須)
```

AI出力の自然言語は完全一致テストしません。mock providerがスキーマ準拠の
オブジェクトを返します(`tests/conftest.py`)。golden testは
Edit Plan → FCPXML/EDL/OTIO をカバーします。

## インストーラ・デスクトップ

- `installer/appimage/build.sh` — Linux AppImage(ローカルビルド、root不要)
- `installer/windows/` — NSIS setup.exe(CIでビルド)
- `.github/workflows/installers.yml` — 両方+ライセンス集をビルドし、
  `v*` タグでReleaseに添付
- `scripts/generate_third_party_licenses.py` — 現在の環境から
  `THIRD_PARTY_LICENSES.md` を再生成
- `desktop/` — `bootstrap.py`(標準ライブラリのみのランチャー)と
  Tauri v2シェルテンプレート。[desktop/README.md](../desktop/README.md) 参照

同梱禁止: メーカー製LUT、Windowsシステムフォント、ffmpeg(GPL遵守なしの
場合)、NVIDIAランタイムライブラリ(代わりにPyPIから取得)。

## リポジトリ構成

```
src/aidirector/
├── media/        ingest、ffprobe、メタデータ、proxy、セグメント分割、フレーム
├── color/        プロファイル、判定、変換レジストリ、LUT、パイプライン
├── perception/   音声認識、テクニカルCV、映像理解、embedding、解釈
├── ai/           スキーマ、servicesファサード、ランタイム管理、providers/
├── memory/       SQLite Media Memory、リポジトリ、検索、マイグレーション
├── director/     story/beatプランナー、selector、editor、critic、prompts/
├── tools/        メディア/文字起こし検索、類似度、品質
├── timeline/     モデル、検証、コンパイラ、プレビュー、書き出し、キャプション
└── web/          FastAPIアプリ、APIルート、ジョブ、静的UI、アプリウィンドウ
```

依存方向は Media → Color → Perception → AI → Memory → Director → Timeline
の一方向のみ(循環禁止。AGENT.md §6/§76参照)。
