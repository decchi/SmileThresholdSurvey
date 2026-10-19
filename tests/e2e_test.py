# -*- coding: utf-8 -*-
"""回答画面の通しテスト（ヘッドレス Chromium で参加者を模擬する）

    pip install playwright && playwright install chromium   # 初回のみ
    python tests/e2e_test.py              # 全シナリオ
    python tests/e2e_test.py --keep out/  # 模擬回答（JSON・CSV）と画面写真を out/ に残す

docs/ を一時フォルダに複製し、config.test.js（保存先＝模擬サーバ、待ち時間の短縮）を重ねて動かす。
本物の Apps Script と同じく、POST には 302 で別URLへ転送して JSON を返す模擬サーバを使う。

確かめること
  本番      練習2・本試行84（3通り×7段階×2体×2回）・確認問題2。各画像がブロックごとに1回ずつ、連続しない。
            確認問題は窓の範囲。説明に回答数（全部で88問）が出る。回答は「不自然」=1・「自然」=0
  範囲確認  ?config=range で本試行60・1ブロック（説明は全部で64問）
  送信失敗  保存先が応答しないとき、CSV の保存画面に切り替わり、CSV の中身が揃っている
  画像なし  代替表示になり image_loaded=0 が記録される
  端末      スマートフォンの UA では先に進めない
  画面      1366×657 で顔と回答ボタンが画面内に収まる
  大きさの確認  顔が収まらないウィンドウでは確認画面が出る。最大化して確認し直すと進め、
                「このまま進む」でも最後まで答えられる（縮めた表示が shrunk に記録される）
"""
import csv, io, json, math, os, random, shutil, sys, tempfile, threading, time
import http.server, socketserver, functools, urllib.parse

from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS = os.path.join(ROOT, 'docs')
STIM = os.path.join(ROOT, 'stimuli', 'stimuli.csv')
KEEP = None
if '--keep' in sys.argv:
    KEEP = os.path.abspath(sys.argv[sys.argv.index('--keep') + 1])
    os.makedirs(KEEP, exist_ok=True)

# 模擬参加者：組み合わせごとの「不自然」の確率（ロジスティック）。閾値（50%点）と傾き
TRUE = {'smile': (0.85, 18.0), 'eye': (0.75, 18.0), 'both': (0.60, 18.0)}
YES = '不自然'          # config.js の question.yesLabel（記録は response = 1）
LAST = {}             # 直近の参加者で見えた説明文など


def p_yes(combo, level):
    th, k = TRUE[combo]
    return 1.0 / (1.0 + math.exp(-k * (level - th)))


# ---------------------------------------------------------------- 模擬 Apps Script
class GAS(object):
    def __init__(self):
        self.payloads = []
        self.mode = 'ok'           # ok / fail
        self.lock = threading.Lock()

    def handler(self):
        gas = self

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _json(self, code, obj):
                b = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.send_header('Content-Length', str(len(b)))
                self.end_headers()
                self.wfile.write(b)

            def do_POST(self):
                n = int(self.headers.get('Content-Length', 0))
                body = self.rfile.read(n).decode('utf-8')
                if gas.mode == 'fail':
                    self.send_response(500)
                    self.end_headers()
                    return
                d = json.loads(body)
                with gas.lock:
                    dup = any(p['session']['session_id'] == d['session']['session_id'] for p in gas.payloads)
                    if not dup:
                        gas.payloads.append(d)
                # Apps Script と同じく 302 で別のURLへ（そこで JSON と CORS ヘッダを返す）
                self.send_response(302)
                self.send_header('Access-Control-Allow-Origin', '*')
                self.send_header('Location', '/echo?sid=' + urllib.parse.quote(d['session']['session_id']) + '&n=%d' % len(d['trials']))
                self.send_header('Content-Length', '0')
                self.end_headers()

            def do_GET(self):
                u = urllib.parse.urlparse(self.path)
                q = urllib.parse.parse_qs(u.query)
                if u.path == '/echo':
                    return self._json(200, {'ok': True, 'session_id': q['sid'][0], 'n': int(q['n'][0])})
                if 'check' in q:
                    found = any(p['session']['session_id'] == q['check'][0] for p in gas.payloads)
                    return self._json(200, {'ok': True, 'found': found})
                return self._json(200, {'ok': True, 'service': 'mock'})
        return H


class Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


def serve(handler, port):
    socketserver.ThreadingTCPServer.allow_reuse_address = True
    httpd = socketserver.ThreadingTCPServer(('127.0.0.1', port), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


def make_site(images):
    tmp = tempfile.mkdtemp(prefix='sts_')
    site = os.path.join(tmp, 'docs')
    shutil.copytree(DOCS, site)
    with open(os.path.join(site, 'config.test.js'), 'w', encoding='utf-8') as f:
        f.write('window.SURVEY_CONFIG_OVERLAY = { submitUrl: "http://127.0.0.1:8766/exec",'
                ' timing: { itiMs: 20, fixationMs: 20, minViewMs: 20 } };\n')
    with open(os.path.join(site, 'config.testrange.js'), 'w', encoding='utf-8') as f:
        src = open(os.path.join(DOCS, 'config.range.js'), encoding='utf-8').read()
        f.write(src + '\nwindow.SURVEY_CONFIG_OVERLAY.submitUrl = "http://127.0.0.1:8766/exec";'
                '\nwindow.SURVEY_CONFIG_OVERLAY.timing = { itiMs: 20, fixationMs: 20, minViewMs: 20 };\n')
    for f in os.listdir(os.path.join(site, 'images')):   # 本物の刺激は使わない（代用画像か、画像なし）
        if f.endswith('.png'):
            os.remove(os.path.join(site, 'images', f))
    if images:
        from PIL import Image, ImageDraw
        for r in csv.DictReader(io.open(STIM, encoding='utf-8')):
            im = Image.new('RGB', (200, 250), (235, 233, 228))   # 1000×1250 と同じ縦横比の小さな代用画像
            d = ImageDraw.Draw(im)
            d.ellipse((40, 40, 160, 200), outline=(60, 60, 60), width=3)
            d.text((10, 220), r['stimulus_id'], fill=(30, 30, 30))
            im.save(os.path.join(site, 'images', r['stimulus_id'] + '.png'))
    return tmp, site


def run_participant(page, base, query, rng, viewport=None, shot=None, fit=None):
    """fit：画面の大きさの確認が出たときの対応（None = 出ないはず、'proceed' = このまま進む）"""
    page.goto(base + 'index.html?' + query)
    page.wait_for_selector('text=調査へのご協力のお願い')
    if page.locator('input[type=text]').count():
        page.fill('input[type=text]', 'worker_%04d' % rng.randint(0, 9999))
    page.check('input[type=checkbox]')
    page.click('text=次へ')
    page.wait_for_selector('text=画面の大きさの調整')
    page.click('text=大きさを合わせた')
    page.wait_for_function("() => /練習を始める|画面の大きさの確認/.test(document.body.innerText)")
    warned = page.locator('h1:has-text("画面の大きさの確認")').count() > 0
    LAST['fit_warned'] = warned
    assert warned == (fit is not None), ('fit check', warned, fit)
    if warned:
        page.click('button:has-text("このまま進む")')
    page.wait_for_selector('text=練習を始める')
    LAST['instructions'] = page.inner_text('.card')
    page.wait_for_function("() => { const b=[...document.querySelectorAll('button')].find(x=>x.textContent==='練習を始める'); return b && !b.disabled; }", timeout=60000)
    page.click('text=練習を始める')
    shot_done = False
    n = 0
    while True:
        # 回答ボタンが押せるようになるか、試行画面が終わるまで待つ
        page.wait_for_function("() => !document.querySelector('.trial') || !document.querySelector('.choice').disabled || !document.querySelector('.trial').isConnected", timeout=30000)
        if not page.locator('.trial').count():
            if page.locator('text=本番を始める').count():
                page.click('text=本番を始める')
                continue
            if page.locator('text=休憩').count() and page.locator('button:has-text("続ける")').count():
                page.click('button:has-text("続ける")')
                continue
            break
        cur = page.evaluate('() => window.__SURVEY__.current')
        if shot and not shot_done and cur['kind'] == 'main':
            page.screenshot(path=shot)
            shot_done = True
        left_label = page.locator('.choice').nth(0).inner_text().split('\n')[0]
        if cur['kind'] == 'attention':
            want = cur['correct']
        else:
            parts = cur['id'].split('_')
            want = 1 if rng.random() < p_yes(parts[1], int(parts[2]) / 100.0) else 0
        yes_left = left_label.startswith(YES)
        key = 'f' if (want == 1) == yes_left else 'j'
        if rng.random() < 0.5:
            page.keyboard.press(key)
        else:
            page.locator('.choice').nth(0 if key == 'f' else 1).click()
        n += 1
        page.wait_for_function("(k) => !window.__SURVEY__.current || window.__SURVEY__.trials.length >= k", arg=n, timeout=30000)
    page.wait_for_selector('text=最後に、あなたご自身について')
    page.fill('input[type=number]', str(rng.randint(20, 60)))
    for name, opt in [('sex', rng.choice(['男性', '女性'])), ('vr_experience', 'ない')]:
        page.check('input[name=%s][value="%s"]' % (name, opt))
    page.fill('textarea', '=HYPERLINK("x") テスト')
    page.click('text=回答を送信する')
    page.wait_for_selector('text=ご協力ありがとうございました', timeout=60000)
    return n


def check_payload(p, n_main, n_blocks, levels, n_att=2):
    s, tr = p['session'], p['trials']
    main = [t for t in tr if t['phase'] == 'main']
    att = [t for t in tr if t['phase'] == 'attention']
    prac = [t for t in tr if t['phase'] == 'practice']
    assert len(prac) == 2, len(prac)
    assert len(main) == n_main, len(main)
    assert len(att) == n_att, len(att)
    ids = sorted({t['stimulus_id'] for t in main})
    assert len(ids) == n_main // n_blocks
    for b in range(1, n_blocks + 1):
        bl = [t['stimulus_id'] for t in tr if t['block'] == b and t['phase'] == 'main']
        assert sorted(bl) == ids, 'block %d does not contain every image once' % b
    seq = [t['stimulus_id'] for t in tr if t['phase'] == 'main']
    assert all(a != b for a, b in zip(seq, seq[1:])), 'same image twice in a row'
    assert sorted({t['level'] for t in main}) == sorted(levels), sorted({t['level'] for t in main})
    for t in att:
        blen = len([x for x in tr if x['block'] == t['block'] and x['phase'] != 'practice'])
        frac = (t['block_trial'] - 1) / float(blen)
        assert 0.15 <= frac <= 0.85, ('attention position', frac)
    assert s['attention_pass'] == 1 and s['attention_correct'] == n_att
    assert s['n_trials_main'] == n_main
    assert [t['trial_index'] for t in tr] == list(range(len(tr)))
    assert all(isinstance(t['rt_ms'], int) and t['rt_ms'] >= 0 for t in tr)
    assert s['completion_code'].startswith(('SMILET-', 'SMILER-'))
    for t in main:
        lv = t['level']
        assert abs(t['smile'] - lv) < 1e-9
        assert abs(t['eye'] - (lv if t['combo'] in ('eye', 'both') else 0)) < 1e-9
        assert abs(t['lip'] - (lv if t['combo'] in ('lip', 'both') else 0)) < 1e-9
    return s


def main():
    gas = GAS()
    g = serve(gas.handler(), 8766)
    results = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch()

        # ---- 本番（代用画像あり）×3人 ----
        tmp, site = make_site(images=True)
        web = serve(functools.partial(Quiet, directory=site), 8765)
        base = 'http://127.0.0.1:8765/'
        levels = [0.25, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]
        for k in range(3):
            ctx = browser.new_context(viewport={'width': 1366, 'height': 657}, accept_downloads=True)
            page = ctx.new_page()
            errors = []
            page.on('pageerror', lambda e: errors.append(str(e)))
            shot = os.path.join(KEEP, 'screen_1366x657.png') if (KEEP and k == 0) else None
            rng = random.Random(100 + k)
            n = run_participant(page, base, 'config=test&debug&seed=%d' % (11 + k), rng, shot=shot)
            assert not errors, errors
            assert page.locator('.code').inner_text().startswith('SMILET-')
            p = gas.payloads[-1]
            s = check_payload(p, 84, 2, levels)
            assert s['images_loaded'] == 1, s['images_loaded']
            assert '回答は全部で88問' in LAST['instructions'] and '本番を86問（前半43問、休憩、後半43問）' in LAST['instructions'], LAST['instructions']
            assert p['session']['experiment_id'] == 'thr2'
            assert {t['response_label'] for t in p['trials'] if t['phase'] == 'main'} <= {'不自然', '自然'}
            assert all((t['response'] == 1) == (t['response_label'] == '不自然') for t in p['trials'])
            assert sorted({t['combo'] for t in p['trials'] if t['phase'] == 'main'}) == ['both', 'eye', 'smile']
            assert s['n_shrunk'] == 0 or s['display_mm_min'] > 0
            assert p['trials'][-1]['response'] in (0, 1)
            results.append('本番 %d: %d 回答（3通り）、完了コード %s、表示幅 %.1f〜%.1f mm、縮小 %d 回、ボタン順 %s' % (
                k + 1, n, s['completion_code'], s['display_mm_min'], s['display_mm_max'], s['n_shrunk'], s['button_order']))
            ctx.close()
        # 同じ乱数の種なら同じ提示順になる
        assert gas.payloads[0]['session']['seed'] == 11

        # 画面の収まり：1366×657 で顔とボタンが画面内か
        ctx = browser.new_context(viewport={'width': 1366, 'height': 657})
        page = ctx.new_page()
        page.goto(base + 'index.html?config=test&debug&pid=fit01')
        page.check('input[type=checkbox]'); page.click('text=次へ')
        page.click('text=カードが手元にない場合はこちら')
        page.wait_for_function("() => { const b=[...document.querySelectorAll('button')].find(x=>x.textContent==='練習を始める'); return b && !b.disabled; }")
        page.click('text=練習を始める')
        page.wait_for_function("() => document.querySelector('.choice') && !document.querySelector('.choice').disabled")
        box = page.locator('.imgbox').bounding_box()
        btn = page.locator('.choice').nth(1).bounding_box()
        assert box['y'] >= 0 and box['y'] + box['height'] <= 657, box
        assert btn['x'] + btn['width'] <= 1366 and btn['y'] + btn['height'] <= 657, btn
        results.append('1366×657：顔 %dx%d px（既定の較正で %.0f mm 幅）、ボタンも画面内' % (box['width'], box['height'], box['width'] / (96 / 25.4)))
        ctx.close()

        # ---- 画面の大きさの確認 ----
        # (1) 顔が収まらないウィンドウ → 確認画面。まだ小さいまま確認し直すと注意、最大化してから確認し直すと進める
        ctx = browser.new_context(viewport={'width': 1100, 'height': 600}, screen={'width': 1366, 'height': 768})
        page = ctx.new_page()
        page.goto(base + 'index.html?config=test&debug&pid=fit02')
        page.check('input[type=checkbox]'); page.click('text=次へ')
        page.wait_for_selector('text=画面の大きさの調整')
        # 14型ノートPC相当の較正（4.42 px/mm、カード幅 378 px）→ 顔 442×552 px は高さ 600 に収まらない
        page.evaluate("() => { const s = document.querySelector('input[type=range]'); s.value = '378'; s.dispatchEvent(new Event('input')); }")
        page.click('text=大きさを合わせた')
        page.wait_for_selector('h1:has-text("画面の大きさの確認")')
        assert '13インチ以上' in page.inner_text('.card') and '幅 100 mm' in page.inner_text('.card')
        page.click('button:has-text("もう一度確認する")')
        page.wait_for_selector('text=まだ収まりません')
        page.set_viewport_size({'width': 1366, 'height': 768})
        page.click('button:has-text("もう一度確認する")')
        page.wait_for_selector('text=練習を始める')
        ctx.close()
        # (2) 「このまま進む」でも最後まで答えられ、縮めた表示が記録される（短縮版）
        # 画面（screen）はノートPC、ウィンドウだけが小さい状態（screen も小さいとスマートフォン扱いで断られる）
        ctx = browser.new_context(viewport={'width': 1000, 'height': 500}, screen={'width': 1366, 'height': 768})
        page = ctx.new_page()
        run_participant(page, base, 'config=test&debug&demo&pid=fit03', random.Random(9), fit='proceed')
        p = gas.payloads[-1]
        assert p['session']['n_shrunk'] > 0 and p['session']['display_mm_max'] < 100, (p['session']['n_shrunk'], p['session']['display_mm_max'])
        assert '回答は全部で8問' in LAST['instructions'] and '本番を7問お答え' in LAST['instructions'], LAST['instructions']
        results.append('画面の大きさ: 収まらないと確認画面、最大化して確認し直すと進む。「このまま進む」では顔を %.0f mm に縮めて記録' % p['session']['display_mm_max'])
        ctx.close()

        # ---- 範囲確認 ----
        ctx = browser.new_context(viewport={'width': 1440, 'height': 900})
        page = ctx.new_page()
        n = run_participant(page, base, 'config=testrange&debug&pid=lab01', random.Random(7))
        p = gas.payloads[-1]
        s = check_payload(p, 60, 1, [round(0.1 * i, 1) for i in range(1, 11)])
        assert p['session']['experiment_id'] == 'range2' and s['completion_code'].startswith('SMILER-')
        assert '回答は全部で64問' in LAST['instructions'] and '本番を62問お答え' in LAST['instructions'], LAST['instructions']
        results.append('範囲確認: %d 回答（本試行 60・確認問題 2・練習 2）、説明に「全部で64問」' % n)
        ctx.close()

        # ---- 送信失敗 → CSV ----
        gas.mode = 'fail'
        ctx = browser.new_context(viewport={'width': 1440, 'height': 900}, accept_downloads=True)
        page = ctx.new_page()
        before = len(gas.payloads)
        run_participant_demo = run_participant(page, base, 'config=test&debug&demo&pid=fail01', random.Random(3))
        page.wait_for_selector('text=回答ファイルを保存する')
        with page.expect_download() as dl:
            page.click('text=回答ファイルを保存する')
        path = dl.value.path()
        text = open(path, encoding='utf-8-sig').read()
        rows = list(csv.DictReader(io.StringIO(text)))
        assert len(rows) == run_participant_demo and rows[0]['participant_id'] == 'fail01'
        assert rows[0]['completion_code'].startswith('SMILET-') and rows[0]['mode'] == 'demo'
        assert len(gas.payloads) == before
        results.append('送信失敗: CSV 保存に切り替わり、%d 行（短縮版）を保存' % len(rows))
        if KEEP:
            shutil.copy(path, os.path.join(KEEP, 'fallback.csv'))
        gas.mode = 'ok'
        ctx.close()
        web.shutdown()
        shutil.rmtree(tmp)

        # ---- 画像なし → 代替表示 ----
        tmp, site = make_site(images=False)
        web = serve(functools.partial(Quiet, directory=site), 8767)
        ctx = browser.new_context(viewport={'width': 1440, 'height': 900})
        page = ctx.new_page()
        run_participant(page, 'http://127.0.0.1:8767/', 'config=test&debug&demo&pid=noimg', random.Random(5))
        p = gas.payloads[-1]
        assert p['session']['images_loaded'] == 0 and all(t['image_loaded'] == 0 for t in p['trials'] if t['phase'] != 'attention')
        results.append('画像なし: 代替表示で最後まで進み、images_loaded=0 を記録')
        ctx.close()

        # ---- スマートフォン ----
        ctx = browser.new_context(**pw.devices['iPhone 13'])
        page = ctx.new_page()
        page.goto('http://127.0.0.1:8767/index.html?pid=sp')
        page.wait_for_selector('text=パソコン（ノートパソコンを含む）でのみ')
        results.append('スマートフォン: 参加を断る画面を表示')
        ctx.close()
        web.shutdown()
        shutil.rmtree(tmp)
        browser.close()
    g.shutdown()

    if KEEP:
        with open(os.path.join(KEEP, 'payloads.json'), 'w', encoding='utf-8') as f:
            json.dump(gas.payloads, f, ensure_ascii=False)
    print('\n'.join('OK  ' + r for r in results))


if __name__ == '__main__':
    main()
