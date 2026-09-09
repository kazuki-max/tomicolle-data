// TomiColle 学習データ投函口（Google Apps Script）
// - doPost: アプリからの投函（写真を含まない特徴ベクトル・読み取り文字・正解ラベル）をシートに追記
// - doGet : 学習処理（GitHub Actions）が行番号カーソルで取り出す
const SHEET_NAME = 'inbox';

function sheet_() {
  const props = PropertiesService.getScriptProperties();
  let id = props.getProperty('SHEET_ID');
  let ss = null;
  if (id) { try { ss = SpreadsheetApp.openById(id); } catch (e) { ss = null; } }
  if (!ss) {
    ss = SpreadsheetApp.create('TomiColle inbox');
    props.setProperty('SHEET_ID', ss.getId());
  }
  return ss.getSheetByName(SHEET_NAME) || ss.insertSheet(SHEET_NAME);
}

function doPost(e) {
  try {
    const body = e && e.postData ? e.postData.contents : '';
    if (!body || body.length > 200000) return json_({ ok: false, error: 'size' });
    const obj = JSON.parse(body);
    if (!obj.id || !Array.isArray(obj.vectors)) return json_({ ok: false, error: 'shape' });
    const lock = LockService.getScriptLock();
    lock.waitLock(10000);
    try {
      sheet_().appendRow([new Date().toISOString(), String(obj.id).slice(0, 64), body]);
    } finally {
      lock.releaseLock();
    }
    return json_({ ok: true });
  } catch (err) {
    return json_({ ok: false, error: String(err) });
  }
}

function doGet(e) {
  const since = Number((e && e.parameter && e.parameter.since) || 0);
  const limit = 300;
  const sh = sheet_();
  const last = sh.getLastRow();
  const start = Math.max(1, since + 1);
  if (last < start) return json_({ rows: [], next: last, last: last });
  const n = Math.min(limit, last - start + 1);
  const values = sh.getRange(start, 1, n, 3).getValues();
  const rows = values.map((r, i) => ({ row: start + i, at: r[0], id: r[1], data: r[2] }));
  return json_({ rows: rows, next: start + n - 1, last: last });
}

function json_(o) {
  return ContentService.createTextOutput(JSON.stringify(o)).setMimeType(ContentService.MimeType.JSON);
}
