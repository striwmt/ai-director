# AI Director

[English README is here](README.md)

**AI Director** は、映像・音声・写真を(カメラや色空間の違いを越えて)AIが
*理解*し、ユーザーの意図から物語を設計して、人間が仕上げ可能な高品質の
編集案を生成するローカルファーストのAIディレクターです。素材をスコアリング
して自動カットするツールではありません。LLMディレクターが「何を、なぜ、
どの順番で、何秒見せるか」を判断し、その理由まで説明できます。

アーキテクチャと設計原則の全文は [AGENT.md](AGENT.md) を参照してください。

## パイプライン

```
Footage → Media Ingest → Color Management → Perception → Media Memory
        → AI Director → Edit Plan → Timeline Compiler
        → Preview MP4 / FCPXML / OTIO / EDL → DaVinci Resolve / FCP
```

- **カラーマネジメント済み解析**: Log素材(DJI D-Log2/D-Log/D-Log M、HLG等)は
  AIが見る前にニュートラルなRec.709の*解析用表現*へ正規化されます。
  元素材は一切変更せず、NLE書き出しは原本を参照します。
- **Media Memory**: すべての観測結果(メタデータ、セグメント、文字起こし、
  VLM解析、embedding、テクニカル特徴)はSQLiteに永続化され検索されます。
  ディレクターは生のピクセルではなく記憶に対して推論します。
- **モデルは交換可能な部品**: vision / director / speech / embedding の各
  Providerは `config/models.yaml` で設定します(OpenAI互換サーバ、
  faster-whisper、transformers)。ビジネスロジックはモデルライブラリに
  一切触れません。単一GPUでのphase executionが標準の実行戦略です。

リファレンスモデル構成(RTX 5060 Ti 16GBクラス、すべてローカル):

| 役割 | モデル |
|---|---|
| 映像理解 | Qwen3-VL-4B-Instruct(transformers, bf16) |
| Director | Qwen3-8B Q4_K_M(llama.cppサーバ、OpenAI互換) |
| Embedding | Qwen3-VL-Embedding-2B(sentence-transformers) |
| 音声認識 | faster-whisper large-v3-turbo |

## セットアップ

Python 3.11+、`uv`、PATH上の `ffmpeg`/`ffprobe` が必要です。

```bash
uv sync                      # コア
uv sync --extra speech       # + faster-whisper(ローカルASR)
uv sync --extra vision       # + transformers/torch(ローカルVLM)
uv sync --extra embedding    # + sentence-transformers(検索)
uv sync --extra web          # + レビュー/編集Web UI
```

モデルのエンドポイントは `config/models.yaml` で設定します。Director LLMの
起動例:

```bash
llama-server -hf Qwen/Qwen3-8B-GGUF:Q4_K_M --port 8102 -ngl 99 \
  -c 16384 -fa on --jinja --reasoning-budget 0
```

メーカー製LUTは `assets/luts/` に配置してください
(`assets/luts/README.md` 参照)。

## 使い方

```bash
aidirector ingest ./footage                      # スキャン + メタデータ + 色プロファイル判定
aidirector ingest ./footage --color-profile dji-dlog2   # 手動指定

aidirector analyze ./footage                     # セグメント、ASR、VLM、embedding

aidirector edit ./footage \
  --duration 90 \
  --profile travel_vlog \
  --prompt "雨の町を静かに歩く旅行Vlog" \
  --captions beats \
  --caption-format "{HH}:{MM} {PLACE}"           # → edit-plan.json + preview.mp4

aidirector search ./footage "夕焼け"              # Media Memoryのセマンティック検索

aidirector preview latest --canvas landscape     # プレビュー再レンダリング
aidirector export latest --format fcpxml         # NLE書き出し(原本メディア参照)
```

状態は `./.aidirector/` に保存されます(SQLiteのMedia Memory、proxy、
フレーム、レンダリング結果、編集案)。

### 場面キャプション

`--captions beats|clips` で、場面転換後に時刻・場所のキャプションを中央に
表示します。場所はDirectorが素材から明確に特定できた場合のみ命名し、時刻は
撮影メタデータ由来です — 事実がなければキャプションは出しません。レイアウトは
`--caption-format` でテンプレート指定できます(例: `"{HH}:{MM} {PLACE}"`。
トークン: `{PLACE} {DATE} {TIME} {YYYY} {MO} {DD} {HH} {MM}`、`\n` で
小さめの2行目)。キャプションはEdit Planの一部(編集可能なJSON)で、
NLE書き出しにも引き継がれます(FCPXMLタイトル / OTIOマーカー / EDLコメント)。

### レビュー / 編集 Web UI

```bash
aidirector web            # → http://127.0.0.1:8484/
aidirector app            # デスクトップモード: 独立したアプリウィンドウ(下記参照)
```

ブラウザから作成(素材パス+プロンプト+設定を入力、フェーズ/ログの
ライブ進行表示付き)、並べ替え、トリム(フィルムストリップ上でIN/OUT
ハンドルをドラッグ)、クリップ除外、キャプション編集、Media Memoryからの
セグメント追加、新バージョンとしての保存(操作はフィードバックとして記録)、
プレビューの再レンダリングができます。

### Director LLMのマネージド起動(手動サーバ不要)

`config/models.yaml` で `provider: llama-server` を指定すると、AI Director
がdirectorフェーズの前後で `llama-server` を自動起動・自動停止します —
映像解析中にVRAMを占有せず、ポート上に既存サーバがあればそれを再利用
します。Windows / Linux 両対応です(キャプションフォントとCUDAランタイム
の解決はプラットフォーム別に行われます)。

## デスクトップアプリ(Windows / Linux)

`aidirector app` は**独立したアプリウィンドウ**(Chromiumのappモード —
WindowsではEdge、他ではChrome/Chromium)を開きます。ウィンドウを閉じると
バックエンドも終了します。新しいマシンでは1コマンドでセットアップできます:

```bash
python desktop/bootstrap.py     # uvを取得→環境構築→アプリ起動
```

インストーラ: `installer/appimage/build.sh` でLinux AppImageをローカル
ビルドできます。CI(`installers.yml`)はこれに加えWindowsの
`AIDirector-Setup.exe`(NSIS)とサードパーティライセンス集をビルドします。
`desktop/tauri` にはTauri v2シェルのテンプレートもあります —
[desktop/README.md](desktop/README.md) 参照。

## Docker

```bash
docker compose up -d aidirector          # Web UI → http://localhost:8484/
docker compose --profile llm up -d       # + Director LLM(llama.cppサーバ)

# CLI(素材は /footage に読み取り専用でマウントされます):
docker compose run --rm aidirector aidirector analyze /footage
docker compose run --rm aidirector aidirector edit /footage \
  --duration 60 --prompt "落ち着いた旅行Vlog"
```

- ホスト側パス: `AIDIRECTOR_FOOTAGE`(デフォルト `./footage`)と
  `AIDIRECTOR_DATA`(デフォルト `./data`、Media Memoryとレンダリング結果を
  保持)で指定します。
- GPU: [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html)
  を導入し、`docker-compose.yaml` の `gpus: all` のコメントを外して、
  `director-llm` を `:server-cuda` イメージに切り替えてください。GPUなし
  でもすべてCPUで動作します(低速ですが機能します)。
- 16GB単一GPUでは、まず `analyze` を実行してから `llm` プロファイルを起動
  してください — phase executionによりモデル同士のVRAM競合を避けます
  (AGENT.md §38)。
- イメージサイズ: デフォルトビルドはtorch/transformersを含むため数GBに
  なります。`--build-arg EXTRAS="--extra web --extra speech"` で削減できます。

## 開発

```bash
uv run pytest             # unit + golden + integrationテスト(ffmpeg必須)
```

ディレクトリ構成はAGENT.md §6に従います:
`src/aidirector/{media,color,perception,ai,memory,director,tools,timeline,web}`。
