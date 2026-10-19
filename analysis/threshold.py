# -*- coding: utf-8 -*-
"""違和感の閾値の算出（恒常法）

    python analysis/threshold.py --responses responses.csv --sessions sessions.csv
    python analysis/threshold.py --csv-dir 提出されたCSVのフォルダ          # 送信できず CSV で提出された分
    python analysis/threshold.py --raw raw.csv                            # raw シートからの復元
    python analysis/threshold.py ... --experiment range2                  # 範囲確認の集計
    python analysis/threshold.py ... --check-codes platform.csv           # 完了コードの照合

入力は Google スプレッドシートの各シートを「ファイル > ダウンロード > CSV」で保存したもの。
複数の入力を同時に渡すと、session_id の重複を除いてまとめる。

手順
  1. 対象の実験（experiment_id。既定は本番の thr2）と mode = main の回答だけを使う（短縮版 demo は除く）
     ※ thr1・range は、質問と見せる顔を変える前（2026-10-20 より前）の版
  2. 除外：確認問題の不正解（データを見る前に決めた基準）、画像の読み込み失敗、同じ参加者IDの2回目以降
     ※ 反応の速すぎ・全試行同じ答えなどは participants.csv に印を付けるだけで、除外はしない
  3. 見せた組み合わせ（今は口角だけ・＋目・＋両方の3通り）ごとに、強さ別の回答 1 の割合を求める
     （回答 1 は「不自然」。旧版では「違和感あり」。2体をまとめた値と、体ごとの値）
  4. ロジスティック関数（S字の曲線）を最尤法で当てはめ、50%点と25%点を求める
  5. 参加者を単位にしたブートストラップ（既定 2000 回）で 95% 信頼区間を付ける

出力（--out、既定 results/）
  thresholds.csv     組み合わせ × {全体, 男性, 女性} の 50%点・25%点と信頼区間
  proportions.csv    強さ別の回答数・回答 1（不自然）の数・割合
  participants.csv   参加者ごとの確認問題・除外の理由・印・完了コードの照合
  psychometric.png   組み合わせごとの曲線（2体をまとめた値）
  psychometric_by_avatar.png  体ごとの曲線
  summary.txt        結果の要約
"""
import argparse
import glob
import io
import json
import math
import os
import sys

import numpy as np
import pandas as pd

COMBOS_ALL = [('smile', '口角だけ'), ('eye', '＋目'), ('lip', '＋上唇'), ('both', '＋両方')]   # 並べる順と表示名
AVATARS = [('all', '全体'), ('male', '男性'), ('female', '女性')]
CODE_PREFIX = {'thr2': 'SMILET-', 'range2': 'SMILER-', 'thr1': 'SMILET-', 'range': 'SMILER-'}
CODE_SALT = '261011'
LOGIT25 = math.log(0.25 / 0.75)


# ---------------------------------------------------------------- 読み込み
def read_csv(path):
    return pd.read_csv(path, encoding='utf-8-sig', dtype={'participant_id': str, 'session_id': str})


def from_payloads(payloads):
    sessions = pd.DataFrame([p['session'] for p in payloads])
    trials = pd.DataFrame([t for p in payloads for t in p['trials']])
    return trials, sessions


def from_raw(path):
    raw = read_csv(path)
    payloads = []
    for _, r in raw.iterrows():
        n = int(r['n_chunks'])
        parts = [str(x) for x in r.iloc[3:3 + n]]
        payloads.append(json.loads(''.join(p[1:] if p.startswith("'") else p for p in parts)))
    return from_payloads(payloads)


SESSION_ONLY = ['config_name', 'version', 'seed', 'button_order', 'started_at', 'finished_at', 'duration_s',
                'completion_code', 'n_trials_main', 'n_practice', 'attention_n', 'attention_correct',
                'attention_pass', 'images_loaded', 'n_images_failed', 'calibration_method', 'px_per_mm',
                'stimulus_width_mm', 'display_mm_min', 'display_mm_max', 'n_shrunk', 'break_s', 'window_w',
                'window_h', 'screen_w', 'screen_h', 'dpr', 'user_agent', 'language', 'age', 'sex',
                'vr_experience', 'free_comment', 'submit_attempts']


def from_fallback_dir(d):
    trials, sessions = [], []
    for f in sorted(glob.glob(os.path.join(d, '*.csv'))):
        df = read_csv(f)
        if 'phase' not in df.columns or 'session_id' not in df.columns:
            print('  読み飛ばし（回答ファイルではない）: %s' % f)
            continue
        cols = ['session_id', 'participant_id', 'experiment_id', 'mode'] + [c for c in SESSION_ONLY if c in df.columns]
        sessions.append(df[cols].drop_duplicates('session_id'))
        trials.append(df[[c for c in df.columns if c not in SESSION_ONLY]])
    if not trials:
        return pd.DataFrame(), pd.DataFrame()
    return pd.concat(trials, ignore_index=True), pd.concat(sessions, ignore_index=True)


def load(args):
    T, S = [], []
    if args.responses or args.sessions:
        if not (args.responses and args.sessions):
            sys.exit('--responses と --sessions は両方指定すること')
        T.append(read_csv(args.responses))
        S.append(read_csv(args.sessions))
    if args.csv_dir:
        t, s = from_fallback_dir(args.csv_dir)
        T.append(t); S.append(s)
    if args.raw:
        t, s = from_raw(args.raw)
        T.append(t); S.append(s)
    if args.json:
        t, s = from_payloads(json.load(io.open(args.json, encoding='utf-8')))
        T.append(t); S.append(s)
    if not T:
        sys.exit('入力が無い。--responses/--sessions、--csv-dir、--raw、--json のどれかを指定すること')
    sessions = pd.concat(S, ignore_index=True).drop_duplicates('session_id')
    trials = pd.concat(T, ignore_index=True).drop_duplicates(['session_id', 'trial_index'])
    for c in ['response', 'level', 'rt_ms', 'block', 'trial_index', 'correct', 'image_loaded']:
        if c in trials.columns:
            trials[c] = pd.to_numeric(trials[c], errors='coerce')
    for c in ['attention_pass', 'images_loaded', 'n_trials_main', 'attention_correct', 'attention_n', 'duration_s']:
        if c in sessions.columns:
            sessions[c] = pd.to_numeric(sessions[c], errors='coerce')
    return trials, sessions


# ---------------------------------------------------------------- 完了コード
def fnv1a(s):
    h = 0x811c9dc5
    for b in s.encode('utf-8'):
        h ^= b
        h = (h * 0x01000193) & 0xffffffff
    return '%08x' % h


def expected_code(pid, experiment):
    return CODE_PREFIX.get(experiment, '') + fnv1a('%s|%s' % (pid, CODE_SALT)).upper()


# ---------------------------------------------------------------- 当てはめ
def fit_logistic(x, k, n, ridge=1e-4, center=0.5):
    """2母数ロジスティック p = 1 / (1 + exp(-(a + b (x - center)))) を最尤法（Newton 法）で当てはめる。
    完全分離でも発散しないよう、ごく弱い L2 罰則を付ける。返り値 (a, b, 収束したか)"""
    x = np.asarray(x, float) - center
    k = np.asarray(k, float)
    n = np.asarray(n, float)
    X = np.column_stack([np.ones_like(x), x])
    beta = np.zeros(2)
    for _ in range(200):
        p = 1.0 / (1.0 + np.exp(-(X @ beta)))
        w = n * p * (1 - p)
        grad = X.T @ (k - n * p) - ridge * beta
        H = (X * w[:, None]).T @ X + ridge * np.eye(2)
        try:
            step = np.linalg.solve(H, grad)
        except np.linalg.LinAlgError:
            return beta[0], beta[1], False
        beta = beta + step
        if np.max(np.abs(step)) < 1e-9:
            return beta[0], beta[1], True
    return beta[0], beta[1], False


def thresholds(a, b, center=0.5):
    if not np.isfinite(b) or b <= 0:
        return np.nan, np.nan
    return center - a / b, center + (LOGIT25 - a) / b


def curve(a, b, xs, center=0.5):
    return 1.0 / (1.0 + np.exp(-(a + b * (np.asarray(xs) - center))))


# ---------------------------------------------------------------- 本体
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--responses')
    ap.add_argument('--sessions')
    ap.add_argument('--csv-dir')
    ap.add_argument('--raw')
    ap.add_argument('--json', help='模擬回答（tests/e2e_test.py --keep の payloads.json）')
    ap.add_argument('--experiment', default='thr2')
    ap.add_argument('--out', default='results')
    ap.add_argument('--boot', type=int, default=2000)
    ap.add_argument('--seed', type=int, default=1)
    ap.add_argument('--check-codes', help='募集側の CSV（participant_id, code の2列）')
    ap.add_argument('--no-plot', action='store_true')
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)

    trials, sessions = load(args)
    exp = args.experiment
    sessions = sessions[(sessions['experiment_id'] == exp) & (sessions['mode'] == 'main')].copy()
    trials = trials[(trials['experiment_id'] == exp) & (trials['mode'] == 'main')
                    & trials['session_id'].isin(sessions['session_id'])].copy()
    if sessions.empty:
        sys.exit('experiment_id = %s の本番の回答が無い' % exp)

    # ---- 参加者ごとの表と除外 ----
    main_t = trials[trials['phase'] == 'main']
    per = main_t.groupby('session_id').agg(n_main=('response', 'size'), prop_yes=('response', 'mean'),
                                           median_rt_ms=('rt_ms', 'median'))
    P = sessions.set_index('session_id').join(per, how='left')
    P['started_at'] = P['started_at'].astype(str)
    P = P.sort_values('started_at')
    P['duplicate'] = P.duplicated('participant_id', keep='first')
    expected_n = int(P['n_main'].max())
    reasons = []
    for sid, r in P.iterrows():
        why = []
        if r.get('attention_pass') != 1:
            why.append('確認問題の不正解')
        if r.get('images_loaded') != 1:
            why.append('画像の読み込み失敗')
        if r['duplicate']:
            why.append('同じ参加者IDの2回目以降')
        if r['n_main'] != expected_n:
            why.append('本試行の数が不足')
        reasons.append('・'.join(why))
    P['exclude_reason'] = reasons
    P['included'] = P['exclude_reason'] == ''
    flags = []
    for sid, r in P.iterrows():
        f = []
        if r['median_rt_ms'] < 300:
            f.append('反応が速い（中央値 300 ms 未満）')
        if r['prop_yes'] in (0.0, 1.0):
            f.append('全試行が同じ答え')
        flags.append('・'.join(f))
    P['flags'] = flags
    P['expected_code'] = [expected_code(pid, exp) for pid in P['participant_id']]
    if 'completion_code' in P.columns:
        P['code_ok'] = P['completion_code'] == P['expected_code']
    inc = set(P.index[P['included']])
    d = main_t[main_t['session_id'].isin(inc)].copy()
    n_p = len(inc)
    cols = ['participant_id', 'started_at', 'included', 'exclude_reason', 'flags', 'attention_correct', 'attention_n',
            'images_loaded', 'n_main', 'prop_yes', 'median_rt_ms', 'duration_s', 'completion_code', 'expected_code']
    P.reset_index()[['session_id'] + [c for c in cols if c in P.columns]].to_csv(
        os.path.join(args.out, 'participants.csv'), index=False, encoding='utf-8-sig')
    if not n_p:
        sys.exit('除外の後に残った参加者がいない（participants.csv を確認）')

    # ---- 強さ別の割合 ----
    prop_rows = []
    for av, _ in AVATARS:
        sub = d if av == 'all' else d[d['avatar'] == av]
        g = sub.groupby(['combo', 'level'])['response'].agg(['size', 'sum']).reset_index()
        for _, r in g.iterrows():
            prop_rows.append({'combo': r['combo'], 'avatar': av, 'level': r['level'], 'n': int(r['size']),
                              'k': int(r['sum']), 'p': r['sum'] / r['size']})
    prop = pd.DataFrame(prop_rows)
    prop.to_csv(os.path.join(args.out, 'proportions.csv'), index=False, encoding='utf-8-sig')
    levels = sorted(d['level'].unique())
    lo_lv, hi_lv = min(levels), max(levels)
    # 見せた組み合わせ（データにあるものだけ。並びは COMBOS_ALL の順）と、回答 1 の表示名
    present = set(d['combo'])
    COMBOS = [c for c in COMBOS_ALL if c[0] in present]
    unknown = sorted(present - {c for c, _ in COMBOS_ALL})
    if unknown:
        sys.exit('知らない組み合わせがある: %s' % ', '.join(unknown))
    yes_label = '回答 1'
    if 'response_label' in d.columns:
        lab = d.loc[d['response'] == 1, 'response_label'].dropna().astype(str)
        if len(lab):
            yes_label = lab.value_counts().index[0]

    # ---- 当てはめとブートストラップ ----
    # 参加者 × 体 × 組み合わせ × 強さ の集計表（ブートストラップで足し合わせる）
    cnt = d.groupby(['session_id', 'avatar', 'combo', 'level'])['response'].agg(['size', 'sum']).reset_index()
    sids = sorted(inc)
    idx = {s: i for i, s in enumerate(sids)}
    lv_idx = {lv: j for j, lv in enumerate(levels)}
    av_idx = {'male': 0, 'female': 1}
    co_idx = {c: i for i, (c, _) in enumerate(COMBOS)}
    N = np.zeros((len(sids), 2, len(COMBOS), len(levels)))
    K = np.zeros_like(N)
    for _, r in cnt.iterrows():
        i, a, c, j = idx[r['session_id']], av_idx[r['avatar']], co_idx[r['combo']], lv_idx[r['level']]
        N[i, a, c, j] += r['size']
        K[i, a, c, j] += r['sum']
    x = np.array(levels, float)
    rng = np.random.default_rng(args.seed)
    boot_idx = rng.integers(0, len(sids), size=(args.boot, len(sids)))

    def sums(sel, av):
        n = N[sel].sum(axis=0)
        k = K[sel].sum(axis=0)
        if av == 'all':
            return n.sum(axis=0), k.sum(axis=0)
        a = av_idx[av]
        return n[a], k[a]

    res_rows, fits = [], {}
    all_sel = np.arange(len(sids))
    for av, av_label in AVATARS:
        n_all, k_all = sums(all_sel, av)
        boot = [sums(b, av) for b in boot_idx]
        for c, (combo, label) in enumerate(COMBOS):
            a_, b_, ok = fit_logistic(x, k_all[c], n_all[c])
            t50, t25 = thresholds(a_, b_)
            fits[(av, combo)] = (a_, b_)
            bt = np.array([thresholds(*fit_logistic(x, bk[c], bn[c])[:2]) for bn, bk in boot])
            ci = lambda col: (np.nanpercentile(bt[:, col], 2.5), np.nanpercentile(bt[:, col], 97.5)) if np.isfinite(bt[:, col]).sum() > 10 else (np.nan, np.nan)
            (l50, h50), (l25, h25) = ci(0), ci(1)
            note = []
            if not ok:
                note.append('当てはめが収束せず')
            for name, v in (('50%点', t50), ('25%点', t25)):
                if np.isfinite(v) and (v < lo_lv or v > hi_lv):
                    note.append('%sが提示範囲（%.2f〜%.2f）の外（外挿）' % (name, lo_lv, hi_lv))
            if not np.isfinite(t50):
                note.append('割合が強さとともに増えていない')
            res_rows.append({'combo': combo, 'combo_label': label, 'avatar': av, 'avatar_label': av_label,
                             'n_participants': n_p, 'n_trials': int(n_all[c].sum()),
                             'x50': t50, 'x50_lo': l50, 'x50_hi': h50, 'x25': t25, 'x25_lo': l25, 'x25_hi': h25,
                             'slope': b_, 'note': '・'.join(note)})
    res = pd.DataFrame(res_rows)
    res.round(4).to_csv(os.path.join(args.out, 'thresholds.csv'), index=False, encoding='utf-8-sig')

    # ---- 要約 ----
    lines = ['実験 %s：参加者 %d 名のうち %d 名を分析（除外 %d 名）' % (exp, len(P), n_p, len(P) - n_p),
             '閾値は「%s」の割合から求めた値。組み合わせ：%s' % (yes_label, '・'.join(l for _, l in COMBOS))]
    for reason, k in P.loc[~P['included'], 'exclude_reason'].value_counts().items():
        lines.append('  除外の理由：%s %d 名' % (reason, k))
    flagged = P[P['included'] & (P['flags'] != '')]
    if len(flagged):
        lines.append('  印のみ（除外していない）：%d 名（participants.csv の flags）' % len(flagged))
    if 'code_ok' in P.columns and (~P['code_ok']).any():
        lines.append('  完了コードが参加者IDと合わない回答：%d 件' % int((~P['code_ok']).sum()))
    lines.append('強さ：%s（ブートストラップ %d 回、95%%信頼区間）' % (', '.join('%.2f' % v for v in levels), args.boot))
    for av, av_label in AVATARS:
        lines.append('[%s]' % av_label)
        for _, r in res[res['avatar'] == av].iterrows():
            lines.append('  %-6s 50%%点 %.3f [%.3f, %.3f]   25%%点 %.3f [%.3f, %.3f]%s' % (
                r['combo_label'], r['x50'], r['x50_lo'], r['x50_hi'], r['x25'], r['x25_lo'], r['x25_hi'],
                ('   ※' + r['note']) if r['note'] else ''))
    text = '\n'.join(lines)
    io.open(os.path.join(args.out, 'summary.txt'), 'w', encoding='utf-8').write(text + '\n')
    print(text)

    # ---- 完了コードの照合（募集側の一覧） ----
    if args.check_codes:
        sub = read_csv(args.check_codes)
        sub.columns = [c.strip().lower() for c in sub.columns]
        have = set(P['participant_id'])
        sub['expected_code'] = [expected_code(p, exp) for p in sub['participant_id']]
        sub['code_ok'] = sub['code'].astype(str).str.strip().str.upper() == sub['expected_code']
        sub['has_response'] = sub['participant_id'].isin(have)
        sub.to_csv(os.path.join(args.out, 'code_check.csv'), index=False, encoding='utf-8-sig')
        print('完了コードの照合：%d 件中 一致 %d 件、回答あり %d 件（code_check.csv）' % (
            len(sub), int(sub['code_ok'].sum()), int(sub['has_response'].sum())))

    if not args.no_plot:
        plot(prop, res, fits, levels, n_p, args.out, exp, COMBOS, yes_label)


# ---------------------------------------------------------------- 図
INK, INK2, GRID, SURFACE = '#0b0b0b', '#52514e', '#e4e3de', '#ffffff'
SERIES = {'all': '#2a78d6', 'male': '#2a78d6', 'female': '#eb6834'}
STYLE = {'all': ('o', '-'), 'male': ('o', '-'), 'female': ('s', '--')}


def setup_mpl():
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib import font_manager
    have = {f.name for f in font_manager.fontManager.ttflist}
    for fam in ['Yu Gothic', 'Meiryo', 'Hiragino Sans', 'Noto Sans CJK JP', 'IPAexGothic', 'IPAGothic']:
        if fam in have:
            plt.rcParams['font.family'] = [fam, 'DejaVu Sans']
            break
    plt.rcParams.update({'axes.edgecolor': INK2, 'axes.labelcolor': INK2, 'xtick.color': INK2, 'ytick.color': INK2,
                         'axes.titlecolor': INK, 'figure.facecolor': SURFACE, 'axes.facecolor': SURFACE,
                         'font.size': 10})
    return plt


def plot(prop, res, fits, levels, n_p, out, exp, combos, yes_label):
    plt = setup_mpl()
    xs = np.linspace(0, 1.05, 300)
    nc = len(combos)
    nrow, ncol = (1, nc) if nc <= 3 else (2, 2)
    for which in ['all', 'by_avatar']:
        avs = ['all'] if which == 'all' else ['male', 'female']
        H = 3.9 * nrow + 0.7                                     # 図の高さ（インチ）。見出しの位置はインチで決める
        fig, axes = plt.subplots(nrow, ncol, figsize=(4.4 * ncol, H), sharex=True, sharey=True, squeeze=False)
        for ax in axes.ravel()[nc:]:
            ax.set_visible(False)
        for ax, (combo, label) in zip(axes.ravel(), combos):
            ax.grid(True, color=GRID, linewidth=0.8)
            ax.set_axisbelow(True)
            for y in (0.5, 0.25):
                ax.axhline(y, color=INK2, linewidth=0.8, linestyle=(0, (2, 3)), zorder=1)
            for av in avs:
                p = prop[(prop['avatar'] == av) & (prop['combo'] == combo)].sort_values('level')
                a, b = fits[(av, combo)]
                mk, ls = STYLE[av]
                col = SERIES[av]
                name = {'all': '2体まとめ', 'male': '男性アバタ', 'female': '女性アバタ'}[av]
                ax.plot(xs, curve(a, b, xs), color=col, linewidth=2, linestyle=ls, zorder=3, label=name)
                ax.scatter(p['level'], p['p'], s=36, marker=mk, facecolor=col if av != 'female' else SURFACE,
                           edgecolor=col, linewidth=1.6, zorder=4)
                r = res[(res['avatar'] == av) & (res['combo'] == combo)].iloc[0]
                if which == 'all':
                    for key, y in (('x50', 0.5), ('x25', 0.25)):
                        v, lo, hi = r[key], r[key + '_lo'], r[key + '_hi']
                        if np.isfinite(v):
                            ax.errorbar([v], [y], xerr=[[v - lo], [hi - v]] if np.isfinite(lo) else None,
                                        fmt='|', color=INK, markersize=12, linewidth=1.2, capsize=3, zorder=5)
                            ax.annotate('%s %.2f' % ('50%点' if y == 0.5 else '25%点', v), (v, y),
                                        textcoords='offset points', xytext=(-6, 8), ha='right', fontsize=9, color=INK)
            ax.set_title(label, loc='left', fontsize=12, fontweight='bold')
            ax.set_xlim(0, 1.05)
            ax.set_ylim(-0.03, 1.03)
            ax.set_yticks([0, 0.25, 0.5, 0.75, 1.0])
            ax.set_yticklabels(['0%', '25%', '50%', '75%', '100%'])
            ax.set_xticks([0, 0.25, 0.5, 0.75, 1.0])
            for s in ('top', 'right'):
                ax.spines[s].set_visible(False)
        for ax in axes[-1]:
            ax.set_xlabel('強さ（シェイプの値）')
        for ax in axes[:, 0]:
            ax.set_ylabel('「%s」の割合' % yes_label)
        sub = '2体をまとめた値。縦線は50%点・25%点、横の線は95%信頼区間' if which == 'all' else '体ごとの値（●男性、□女性）'
        fig.suptitle('違和感の閾値（%s、%d 名）' % (exp, n_p), x=0.07, y=1 - 0.1 / H, ha='left', va='top',
                     fontsize=13, fontweight='bold', color=INK)
        fig.text(0.07, 1 - 0.42 / H, sub, ha='left', va='top', fontsize=9.5, color=INK2)
        if which == 'by_avatar':
            h, l = axes[0, 0].get_legend_handles_labels()
            fig.legend(h, l, loc='upper right', bbox_to_anchor=(0.98, 1 - 0.06 / H), frameon=False, ncol=2)
        fig.tight_layout(rect=(0, 0, 1, 1 - 0.58 / H))
        name = 'psychometric.png' if which == 'all' else 'psychometric_by_avatar.png'
        fig.savefig(os.path.join(out, name), dpi=160)
        plt.close(fig)


if __name__ == '__main__':
    main()
