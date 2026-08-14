<p align="center">
  <img src="assets/icon/icon.png" width="128" alt="AI Director アイコン">
</p>

# AI Director

[English](README.md) | 日本語 | [開発者向けドキュメント](docs/DEVELOPMENT.ja.md)

**撮影してきた動画を渡して「どんな動画にしたいか」を書くと、AIが素材の中身を理解して編集案を作ってくれる、完全ローカルの動画編集AIです。**

- 素材の映像・音声をAIが解析し、「どこで何が起きているか」を記憶します
- 「雨の町を静かに歩く旅行Vlog、60秒で」のような指示から、構成・カット・順番・長さをAIディレクターが判断します
- なぜそのカットを選んだのか、**すべての判断に理由が残ります**
- 結果はプレビュー動画とタイムラインで確認でき、ブラウザ上で自由に手直しできます
- 仕上げはDaVinci ResolveやFinal Cut Proへ引き継げます(元素材はそのまま参照)
- **すべて自分のPCで動きます。** 素材がクラウドに送られることはありません

## 必要なもの

| 項目 | 内容 |
|---|---|
| OS | Windows 10/11 または Linux |
| GPU | NVIDIA GPU(VRAM 16GB推奨、例: RTX 5060 Ti)。無くても動きますが低速です |
| ディスク | AIモデル用に約20GBの空き |
| ffmpeg | 動画処理に必須(下記参照) |

## インストール

### Windows

1. [Releases](../../releases) から `AIDirector-Setup.exe` をダウンロードして実行(管理者権限不要)
2. ffmpegを導入: `winget install Gyan.FFmpeg`
3. スタートメニューの「AI Director」を起動 — **初回はAIモデルなど数GBをダウンロードするため時間がかかります**

> インストーラは現在コード署名されていないため、SmartScreenの警告が出る
> ことがあります(「詳細情報」→「実行」で続行)。**スマートアプリコントロール**
> が有効な場合は未署名アプリが一律ブロックされ、アプリ単位の許可はできません。
> Windowsセキュリティ →「アプリとブラウザーの制御」でオフにする必要があります
> (一度オフにすると再有効化にはWindowsの再インストールが必要な点に注意)。

### Linux

1. [Releases](../../releases) から `AIDirector-x86_64.AppImage` をダウンロード
2. 実行権限を付けて起動:

```bash
chmod +x AIDirector-*.AppImage
./AIDirector-*.AppImage
```

3. ffmpegは各ディストリのパッケージで(`sudo zypper in ffmpeg` / `sudo apt install ffmpeg`)

### インストーラを使わない場合

Python 3.9以上があれば、リポジトリを取得して1コマンドです:

```bash
git clone <このリポジトリ> && cd ai-director
python desktop/bootstrap.py
```

### AIディレクター用LLMについて

編集判断を行うLLM(Qwen3-8B)はllama.cppで動きます。`config/models.yaml` の
`director` を `provider: llama-server` にしておくと、**必要なときだけ自動で
起動・終了**します(初回はモデル約5GBを自動ダウンロード)。llama.cppの導入は
Windowsなら `winget install ggml.llamacpp`、Linuxは[公式リリース](https://github.com/ggml-org/llama.cpp/releases)から。

## 使い方

起動するとアプリウィンドウが開きます(ウィンドウを閉じるとアプリも終了します)。

### 1. 動画を作る

1. **「+ 新規作成」** をクリック
2. **素材パス**に撮影した動画が入ったフォルダを指定(動画の本数がその場で表示されます)
3. **指示**に作りたい動画を日本語で書く
   例: 「静かな日本の旅。電車で到着し、川沿いを歩き、古い寺を訪ね、鐘の音で締めくくる」
4. 目標の長さ・スタイル(旅行Vlog / シネマティック / トーク)を選ぶ
5. お好みでオプションを設定:
   - **キャプション** — 場面転換時に時刻と場所を中央に表示(表示形式も指定可、例: `{HH}:{MM} {PLACE}`)
   - **発話テロップ** — 話した内容を文字起こしして字幕として表示
6. **「作成開始」** — 解析からレンダリングまでの進行状況が表示されます

初回は素材の解析(AIが映像を「見て」内容を記憶する処理)に時間がかかりますが、
**2回目以降は解析結果を再利用**するので、指示を変えての作り直しは数分で終わります。

### 2. 手直しする

できあがった編集案はタイムラインに並びます。各カットには**AIが選んだ理由**が添えられています。

- **並べ替え** — ↑↓ボタン
- **使う範囲の調整** — フィルムストリップの緑の枠をドラッグ(枠の端でIN/OUT、中央づかみで位置ずらし)
- **除外** — ✕ボタン
- **キャプション・テロップの修正** — その場でテキスト編集
- **カットの追加** — 右側の「Media Memory」(AIが理解した全素材の一覧)からクリックで追加

**「保存」**すると新しいバージョンとして保存され(元の案も残ります)、
**「プレビュー生成」**で動画を作り直せます。

### 3. 編集ソフトへ引き継ぐ

納得できたら、お使いの編集ソフト用に書き出せます。書き出しは**撮影した元
ファイルをそのまま参照**します(D-LogなどのLog素材も原本のまま。カラー
グレーディングは編集ソフトで自由にできます)。

```bash
aidirector export latest --format fcpxml   # Final Cut Pro / DaVinci Resolve
aidirector export latest --format otio     # OpenTimelineIO
aidirector export latest --format edl      # CMX3600 EDL
aidirector export latest --format srt      # 字幕ファイル
```

キャプションとテロップは編集可能なタイトル(FCPXML)や字幕ファイル(SRT)として引き継がれます。

## データの保存場所

| データ | 場所 |
|---|---|
| 解析結果・編集案・プレビュー | 作業フォルダの `.aidirector/` |
| AIモデル | `~/.cache/huggingface`、`~/.cache/llama.cpp` |
| **撮影した元素材** | **一切変更されません** |

## よくある質問・トラブル

**Q. GPUのメモリが足りないと言われる / 解析が途中で止まる**
16GB GPUでは設計上、モデルを1つずつ順番に使います。他のGPUアプリ(ゲーム、
別のLLM)を閉じてから実行してください。

**Q. 色がおかしい(白っぽい映像のまま解析される)**
D-LogなどのLog素材はメーカー公式LUTがあると正確に解析できます。公式サイトから
LUT(.cube)をダウンロードして `assets/luts/` に置いてください(ライセンスの
関係で同梱できません)。無い場合も簡易補正で動作します。

**Q. 色の自動判定が間違っている**
作成時に明示指定できます: `aidirector ingest ./footage --color-profile dji-dlog2`

**Q. アプリウィンドウが開かない**
Chrome/Edge/Chromiumが見つからない場合は通常のブラウザタブで開きます。
`aidirector app --no-window` で最初からブラウザ表示にできます。

**Q. 編集案の質がいまいち**
指示を具体的に(見せたい順番・雰囲気・残したい場面)。素材が多いほど、また
撮影時刻メタデータがあるほど、時系列を活かした構成になります。

## ライセンス

MIT License。同梱・利用している第三者ソフトウェアは
[THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md) を参照してください。

開発に参加する方は [docs/DEVELOPMENT.ja.md](docs/DEVELOPMENT.ja.md) と
[AGENT.md](AGENT.md)(設計原則)へ。
