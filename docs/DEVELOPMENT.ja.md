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
- **決定的なシーケンス保証**: directorプロファイルの `chronology` /
  `duplicate_shots` 設定はプロンプトへの助言にとどまらず、ドラフト生成の
  たびにコードが強制します — 撮影時刻が分かるクリップは古い順に並べ替え
  (`chronology: flexible` 時を除く)、同じ元動画の2回目以降の使用は削除
  (`duplicate_shots: allow`、例: talkプロファイル時を除く)。
  AIは意味を判断、コードは事実を保証。
  撮影時刻は `creation_time` 由来で、SMPTEタイムコード(tmcd)が時計と
  整合する場合は `refined_creation_time` がフレーム精度に高精度化します
  (record-run等の信用できないタイムコードは棄却)。
- **作り直しでの素材網羅**: beatごとの候補検索はセマンティック検索結果を
  多めに取得し、候補枠の半分を「保存済みプランでの使用回数が最も少ない
  元動画」(`asset_usage_counts`)に割り当てます。何度も作り直すたびに
  未使用の素材が候補に上がり、同じ顔ぶれの再登場ではなくライブラリ全体が
  掘り起こされます。
- **モデルは交換可能**: vision / director / speech / embedding /
  music_embedding / music_understanding は `config/models.yaml` で設定する
  Provider interfaceの背後にあり、ビジネスロジックはモデルライブラリを
  importしません。phase executionにより16GB GPU 1枚で完結します。
- **BGMライブラリ解析**: 音楽フォルダの各曲を一度だけ解析 — BPM/キー/
  エネルギー(librosa。EssentiaはAGPL-3.0のため依存に含めず、ユーザーが
  自分で導入した場合のみ自動検出して優先利用)、CLAPによる
  ゼロショットのジャンル/ムード/楽器タグ+音声embedding保存、faster-whisper
  による歌詞/ボーカル判定、音声LLMによる説明文(任意)— 結果はコンテンツ
  ハッシュをキーに `music_tracks` テーブルへグローバルにキャッシュ
  (リネーム耐性あり・プロジェクト間共有)。選曲時はモデルを動かさず、
  director LLMに注釈付きトラックリストを渡します(60曲超はCLAPテキスト
  クエリ埋め込みを**CPU**で計算してランキング)。

リファレンスモデル構成(RTX 5060 Ti 16GBで検証済み):

| 役割 | モデル | Provider |
|---|---|---|
| 映像理解 | Qwen3-VL-4B-Instruct | `transformers`(bf16)、`llama-server`(マルチモーダルGGUF+mmproj)or `openai-compatible` |
| Director | Qwen3-8B(NF4 4bit・インプロセス) | `transformers`(既定)、`llama-server`(自動管理・高速)or `openai-compatible` |
| Embedding | Qwen3-VL-Embedding-2B | `sentence-transformers` |
| 音声認識 | faster-whisper large-v3-turbo | `faster-whisper` |
| 楽曲embedding | CLAP(laion/clap-htsat-unfused) | `transformers` |
| 楽曲理解 | Qwen2.5-Omni-7B(Thinkerのみ4bit、ピーク約9GB VRAM) | `transformers`(または `none`) |

## セットアップ

Python 3.11+、`uv`、PATH上の `ffmpeg`/`ffprobe` が必要です。

```bash
uv sync                      # コアのみ
uv sync --extra speech       # + faster-whisper(ローカルASR)
uv sync --extra vision       # + transformers/torch(ローカルVLM)
uv sync --extra embedding    # + sentence-transformers(検索)
uv sync --extra web          # + レビュー/編集Web UI
uv sync --extra music        # + BGM解析(librosa、CLAP、音声LLM)
```

任意・自己判断で(AGPL-3.0、Linux x86_64のみ。プロジェクトの依存ではありま
せん): `uv pip install essentia==2.1b6.dev1389` でBPM/キー抽出が高精度化
します(導入されていれば自動検出して優先利用)。

モデルのエンドポイントは `config/models.yaml` で設定します(コードへの
ハードコード禁止)。directorの既定providerは `transformers` で、Qwen3-8Bを
他のローカルモデルと同じくインプロセスでロードします(bitsandbytes NF4、
外部ソフト不要)。より高速にするにはllama.cppを導入して
`provider: llama-server`(ランタイムがdirectorフェーズの前後でサーバを
起動・終了。ポート上に既に健全なサーバがあれば再利用)、または外部の
OpenAI互換サーバを自分で動かして `provider: openai-compatible` +
`base_url` を設定します:

```bash
llama-server -hf Qwen/Qwen3-8B-GGUF:Q4_K_M --port 8102 -ngl 99 \
  -c 16384 -fa on --jinja --reasoning-budget 0
```

## CLIリファレンス

```bash
aidirector ingest ./footage [--color-profile dji-dlog2]
aidirector analyze ./footage            # セグメント、ASR、VLM、embedding
aidirector edit ./footage --duration 90 --profile travel_vlog \
    --prompt "..." --captions beats --caption-format "{HH}:{MM} {PLACE}" \
    --subtitles --canvas landscape --music-dir ./bgm \
    --flow "出発,電車移動,レストラン"   # beatはこの順で固定される
aidirector music-analyze ./bgm          # BGMライブラリの事前解析(キャッシュ)
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
BGMライブラリモーダルは `GET /api/music/tracks`(スキャン+ハッシュ+DB参照
のみ、プローブなし)と `POST /api/music/analyze`(第2のジョブスロット。GPU
保護のため作成ジョブと相互排他)を使います。プラン保存APIは `music`
フィールド省略時に既存値を維持し、明示的な `null` で削除します。
`#project=…&plan=…` のディープリンクでリロード時に表示を復元します。

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
Edit Plan → FCPXML/EDL/OTIO(BGMトラックあり/なし)をカバーします。楽曲
特徴量は合成クリック音・純音に対してテストします(BPM/キーを倍音許容付きで
検証)。

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
場合)、NVIDIAランタイムライブラリ(代わりにPyPIから取得)、
Essentia(AGPL-3.0 — ユーザー導入時のみ自動検出)。

## リポジトリ構成

```
src/aidirector/
├── media/        ingest、ffprobe、メタデータ、proxy、セグメント分割、フレーム
├── color/        プロファイル、判定、変換レジストリ、LUT、パイプライン
├── perception/   音声認識、テクニカルCV、映像理解、embedding、楽曲解析
├── ai/           スキーマ、servicesファサード、ランタイム管理、providers/
├── memory/       SQLite Media Memory、リポジトリ、検索、マイグレーション
├── director/     story/beatプランナー、selector、editor、critic、prompts/
├── tools/        メディア/文字起こし検索、類似度、品質
├── timeline/     モデル、検証、コンパイラ、プレビュー、書き出し、キャプション
└── web/          FastAPIアプリ、APIルート、ジョブ、静的UI、アプリウィンドウ
```

依存方向は Media → Color → Perception → AI → Memory → Director → Timeline
の一方向のみ(循環禁止。AGENT.md §6/§76参照)。
