# -*- coding: utf-8 -*-
"""analysis/threshold.py の確認（閾値が分かっている模擬データで回収できるか）

    python tests/analysis_test.py

40名分の模擬回答を、スプレッドシートの CSV（gas/Code.gs と同じ列）・CSV 提出・raw シートの3形式で作り、
分析にかけて次を確かめる。
  - 除外：確認問題の不正解・画像の読み込み失敗・同じIDの2回目・短縮版（demo）が正しく除かれる
  - 50%点・25%点：真の値の近くに推定され、95%信頼区間が真の値を含む
  - 完了コード：参加者IDから計算した値と一致し、食い違いを見つける
"""
import csv, io, json, math, os, random, re, shutil, subprocess, sys, tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'analysis'))
import threshold as th  # noqa: E402

TRUE = {'smile': 0.85, 'eye': 0.75, 'lip': 0.65, 'both': 0.60}   # 50%点
SLOPE = 14.0
LEVELS = [0.25, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]


def gas_cols():
    src = io.open(os.path.join(ROOT, 'gas', 'Code.gs'), encoding='utf-8').read()
    def arr(name):
        body = re.search(r'var %s = \[(.*?)\];' % name, src, re.S).group(1)
        return re.findall(r"'([^']+)'", body)
    return arr('SESSION_COLS'), arr('TRIAL_COLS')


def participant(i, rng, attention_ok=True, images_ok=True, mode='main', pid=None, bad_code=False):
    pid = pid or 'w%03d' % i
    sid = 's%04d-%d' % (i, rng.randint(0, 1 << 30))
    trials = []
    k = 0
    for t, (sp, cmb) in enumerate([('M', 'both'), ('F', 'smile')]):
        trials.append(dict(phase='practice', block=0, stimulus_id='%s_%s_040' % (sp, cmb), response=rng.randint(0, 1)))
    shift = rng.gauss(0, 0.04)   # 参加者ごとのずれ
    for b in (1, 2):
        items = [(a, c, lv) for a in ('male', 'female') for c in TRUE for lv in LEVELS]
        rng.shuffle(items)
        for a, c, lv in items:
            p = 1 / (1 + math.exp(-SLOPE * (lv - TRUE[c] - shift)))
            trials.append(dict(phase='main', block=b, avatar=a, combo=c, level=lv,
                               stimulus_id='%s_%s_%03d' % (a[0].upper(), c, round(lv * 100)),
                               response=1 if rng.random() < p else 0))
        trials.insert(len(trials) - 30, dict(phase='attention', block=b, stimulus_id='att%d' % b,
                                             response=1 if attention_ok else 0, correct=1 if attention_ok else 0))
    for j, t in enumerate(trials):
        t.update(session_id=sid, participant_id=pid, experiment_id='thr1', mode=mode, trial_index=j,
                 rt_ms=rng.randint(500, 2500), image_loaded=1 if images_ok else 0, display_mm=100.0)
    att = [t for t in trials if t['phase'] == 'attention']
    code = th.expected_code(pid, 'thr1')
    session = dict(session_id=sid, participant_id=pid, experiment_id='thr1', mode=mode, config_name='default',
                   started_at='2026-10-%02dT10:%02d:00Z' % (12 + i // 50, i % 50), duration_s=600,
                   completion_code=('SMILET-00000000' if bad_code else code), n_trials_main=112,
                   attention_n=len(att), attention_correct=sum(t['correct'] for t in att),
                   attention_pass=1 if attention_ok else 0, images_loaded=1 if images_ok else 0,
                   free_comment='=SUM(1)' if i == 0 else '', age=30, sex='女性')
    return {'session': session, 'trials': trials}


def write_csv(path, rows, cols):
    with io.open(path, 'w', encoding='utf-8-sig', newline='') as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction='ignore')
        w.writeheader()
        w.writerows(rows)


def main():
    rng = random.Random(42)
    P = [participant(i, rng) for i in range(40)]
    P.append(participant(40, rng, attention_ok=False))              # 除外：確認問題
    P.append(participant(41, rng, images_ok=False))                 # 除外：画像
    P.append(participant(42, rng, pid='w005'))                      # 除外：同じIDの2回目（w005 は既出）
    P.append(participant(43, rng, mode='demo'))                     # 対象外：短縮版
    P.append(participant(44, rng, bad_code=True))                   # 分析には入るが、コードが合わない
    scols, tcols = gas_cols()
    tmp = tempfile.mkdtemp(prefix='ana_')
    try:
        # 1) スプレッドシートの CSV として 30 名、2) CSV 提出として 10 名、3) raw シートとして残り
        sheet, fallback, raw = P[:30] + P[40:], P[30:35], P[35:40]
        write_csv(os.path.join(tmp, 'sessions.csv'), [dict(p['session'], received_at='x') for p in sheet], scols)
        write_csv(os.path.join(tmp, 'responses.csv'), [t for p in sheet for t in p['trials']], tcols)
        os.makedirs(os.path.join(tmp, 'fallback'))
        for p in fallback:
            rows = [dict(p['session'], **t) for t in p['trials']]
            write_csv(os.path.join(tmp, 'fallback', p['session']['session_id'] + '.csv'), rows, tcols + [c for c in scols if c not in tcols])
        with io.open(os.path.join(tmp, 'raw.csv'), 'w', encoding='utf-8-sig', newline='') as f:
            w = csv.writer(f)
            w.writerow(['received_at', 'session_id', 'n_chunks', 'payload'])
            for p in raw:
                s = json.dumps(p, ensure_ascii=False)
                chunks = ["'" + s[i:i + 45000] for i in range(0, len(s), 45000)]
                w.writerow(['x', p['session']['session_id'], len(chunks)] + chunks)
        with io.open(os.path.join(tmp, 'platform.csv'), 'w', encoding='utf-8-sig', newline='') as f:
            f.write('participant_id,code\nw001,%s\nw002,SMILET-DEADBEEF\nw999,%s\n' % (
                th.expected_code('w001', 'thr1'), th.expected_code('w999', 'thr1')))
        out = os.path.join(tmp, 'results')
        cmd = [sys.executable, os.path.join(ROOT, 'analysis', 'threshold.py'),
               '--responses', os.path.join(tmp, 'responses.csv'), '--sessions', os.path.join(tmp, 'sessions.csv'),
               '--csv-dir', os.path.join(tmp, 'fallback'), '--raw', os.path.join(tmp, 'raw.csv'),
               '--out', out, '--boot', '400', '--check-codes', os.path.join(tmp, 'platform.csv')]
        r = subprocess.run(cmd, capture_output=True, text=True)
        print(r.stdout)
        assert r.returncode == 0, r.stderr

        import pandas as pd
        parts = pd.read_csv(os.path.join(out, 'participants.csv'), encoding='utf-8-sig')
        assert len(parts) == 44, len(parts)                     # demo は対象外
        assert parts['included'].sum() == 41, parts['included'].sum()   # 40 + コード不一致の1名
        reasons = parts.loc[~parts['included'], 'exclude_reason'].tolist()
        assert any('確認問題' in x for x in reasons) and any('画像' in x for x in reasons) and any('2回目' in x for x in reasons)
        res = pd.read_csv(os.path.join(out, 'thresholds.csv'), encoding='utf-8-sig')
        for c, t in TRUE.items():
            row = res[(res['combo'] == c) & (res['avatar'] == 'all')].iloc[0]
            assert abs(row['x50'] - t) < 0.04, (c, row['x50'], t)
            assert row['x50_lo'] <= t + 0.02 and row['x50_hi'] >= t - 0.02, (c, row['x50_lo'], row['x50_hi'])
            t25 = t + math.log(1 / 3) / SLOPE
            assert abs(row['x25'] - t25) < 0.05, (c, row['x25'], t25)
        cc = pd.read_csv(os.path.join(out, 'code_check.csv'), encoding='utf-8-sig')
        assert cc['code_ok'].tolist() == [True, False, True] and cc['has_response'].tolist() == [True, True, False]
        assert os.path.exists(os.path.join(out, 'psychometric.png')) and os.path.exists(os.path.join(out, 'psychometric_by_avatar.png'))
        if '--keep' in sys.argv:
            dst = os.path.abspath(sys.argv[sys.argv.index('--keep') + 1])
            shutil.copytree(out, dst, dirs_exist_ok=True)
        print('OK  analysis: 除外 3 名・対象外 1 名を正しく扱い、4通りの 50%点・25%点を真の値の近くに回収（信頼区間も真の値を含む）、完了コードの照合も一致')
    finally:
        shutil.rmtree(tmp)


if __name__ == '__main__':
    main()
