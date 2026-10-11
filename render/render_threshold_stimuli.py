# -*- coding: utf-8 -*-
"""違和感の閾値の実験（恒常法）の刺激画像を書き出す

    python render_threshold_stimuli.py <stimuli.csv> <出力先>
    python render_threshold_stimuli.py <stimuli.csv> <出力先> --only=M_smile_050,F_both_100   # 一部だけ
    python render_threshold_stimuli.py <stimuli.csv> <出力先> --sheet   # 一覧だけ作り直す

このファイルは、Box の「260903_静止画による笑顔評価/render/」に置いて実行する。
同じフォルダの render_stimuli.py（HTTP 配信・CDP・モーフ名の読み出し・COLMAP・CAMERA）と
render.html、../male.vrm と ../female.vrm をそのまま使う。構図・照明・画素数は実験1の刺激と同じ。

例1（リポジトリの中身を 260903_静止画による笑顔評価 に置いた場合）
    cd "<Box>\\ミミックでラポール形成\\03_予備実験\\260903_静止画による笑顔評価\\render"
    python render_threshold_stimuli.py ..\\stimuli\\stimuli.csv ..\\docs\\images

例2（リポジトリを C:\\dev\\SmileThresholdSurvey に置いた場合）
    python render_threshold_stimuli.py C:\\dev\\SmileThresholdSurvey\\stimuli\\stimuli.csv C:\\dev\\SmileThresholdSurvey\\docs\\images

出力
    <出力先>/<stimulus_id>.png                 1枚ずつ（可逆圧縮。非可逆圧縮はかけない）
    render/閾値刺激一覧_M.png, 閾値刺激一覧_F.png  確認用の一覧（行＝4通り、列＝強さ）
"""
import sys, os, io, csv, json, time, base64, socket, struct, shutil, subprocess, tempfile
import urllib.request, urllib.parse

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
# Chrome の操作は paper-research スキルの cdp.py を使う（実験1と同じ）。見つからないときは、
# 下の内蔵の簡易版で Chrome を起動して操作する。環境変数：
#   CDP_PY       cdp.py の場所（既定の場所に無いとき）
#   CDP_CHROME   Chrome（または Edge）の実行ファイル。既定は標準のインストール先を探す
#   CDP_PORT     内蔵版のデバッグ用ポート（既定 9222）
#   CDP_HEADLESS 1 にすると内蔵版はウィンドウを出さずに描画する
#   CDP_CHROME_ARGS 内蔵版で Chrome に足す起動オプション（空白区切り。通常は不要）
def find_cdp_py():
    cands = [os.environ.get('CDP_PY'), rs.CDP]
    for base in [os.path.join(os.path.expanduser('~'), '.claude', d) for d in ('skills', 'plugins')]:
        for root, _dirs, files in os.walk(base):
            if 'cdp.py' in files:
                cands.append(os.path.join(root, 'cdp.py'))
    for c in cands:
        if c and os.path.exists(c):
            return c
    return None


def find_browser():
    cands = [os.environ.get('CDP_CHROME')]
    for env, default in [('PROGRAMFILES', r'C:\Program Files'), ('PROGRAMFILES(X86)', r'C:\Program Files (x86)'),
                         ('LOCALAPPDATA', '')]:
        base = os.environ.get(env, default)
        if base:
            cands.append(os.path.join(base, 'Google', 'Chrome', 'Application', 'chrome.exe'))
    for env, default in [('PROGRAMFILES(X86)', r'C:\Program Files (x86)'), ('PROGRAMFILES', r'C:\Program Files')]:
        cands.append(os.path.join(os.environ.get(env, default), 'Microsoft', 'Edge', 'Application', 'msedge.exe'))
    cands += ['/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
              shutil.which('google-chrome') or '', shutil.which('chromium') or '', shutil.which('chromium-browser') or '']
    for c in cands:
        if c and os.path.exists(c):
            return c
    return None


class MiniWS(object):
    """CDP 用の最小限の WebSocket クライアント（標準ライブラリのみ。テキストフレームの送受信）"""

    def __init__(self, url):
        u = urllib.parse.urlparse(url)
        self.sock = socket.create_connection((u.hostname, u.port or 80), timeout=300)
        key = base64.b64encode(os.urandom(16)).decode()
        req = ('GET %s HTTP/1.1\r\nHost: %s:%d\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n'
               'Sec-WebSocket-Key: %s\r\nSec-WebSocket-Version: 13\r\n\r\n') % (u.path or '/', u.hostname, u.port or 80, key)
        self.sock.sendall(req.encode('ascii'))
        self.buf = bytearray()
        while b'\r\n\r\n' not in self.buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise RuntimeError('Chrome との WebSocket 接続に失敗した')
            self.buf += chunk
        end = self.buf.index(b'\r\n\r\n')
        status = bytes(self.buf[:end]).split(b'\r\n')[0]
        del self.buf[:end + 4]
        if b' 101' not in status:
            raise RuntimeError('Chrome との WebSocket 接続に失敗した: %s' % status.decode('latin-1'))

    def _read(self, n):
        while len(self.buf) < n:
            chunk = self.sock.recv(max(65536, n - len(self.buf)))
            if not chunk:
                raise RuntimeError('Chrome との接続が切れた')
            self.buf += chunk
        out = bytes(self.buf[:n])
        del self.buf[:n]
        return out

    def _frame(self, op, data):
        n = len(data)
        hdr = bytearray([0x80 | op])
        if n < 126:
            hdr.append(0x80 | n)
        elif n < 65536:
            hdr.append(0x80 | 126)
            hdr += struct.pack('>H', n)
        else:
            hdr.append(0x80 | 127)
            hdr += struct.pack('>Q', n)
        mask = os.urandom(4)
        hdr += mask
        body = bytearray(data)
        for i in range(n):
            body[i] ^= mask[i & 3]
        self.sock.sendall(bytes(hdr) + bytes(body))

    def send(self, text):
        self._frame(0x1, text.encode('utf-8'))

    def recv(self):
        msg = bytearray()
        while True:
            b1, b2 = self._read(2)
            fin, op, n = b1 & 0x80, b1 & 0x0F, b2 & 0x7F
            if n == 126:
                n = struct.unpack('>H', self._read(2))[0]
            elif n == 127:
                n = struct.unpack('>Q', self._read(8))[0]
            mask = self._read(4) if b2 & 0x80 else None
            data = self._read(n)
            if mask:
                data = bytes(b ^ mask[i & 3] for i, b in enumerate(data))
            if op == 0x9:          # ping → pong
                self._frame(0xA, data)
                continue
            if op == 0xA:          # pong
                continue
            if op == 0x8:
                raise RuntimeError('Chrome が接続を閉じた')
            msg += data
            if fin:
                return msg.decode('utf-8')

    def close(self):
        try:
            self._frame(0x8, b'')
        except Exception:
            pass
        self.sock.close()


def builtin_cdp():
    port = int(os.environ.get('CDP_PORT', '9222'))
    proc = []

    def is_running():
        try:
            urllib.request.urlopen('http://127.0.0.1:%d/json/version' % port, timeout=2).read()
            return True
        except Exception:
            return False

    def start():
        exe = find_browser()
        if not exe:
            sys.exit('Chrome が見つからない。環境変数 CDP_CHROME に chrome.exe の場所を入れて実行し直すこと。')
        profile = os.path.join(tempfile.gettempdir(), 'smile_threshold_render_profile')
        args = [exe, '--remote-debugging-port=%d' % port, '--user-data-dir=%s' % profile,
                '--no-first-run', '--no-default-browser-check', '--disable-background-timer-throttling',
                '--disable-renderer-backgrounding', '--disable-backgrounding-occluded-windows',
                '--force-device-scale-factor=1', '--window-size=1200,1400']   # 画面の拡大率（125% など）の影響を受けない
        if os.environ.get('CDP_HEADLESS') == '1':
            args.append('--headless=new')
        args += os.environ.get('CDP_CHROME_ARGS', '').split()   # 追加の起動オプション（通常は不要）
        args.append('about:blank')
        print('Chrome を起動: %s' % exe)
        proc.append(subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        for _ in range(60):
            if is_running():
                return
            time.sleep(0.5)
        sys.exit('Chrome のデバッグ用ポート %d に接続できなかった（別の Chrome が使っていないか確認）' % port)

    def stop():
        for p in proc:
            try:
                p.terminate()
            except Exception:
                pass

    return {'PORT': port, 'is_running': is_running, 'start': start, 'stop': stop, 'WS': MiniWS}


def get_cdp():
    path = find_cdp_py()
    if path:
        rs.CDP = path
        ns = rs.load_cdp()
        if all(k in ns for k in ('PORT', 'WS', 'is_running', 'start')):
            print('Chrome の操作: %s' % path)
            return ns
    print('Chrome の操作: 内蔵の簡易版（cdp.py が見つからないため）')
    return builtin_cdp()


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
def parse_args(argv):
    """位置引数2つ（刺激一覧・出力先）と、--only=ID,ID / --only ID,ID / --sheet を読む"""
    pos, only, sheet, i = [], None, False, 0
    while i < len(argv):
        a = argv[i]
        if a.startswith('--only'):
            if '=' in a:
                only = a.split('=', 1)[1]
            elif i + 1 < len(argv):
                only = argv[i + 1]
                i += 1
        elif a == '--sheet':
            sheet = True
        elif a.startswith('--'):
            sys.exit('知らないオプション: %s' % a)
        else:
            pos.append(a)
        i += 1
    if len(pos) != 2:
        usage()
    only = set(x.strip() for x in only.split(',') if x.strip()) if only else None
    return pos[0], pos[1], only, sheet


def main():
    csv_arg, out_arg, only, sheet_only = parse_args(sys.argv[1:])
    csv_path, out_dir = os.path.abspath(csv_arg), os.path.abspath(out_arg)
    rows = read_rows(csv_path)
    if only:
        unknown = only - {r['stimulus_id'] for r in rows}
        if unknown:
            sys.exit('刺激一覧に無い ID: %s' % ', '.join(sorted(unknown)))
        rows_to_render = [r for r in rows if r['stimulus_id'] in only]
    else:
        rows_to_render = rows
    print('刺激一覧: %d 件（書き出し %d 件） -> %s' % (len(rows), len(rows_to_render), out_dir))

    if not sheet_only:
        from PIL import Image
        for p in rs.VRM_PATHS.values():
            if not os.path.exists(p):
                sys.exit('VRM が無い: %s' % p)
        if not os.path.isdir(out_dir):
            os.makedirs(out_dir)
        ns = get_cdp()
        httpd = rs.serve()
        try:
            if not ns['is_running']():
                ns['start']()
                time.sleep(1.5)
            ev, close = open_tab(ns)
            size_warned = False
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
                        if im.size != (1000, 1250) and not size_warned:
                            print('  注意：画像が %dx%d 画素（実験1は 1000x1250）。画面の拡大率の影響の可能性' % im.size)
                            size_warned = True
                        im.save(os.path.join(out_dir, r['stimulus_id'] + '.png'), optimize=True, compress_level=9)
                        print('  %-14s %s' % (r['stimulus_id'],
                                              ', '.join('%s=%.2f' % kv for kv in sorted(shapes.items())) or '(中立)'))
            finally:
                close()
        finally:
            httpd.shutdown()
            if 'stop' in ns:
                ns['stop']()   # 内蔵版で起動した Chrome を閉じる

    for avatar, tag in [('male', 'M'), ('female', 'F')]:
        path = os.path.join(rs.HERE, '閾値刺激一覧_%s.png' % tag)
        print('一覧: %s %s' % (path, contact_sheet(rows, out_dir, avatar, path)))


if __name__ == '__main__':
    main()
