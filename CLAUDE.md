# SmileThresholdSurvey — 開発メモ（Claude Code 向け）

静止画の顔を1枚ずつ見せて、対話中の聞き手の表情として「自然／不自然」を答えてもらう Web 調査（恒常法）と、その集計。
設計は DESIGN.md、使い方は README.md。参加者に見える文言・説明はすべて日本語。

## 守ること

- **列の並びを3か所でそろえる**：`docs/index.html` の `TRIAL_COLS`・`SESSION_COLS`、`gas/Code.gs` の同名の配列、
  `analysis/threshold.py` の読み込み。列を足したら3か所とも直し、`tests/` を通す
- **実験の中身は設定で変える**：刺激・段階・回数・文言は `docs/config.js`（範囲確認は `config.range.js` の差分）。
  `index.html` は設定を読むだけの本体に保つ
- **未決の文言には【仮】を付ける**（DESIGN.md の未決事項と対応させる）。決まっていないことを決まったように書かない
- **刺激画像は可逆圧縮のみ**（PNG）。stimulus_id は `<M|F>_<smile|eye|lip|both>_<強さ×100 の3桁>`。
  一覧は `stimuli/make_stimulus_list.py` から作り、手で `stimuli.csv` を編集しない
- `docs/` は GitHub Pages でそのまま公開される。VRM・回答データ・個人情報を置かない（`.gitignore` 済み）
- Apps Script を直したら、利用者に「新バージョンとしてデプロイし直す」必要があると伝える

## 確認のしかた（push の前に通す）

```bash
python tests/e2e_test.py        # Playwright で回答画面を通す（模擬の保存先つき）
node tests/gas_test.js          # Code.gs を偽のスプレッドシートで動かす
python tests/analysis_test.py   # 模擬データで閾値の回収を確かめる
```
