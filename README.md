# SmileThresholdSurvey

静止画による笑顔の**違和感の閾値**の測定（恒常法・Webアンケート）。
アバタの笑顔（口角）に目・上唇を足した4通りの顔について、違和感が出始める強さを求める。結果は笑顔混合のパラメータ（混合の式・混合比率 α の上限）に使う。
設計と未決事項は [DESIGN.md](DESIGN.md)。

```
docs/                公開するページ（GitHub Pages の公開フォルダ。ここに置いたものはすべて公開される）
  index.html         回答画面。設定を読んで動く汎用の本体
  config.js          本番の設定（4通り × 7段階 × 2体 × 2回 = 112試行）
  config.range.js    範囲確認の設定（0.1〜1.0 × 1回 = 80試行）。?config=range で重ねる
  images/            刺激画像（stimuli/stimuli.csv の stimulus_id.png）
gas/Code.gs          回答を Google スプレッドシートに保存する Apps Script
stimuli/             刺激一覧（stimuli.csv）と、その作成スクリプト
render/              刺激画像の書き出しスクリプト（Box の render/ に置いて使う）
analysis/            閾値の算出（threshold.py）
tests/               回答画面・Apps Script・分析の通しテスト
```

## 進め方

1. 刺激画像を書き出す（VRM のある手元のPC）
2. 保存先（Google スプレッドシート）を用意する
3. 公開する（GitHub Pages）
4. 実施前の確認
5. 範囲確認（研究室内で数人）→ 強さの段階を確定
6. 本番（クラウドソーシング）
7. 集計

---

## 1. 刺激画像を書き出す

刺激は 88 枚（4通り × 11段階 × 2体。本番の 56 枚と範囲確認の 80 枚を合わせて重複を除いたもの）。
VRM と描画の道具は Box の「260903_静止画による笑顔評価」にあるので、`render/render_threshold_stimuli.py` をそこの `render/` に置いて実行する。
構図・照明・画素数（1000×1250）は実験1の刺激と同じになる。

```powershell
cd "<Box>\ミミックでラポール形成\03_予備実験\260903_静止画による笑顔評価\render"
copy <リポジトリ>\render\render_threshold_stimuli.py .
python render_threshold_stimuli.py <リポジトリ>\stimuli\stimuli.csv <リポジトリ>\docs\images
```

- 書き出し後、`render/閾値刺激一覧_M.png`・`_F.png`（行＝4通り、列＝強さ）で見え方を確認する
- **画像には非可逆圧縮をかけない**（条件間の差が小さいため。スクリプトは可逆圧縮のみ）
- 強さや組み合わせを変えるときは `stimuli/make_stimulus_list.py` を直して `stimuli.csv` を作り直す
- 画像が無い間も、回答画面は線画の代替表示で最後まで動く（`image_loaded = 0` として記録され、分析では除外される）

## 2. 保存先を用意する

1. 新しいスプレッドシートを作る（例：`261011_違和感の閾値_回答`）
2. 「拡張機能 > Apps Script」を開き、`gas/Code.gs` の中身をすべて貼り付けて保存する
3. 「デプロイ > 新しいデプロイ > 種類の選択：ウェブアプリ」
   - 次のユーザーとして実行：**自分**
   - アクセスできるユーザー：**全員**
4. 発行された `https://script.google.com/macros/s/……/exec` をブラウザで開き、`{"ok":true,...}` が返ることを確かめる
5. その URL を `docs/config.js` の `submitUrl` に入れて push する

シートは最初の回答で自動的にできる：`sessions`（参加者ごと1行）、`responses`（試行ごと1行）、`raw`（受け取った JSON そのまま。取りこぼしの復元用）。
同じ回答の再送は重複して保存されない。スクリプトを直したときは「デプロイを管理 > 編集 > 新バージョン」で出し直す。

`submitUrl` が空、または送信に失敗したときは、最後に回答を CSV で保存してもらう画面に切り替わる（回答は失われない）。

## 3. 公開する

GitHub の **Settings > Pages** で、Source を「Deploy from a branch」、Branch を `main`、フォルダを `/docs` にする。
公開URL：<https://decchi.github.io/SmileThresholdSurvey/>

募集ページには、公開URLと、完了コード（本番は `SMILET-`、範囲確認は `SMILER-` で始まる）の記入欄を用意する。
参加者IDは同意画面で入力してもらう（URL に `?pid=<ID>` を付ければ入力欄を省ける）。完了コードは参加者IDから決まるので、`analysis/threshold.py --check-codes` で照合できる。

## 4. 実施前の確認

- [ ] 88 枚すべての画像が表示され、「画像未配置（代替表示）」が出ない
- [ ] 自分で通しで1回答し、`sessions` 1行・`responses` 116行（練習2・本試行112・確認問題2）・`raw` 1行が入る
- [ ] `?demo` の短縮版が動く（`mode = demo` で記録され、分析では除かれる）
- [ ] 手元のノートPCで、顔と回答ボタンがスクロールせずに収まる
- [ ] 画面較正で、カードと四角形が重なる（複数の端末で）
- [ ] 質問文・説明文を確定し、`config.js` の【仮】を外す（DESIGN.md 未決事項1）
- [ ] 研究室内で数名に見せ、刺激が意図どおり書き出されているかを点検する

## 5. 範囲確認

公開URLに `?config=range` を付けて開く（80試行、約7分）。集計は `--experiment range`。

## 6. URL パラメータ

| パラメータ | 効果 |
|---|---|
| `?pid=<ID>` | 参加者IDを埋めた状態で開く |
| `?config=range` | `config.range.js` を重ねて読む（範囲確認）。名前は英小文字・数字・下線のみ |
| `?demo` | 短縮版（練習1・本試行6・確認問題1）。動作確認用で、`mode = demo` として記録 |
| `?nocheck` | スマートフォン判定を素通りする（動作確認用） |
| `?seed=<数>` | 提示順の乱数の種を固定する（既定はランダム。種は記録される） |
| `?order=yes_left` / `yes_right` | 「違和感あり」ボタンの左右を固定する（既定は参加者IDで振り分け） |
| `?debug` | 動作確認用に内部状態を `window.__SURVEY__` に出す |

## 7. 集計

```bash
pip install -r analysis/requirements.txt
# スプレッドシートの sessions・responses をそれぞれ CSV でダウンロードして
python analysis/threshold.py --responses responses.csv --sessions sessions.csv --out results
# CSV で提出された回答があれば --csv-dir <フォルダ> を追加。raw シートからの復元は --raw raw.csv
```

出力は `results/` に、`thresholds.csv`（4通り × {全体・男性・女性} の 50%点・25%点と 95%信頼区間）、`proportions.csv`、`participants.csv`（除外の理由・印・完了コード）、`psychometric.png`・`psychometric_by_avatar.png`、`summary.txt`。

除外するのは、確認問題の不正解・画像の読み込み失敗・同じ参加者IDの2回目以降だけ。反応の速すぎや全試行同じ答えは印を付けるだけにしてある（基準はデータを見る前に決める。DESIGN.md 未決事項5）。

## 8. 記録される項目

`responses`（試行ごと）：`phase`（practice / main / attention）、`block`、`stimulus_id`・`avatar`・`combo`・`level`・`smile`・`eye`・`lip`、`response`（1 = 違和感あり、0 = 違和感なし）、`correct`（確認問題のみ）、`key`（F / J / クリック）、`rt_ms`（顔の提示から回答まで）、`display_mm`（実際に表示した顔の幅）、`image_loaded` など。

`sessions`（参加者ごと）：確認問題の結果、画面較正（`px_per_mm`）、表示幅、所要時間、ボタンの左右、乱数の種、年齢・性別・VR経験・自由記述、完了コードなど。

## 9. テスト

```bash
pip install playwright pillow && playwright install chromium
python tests/e2e_test.py          # 回答画面を模擬参加者で通す（本番・範囲確認・送信失敗・画像なし・スマホ・1366×657）
node tests/gas_test.js            # Apps Script を偽のスプレッドシートで動かす
python tests/analysis_test.py     # 閾値が分かっている模擬データで分析を確かめる
```
