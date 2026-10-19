# -*- coding: utf-8 -*-
"""刺激一覧 stimuli.csv を作る

    python make_stimulus_list.py

見せる顔は「口角だけ・＋目・＋両方（目と上唇）」の3通り × 強さ × 2体。
目・上唇は混合の式で 0 から口角の値までに収まるので、上端（口角と同じ値）を使う。
左右は同じ値（左右の平均を左右に適用するため）。口の開き（Jaw_Open）は 0。

「＋上唇」（目なしで上唇だけ）は 2026-10-19 に外した（上唇は目と一緒に混ぜる場合だけを確かめる）。
書き出し済みの画像を残すため、一覧には used_in = unused として残す。戻すときは COMBOS の use を True にする。

強さは本番と範囲確認の和集合（11段階）。どの画像をどこで使うかは docs/config*.js が決める。
  本番     0.25, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0（7段階）
  範囲確認 0.1, 0.2, ..., 1.0（10段階）
  練習     本番は 0.4、範囲確認は 0.25（それぞれ本試行に無い強さ）

列は 260903_02_刺激一覧_実験1.csv と同じ形式（書き出しスクリプトがそのまま読める）。
"""
import csv
import io
import os

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'stimuli.csv')

AVATARS = [('M', 'male'), ('F', 'female')]
COMBOS = [  # (id, 表示名, 目を足すか, 上唇を足すか, 今の設計で使うか)
    ('smile', '口角だけ', False, False, True),
    ('eye', '＋目', True, False, True),
    ('lip', '＋上唇', False, True, False),
    ('both', '＋両方', True, True, True),
]
MAIN = [0.25, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]
RANGE = [round(0.1 * i, 1) for i in range(1, 11)]
PRACTICE = {  # 練習に使う刺激（本試行に無い強さ）
    'main': [('M', 'both', 0.4), ('F', 'smile', 0.4)],
    'range': [('M', 'both', 0.25), ('F', 'smile', 0.25)],
}
LEVELS = sorted(set(MAIN) | set(RANGE) | {0.4})

COLS = ['stimulus_id', 'avatar', 'combo', 'combo_label', 'level',
        'Mouth_Smile_Left', 'Mouth_Smile_Right', 'Eye_Left_Squeeze', 'Eye_Right_Squeeze',
        'Mouth_Upper_UpLeft', 'Mouth_Upper_UpRight', 'Jaw_Open', 'used_in']


def sid(tag, combo, level):
    return '%s_%s_%03d' % (tag, combo, round(level * 100))


def main():
    rows = []
    for tag, avatar in AVATARS:
        for combo, label, eye, lip, use in COMBOS:
            for lv in LEVELS:
                used = []
                if use and lv in MAIN:
                    used.append('main')
                if use and lv in RANGE:
                    used.append('range')
                for k, items in PRACTICE.items():
                    if (tag, combo, lv) in items:
                        used.append('practice_' + k)
                if not used and not use and (lv in MAIN or lv in RANGE):
                    used.append('unused')            # 書き出し済みだが今は使わない
                if not used:
                    continue
                v = '%.2f' % lv
                rows.append({
                    'stimulus_id': sid(tag, combo, lv), 'avatar': avatar, 'combo': combo,
                    'combo_label': label, 'level': v,
                    'Mouth_Smile_Left': v, 'Mouth_Smile_Right': v,
                    'Eye_Left_Squeeze': v if eye else '0', 'Eye_Right_Squeeze': v if eye else '0',
                    'Mouth_Upper_UpLeft': v if lip else '0', 'Mouth_Upper_UpRight': v if lip else '0',
                    'Jaw_Open': '0', 'used_in': ';'.join(used),
                })
    with io.open(OUT, 'w', encoding='utf-8', newline='') as f:
        w = csv.DictWriter(f, fieldnames=COLS)
        w.writeheader()
        w.writerows(rows)
    n_main = sum('main' in r['used_in'].split(';') for r in rows)
    n_range = sum('range' in r['used_in'].split(';') for r in rows)
    n_unused = sum(r['used_in'] == 'unused' for r in rows)
    print('%s: %d 枚（本番 %d、範囲確認 %d、今は使わない %d。練習を含む全体）' % (OUT, len(rows), n_main, n_range, n_unused))


if __name__ == '__main__':
    main()
