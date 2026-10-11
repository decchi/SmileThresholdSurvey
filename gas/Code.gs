/**
 * 回答の受け口（Google Apps Script の Web アプリ）── 違和感の閾値の測定
 *
 * 設置：回答用のスプレッドシートで「拡張機能 > Apps Script」を開き、このファイルの中身を貼って保存。
 *       「デプロイ > 新しいデプロイ > ウェブアプリ」、実行ユーザー＝自分、アクセス＝全員。
 *       発行された .../exec の URL を docs/config.js の submitUrl に入れる（README.md 3章）。
 *       直したときは「デプロイを管理 > 編集 > 新バージョン」で出し直す（上書き保存だけでは反映されない）。
 *
 * シート（無ければ作る。1行目が見出し。列が足りなければ右端に足す）
 *   sessions   参加者ごとに1行
 *   responses  試行ごとに1行（分析はこちら）
 *   raw        受け取った JSON そのもの（取りこぼしたときの復元用。1セル5万字の上限があるので分割）
 *
 * 同じ session_id は2回目以降を保存しない（再送しても重複しない）。
 * GET：?check=<session_id> で保存済みかを返す。引数なしは動作確認用。
 */

var SESSION_COLS = ['received_at', 'session_id', 'participant_id', 'experiment_id', 'config_name', 'version', 'mode',
  'seed', 'button_order', 'started_at', 'finished_at', 'duration_s', 'completion_code', 'n_trials_main', 'n_practice',
  'attention_n', 'attention_correct', 'attention_pass', 'images_loaded', 'n_images_failed', 'calibration_method',
  'px_per_mm', 'stimulus_width_mm', 'display_mm_min', 'display_mm_max', 'n_shrunk', 'break_s', 'window_w',
  'window_h', 'screen_w', 'screen_h', 'dpr', 'user_agent', 'language', 'age', 'sex', 'vr_experience',
  'free_comment', 'submit_attempts'];
var TRIAL_COLS = ['session_id', 'participant_id', 'experiment_id', 'mode', 'trial_index', 'phase', 'block',
  'block_trial', 'stimulus_id', 'avatar', 'combo', 'level', 'smile', 'eye', 'lip', 'image_loaded', 'response',
  'response_label', 'correct', 'key', 'rt_ms', 'display_mm', 'shrunk', 't_onset_ms'];
var MAX_BYTES = 1000000;   // これを超える送信は受け付けない
var MAX_TRIALS = 600;
var CHUNK = 45000;         // raw シートの1セルあたりの文字数

function doGet(e) {
  var p = (e && e.parameter) || {};
  if (p.check) {
    return json_({ ok: true, found: sessionExists_(SpreadsheetApp.getActiveSpreadsheet(), String(p.check)) });
  }
  return json_({ ok: true, service: 'SmileThresholdSurvey', time: new Date().toISOString() });
}

function doPost(e) {
  var text = (e && e.postData && e.postData.contents) || '';
  if (text.length > MAX_BYTES) return json_({ ok: false, error: 'too_large' });
  var data;
  try { data = JSON.parse(text); } catch (err) { return json_({ ok: false, error: 'bad_json' }); }
  var s = data && data.session, trials = data && data.trials;
  if (!s || typeof s !== 'object' || !s.session_id || !Array.isArray(trials) || trials.length > MAX_TRIALS) {
    return json_({ ok: false, error: 'bad_payload' });
  }
  var lock = LockService.getScriptLock();
  lock.waitLock(30000);
  try {
    var ss = SpreadsheetApp.getActiveSpreadsheet();
    var sid = String(s.session_id);
    if (sessionExists_(ss, sid)) return json_({ ok: true, duplicate: true, session_id: sid });
    var now = new Date().toISOString();
    appendRaw_(ss, now, sid, text);
    var session = {};
    for (var k in s) session[k] = s[k];
    session.received_at = now;
    appendObjects_(ss, 'sessions', SESSION_COLS, [session]);
    appendObjects_(ss, 'responses', TRIAL_COLS, trials);
    return json_({ ok: true, session_id: sid, n: trials.length });
  } finally {
    lock.releaseLock();
  }
}

/* ---------------------------------------------------------------- 内部 */
function json_(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(ContentService.MimeType.JSON);
}

function sheet_(ss, name, cols) {
  var sh = ss.getSheetByName(name);
  if (!sh) sh = ss.insertSheet(name);
  if (sh.getLastRow() === 0) {
    sh.getRange(1, 1, 1, cols.length).setValues([cols]);
    sh.setFrozenRows(1);
  }
  return sh;
}

/** 1行目の見出しを読み、足りない列は右端に足して、見出しの並びを返す */
function header_(sh, cols) {
  var n = sh.getLastColumn();
  var head = n ? sh.getRange(1, 1, 1, n).getValues()[0].map(String) : [];
  var missing = cols.filter(function (c) { return head.indexOf(c) < 0; });
  if (missing.length) {
    sh.getRange(1, head.length + 1, 1, missing.length).setValues([missing]);
    head = head.concat(missing);
  }
  return head;
}

/** 文字列が = + - @ で始まると数式として解釈されるので、先頭に ' を付けて文字のまま入れる */
function cell_(v) {
  if (v === null || v === undefined) return '';
  if (typeof v === 'number' || typeof v === 'boolean') return v;
  if (typeof v === 'object') v = JSON.stringify(v);
  v = String(v);
  return /^[=+\-@]/.test(v) ? "'" + v : v;
}

function appendObjects_(ss, name, cols, objs) {
  if (!objs.length) return;
  var sh = sheet_(ss, name, cols);
  var head = header_(sh, cols);
  var rows = objs.map(function (o) { return head.map(function (c) { return cell_(o && o[c]); }); });
  sh.getRange(sh.getLastRow() + 1, 1, rows.length, head.length).setValues(rows);
}

function appendRaw_(ss, now, sid, text) {
  var sh = sheet_(ss, 'raw', ['received_at', 'session_id', 'n_chunks', 'payload']);
  var chunks = [];
  for (var i = 0; i < text.length; i += CHUNK) chunks.push("'" + text.slice(i, i + CHUNK));
  var row = [now, sid, chunks.length].concat(chunks);
  sh.getRange(sh.getLastRow() + 1, 1, 1, row.length).setValues([row]);
}

function sessionExists_(ss, sid) {
  var sh = ss.getSheetByName('sessions');
  if (!sh || sh.getLastRow() < 2) return false;
  var head = sh.getRange(1, 1, 1, sh.getLastColumn()).getValues()[0].map(String);
  var col = head.indexOf('session_id') + 1;
  if (!col) return false;
  var hit = sh.getRange(2, col, sh.getLastRow() - 1, 1).createTextFinder(sid).matchEntireCell(true).findNext();
  return !!hit;
}
