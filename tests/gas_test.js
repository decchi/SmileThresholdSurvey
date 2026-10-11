// gas/Code.gs の動作確認（スプレッドシートを模した偽物の上で動かす）
//   node tests/gas_test.js [payloads.json]
// payloads.json は tests/e2e_test.py --keep <dir> が残す模擬回答。無ければ小さな回答を作って使う。
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');

// ---------------------------------------------------------------- 偽のスプレッドシート
function makeSheet(name) {
  const rows = []; // rows[r][c]（0 始まり）
  const sh = {
    name, rows, frozen: 0,
    getLastRow: () => rows.length,
    getLastColumn: () => rows.reduce((m, r) => Math.max(m, r.length), 0),
    setFrozenRows: (n) => { sh.frozen = n; },
    getRange: (r, c, nr, nc) => ({
      setValues: (vals) => {
        assert.strictEqual(vals.length, nr, 'rows mismatch');
        vals.forEach((v, i) => {
          assert.strictEqual(v.length, nc, 'cols mismatch');
          const row = rows[r - 1 + i] || (rows[r - 1 + i] = []);
          v.forEach((x, j) => { row[c - 1 + j] = x; });
        });
      },
      getValues: () => {
        const out = [];
        for (let i = 0; i < nr; i++) {
          const row = rows[r - 1 + i] || [];
          const o = [];
          for (let j = 0; j < nc; j++) o.push(row[c - 1 + j] === undefined ? '' : row[c - 1 + j]);
          out.push(o);
        }
        return out;
      },
      createTextFinder: (text) => {
        const f = {
          matchEntireCell: () => f,
          findNext: () => {
            for (let i = 0; i < nr; i++) {
              const row = rows[r - 1 + i] || [];
              for (let j = 0; j < nc; j++) if (String(row[c - 1 + j]) === text) return { row: r + i };
            }
            return null;
          }
        };
        return f;
      }
    })
  };
  return sh;
}
function makeSpreadsheet() {
  const sheets = {};
  return {
    sheets,
    getSheetByName: (n) => sheets[n] || null,
    insertSheet: (n) => (sheets[n] = makeSheet(n))
  };
}

function load(ss) {
  const ctx = {
    SpreadsheetApp: { getActiveSpreadsheet: () => ss },
    LockService: { getScriptLock: () => ({ waitLock: () => {}, releaseLock: () => {} }) },
    ContentService: {
      MimeType: { JSON: 'json' },
      createTextOutput: (s) => ({ setMimeType: () => ({ body: s }) })
    },
    console
  };
  vm.createContext(ctx);
  vm.runInContext(fs.readFileSync(path.join(__dirname, '..', 'gas', 'Code.gs'), 'utf8'), ctx);
  return ctx;
}
const res = (out) => JSON.parse(out.body);
const post = (ctx, obj) => res(ctx.doPost({ postData: { contents: typeof obj === 'string' ? obj : JSON.stringify(obj) } }));

// ---------------------------------------------------------------- 回答の用意
let payloads;
const arg = process.argv[2];
if (arg && fs.existsSync(arg)) payloads = JSON.parse(fs.readFileSync(arg, 'utf8'));
else {
  payloads = [{
    session: { session_id: 's-test', participant_id: 'w1', experiment_id: 'thr1', free_comment: '=HYPERLINK("x")', age: 30 },
    trials: [{ session_id: 's-test', trial_index: 0, phase: 'main', stimulus_id: 'M_smile_050', response: 1, rt_ms: 800 }]
  }];
}

// ---------------------------------------------------------------- 確認
const ss = makeSpreadsheet();
const ctx = load(ss);
assert.ok(res(ctx.doGet({ parameter: {} })).ok);

let nTrials = 0;
payloads.forEach((p) => {
  const r = post(ctx, p);
  assert.ok(r.ok && !r.duplicate, JSON.stringify(r));
  assert.strictEqual(r.n, p.trials.length);
  nTrials += p.trials.length;
});
const S = ss.sheets.sessions.rows, R = ss.sheets.responses.rows, W = ss.sheets.raw.rows;
assert.strictEqual(S.length, payloads.length + 1, 'sessions rows');
assert.strictEqual(R.length, nTrials + 1, 'responses rows');
assert.strictEqual(W.length, payloads.length + 1, 'raw rows');
assert.strictEqual(ss.sheets.responses.frozen, 1);

// 再送は重複しない
const again = post(ctx, payloads[0]);
assert.ok(again.ok && again.duplicate);
assert.strictEqual(S.length, payloads.length + 1);

// 保存済みの問い合わせ
assert.strictEqual(res(ctx.doGet({ parameter: { check: payloads[0].session.session_id } })).found, true);
assert.strictEqual(res(ctx.doGet({ parameter: { check: 'nope' } })).found, false);

// 列の対応：見出しの名前で値が入っている
const head = S[0], rhead = R[0];
payloads.forEach((p, i) => {
  const row = S[i + 1];
  assert.strictEqual(row[head.indexOf('session_id')], p.session.session_id);
  assert.ok(row[head.indexOf('received_at')]);
});
const t0 = payloads[0].trials[0], r1 = R[1];
['stimulus_id', 'response', 'rt_ms', 'trial_index'].forEach((c) => assert.strictEqual(r1[rhead.indexOf(c)], t0[c], c));

// 数式として解釈されうる文字列は ' 付きで入る
const fc = S[1][head.indexOf('free_comment')];
if (String(payloads[0].session.free_comment || '').startsWith('=')) assert.ok(String(fc).startsWith("'="), fc);

// raw から元の JSON を復元できる（' を外して連結）
const rawRow = W[1];
const text = rawRow.slice(3, 3 + rawRow[2]).map((x) => String(x).slice(1)).join('');
assert.deepStrictEqual(JSON.parse(text), payloads[0]);

// 不正な送信
assert.strictEqual(post(ctx, '{bad').error, 'bad_json');
assert.strictEqual(post(ctx, { session: {}, trials: [] }).error, 'bad_payload');

// 見出しが古い（列が少ない）シートに追記しても、足りない列が右に足される
const ss2 = makeSpreadsheet();
const ctx2 = load(ss2);
const old = ss2.insertSheet('responses');
old.rows.push(['session_id', 'stimulus_id', 'response']);
assert.ok(post(ctx2, payloads[0]).ok);
assert.ok(old.rows[0].length > 3 && old.rows[0].indexOf('rt_ms') > 2);
assert.strictEqual(old.rows[1][0], payloads[0].session.session_id);

console.log('OK  Code.gs: %d 人・%d 試行を保存、再送の重複なし、check 応答、数式の無害化、raw からの復元、見出しの追加',
  payloads.length, nTrials);
