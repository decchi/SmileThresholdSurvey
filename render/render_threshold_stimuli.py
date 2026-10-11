# -*- coding: utf-8 -*-
"""違和感の閾値の実験（恒常法）の刺激画像を書き出す

    python render_threshold_stimuli.py <stimuli.csv> <出力先>
    python render_threshold_stimuli.py <stimuli.csv> <出力先> --only M_smile_050,F_both_100
    python render_threshold_stimuli.py <stimuli.csv> <出力先> --sheet   # 一覧だけ作り直す

このファイルは、Box の「260903_静止画による笑顔評価/render/」に置いて実行する。
同じフォルダの render_stimuli.py（HTTP 配信・CDP・モーフ名の読み出し・COLMAP・CAMERA）と
render.html、../male.vrm と ../female.vrm をそのまま使う。構図・照明・画素数は実験1の刺激と同じ。

例（リポジトリを C:\\dev\\SmileThresholdSurvey に置いた場合）
    cd "<Box>\\ミミックでラポール形成\\03_予備実験\\260903_静止画による笑顔評価\\render"
    python render_threshold_stimuli.py C:\\dev\\SmileThresholdSurvey\\stimuli\\stimuli.csv C:\\dev\\SmileThresholdSurvey\\docs\\images

出力
    <出力先>/<stimulus_id>.png                 1枚ずつ（可逆圧縮。非可逆圧縮はかけない）
    render/閾値刺激一覧_M.png, 閾値刺激一覧_F.png  確認用の一覧（行＝4通り、列＝強さ）
"""
import sys, os, io, csv, json, time, base64, urllib.request

import render_stimuli as rs

COMBO_ORDER = ['smile', 'eye', 'lip', 'both']
COMBO_LABEL = {'smile': '口角だけ', 'eye': '＋目', 'lip': '＋上唇', 'both': '＋両方'}


def usage():
    sys.exit(__doc__)


def read_rows(path):
    rows = list(csv.DictReader(io.open(path, encoding='utf-8')))
    need = ['stimulus_id', 'avatar', 'combo', 'level'] + list(rs.COLMAP)
    miss = [c for c in need if c not in rows[0]]
    if miss:
        sys.exit('刺激一覧に列が無い: %s' % ', '.join(miss))
    return rows


def shapes_of(r):
    """CSV の列名（設計書の表記）の値を VRM のモーフ名に直す。0 は渡さない。"""
    out = {}
    for col, morph in rs.COLMAP.items():
        v = float(r[col])
        if v:
            out[morph] = v
    return out


# ---------------------------------------------------------------- CDP
def open_tab(ns):
    """render.html を新しいタブで開き、そのタブに接続する（render_slide_faces.py と同じ方式）。"""
    t = json.loads(urllib.request.urlopen(urllib.request.Request(
        "http://127.0.0.1:%d/json/new?http://127.0.0.1:%d/render.html%%3Fv=%d" % (
            ns['PORT'], rs.PORT, int(time.time())),
        method="PUT"), timeout=15).read().decode())
    time.sleep(2.5)
    ws = ns['WS'](t['webSocketDebuggerUrl'])
    mid = [0]

    def ev(expr):
        mid[0] += 1
        ws.send(json.dumps({"id": mid[0], "method": "Runtime.evaluate",
                            "params": {"expression": expr, "returnByValue": True, "awaitPromise": True}}))
        while True:
            msg = json.loads(ws.recv())
            if msg.get("id") != mid[0]:
                continue
            r = msg.get("result", {})
            if "exceptionDetails" in r:
                raise RuntimeError(json.dumps(r["exceptionDetails"], ensure_ascii=False)[:800])
            return r.get("result", {}).get("value")

    def close():
        ws.close()
        try:
            urllib.request.urlopen("http://127.0.0.1:%d/json/close/%s" % (ns['PORT'], t['id']), timeout=10).read()
        except Exception:
            pass

    return ev, close


# ---------------------------------------------------------------- 一覧
def contact_sheet(rows, out_dir, avatar, path):
    from PIL import Image, ImageDraw, ImageFont
    sel = [r for r in rows if r['avatar'] == avatar]
    levels = sorted({float(r['level']) for r in sel})
    by = {(r['combo'], float(r['level'])): r['stimulus_id'] for r in sel}
    try:
        font = ImageFont.truetype("C:/Windows/Fonts/meiryo.ttc", 18)
    except Exception:
        font = ImageFont.load_default()
    CW, CH, PAD, LAB, LEFT = 160, 200, 8, 28, 90
    sheet = Image.new('RGB', (LEFT + len(levels) * (CW + PAD) + PAD,
                              PAD + len(COMBO_ORDER) * (CH + PAD) + LAB), (250, 250, 248))
    d = ImageDraw.Draw(sheet)
    for i, combo in enumerate(COMBO_ORDER):
        y = PAD + i * (CH + PAD)
        d.text((PAD, y + CH // 2 - 10), COMBO_LABEL[combo], fill=(30, 30, 28), font=font)
        for j, lv in enumerate(levels):
            sid = by.get((combo, lv))
            f = os.path.join(out_dir, sid + '.png') if sid else None
            if not f or not os.path.exists(f):
                continue
            im = Image.open(f).convert('RGB')
            sheet.paste(im.resize((CW, CH), Image.LANCZOS), (LEFT + j * (CW + PAD), y))
    y = PAD + len(COMBO_ORDER) * (CH + PAD)
    for j, lv in enumerate(levels):
        d.text((LEFT + j * (CW + PAD) + 2, y + 2), '%.2f' % lv, fill=(30, 30, 28), font=font)
    sheet.save(path)
    return sheet.size


# ---------------------------------------------------------------- 本体
def main():
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    if len(args) != 2:
        usage()
    csv_path, out_dir = [os.path.abspath(a) for a in args]
    rows = read_rows(csv_path)
    only = None
    for a in sys.argv[1:]:
        if a.startswith('--only'):
            only = set(a.split('=', 1)[1].split(',')) if '=' in a else None
    if only:
        rows_to_render = [r for r in rows if r['stimulus_id'] in only]
    else:
        rows_to_render = rows
    print('刺激一覧: %d 件（書き出し %d 件） -> %s' % (len(rows), len(rows_to_render), out_dir))

    if '--sheet' not in sys.argv:
        from PIL import Image
        for p in rs.VRM_PATHS.values():
            if not os.path.exists(p):
                sys.exit('VRM が無い: %s' % p)
        if not os.path.isdir(out_dir):
            os.makedirs(out_dir)
        ns = rs.load_cdp()
        httpd = rs.serve()
        try:
            if not ns['is_running']():
                ns['start']()
                time.sleep(1.5)
            ev, close = open_tab(ns)
            try:
                for avatar in ['male', 'female']:
                    todo = [r for r in rows_to_render if r['avatar'] == avatar]
                    if not todo:
                        continue
                    info = ev("LOAD('/vrm/%s?t=%d')" % (avatar, time.time()))
                    if not info or info.get('error'):
                        raise RuntimeError('読み込み失敗 %s: %s' % (avatar, info))
                    if not ev("SET_NAMES(%s)" % json.dumps(rs.target_names(rs.VRM_PATHS[avatar]))):
                        raise RuntimeError('%s: モーフ辞書を差し替えられなかった' % avatar)
                    ev("AUTOFRAME(%f,%f,%f)" % (rs.CAMERA['vfov'], rs.CAMERA['topMargin'], rs.CAMERA['spanMult']))
                    for r in todo:
                        shapes = shapes_of(r)
                        url = ev("RENDER(%s)" % json.dumps(shapes))
                        im = Image.open(io.BytesIO(base64.b64decode(url.split(',')[1]))).convert('RGB')
                        im.save(os.path.join(out_dir, r['stimulus_id'] + '.png'), optimize=True, compress_level=9)
                        print('  %-14s %s' % (r['stimulus_id'],
                                              ', '.join('%s=%.2f' % kv for kv in sorted(shapes.items())) or '(中立)'))
            finally:
                close()
        finally:
            httpd.shutdown()

    for avatar, tag in [('male', 'M'), ('female', 'F')]:
        path = os.path.join(rs.HERE, '閾値刺激一覧_%s.png' % tag)
        print('一覧: %s %s' % (path, contact_sheet(rows, out_dir, avatar, path)))


if __name__ == '__main__':
    main()
