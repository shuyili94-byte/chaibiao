const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const script = path.join(__dirname, '..', 'static', 'app.js');
const ui = fs.existsSync(script) ? require(script) : {};

function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return {promise, resolve, reject};
}

test('period filtering offers only real available dates and preserves cumulative period names', () => {
  assert.equal(typeof ui.filterPeriods, 'function', 'Available financial periods must be implemented');
  const periods = [
    {date:'2025-12-31', year:2025, type:'annual'},
    {date:'2026-06-30', year:2026, type:'halfyear'},
    {date:'2026-03-31', year:2026, type:'q1'}
  ];
  assert.deepEqual(ui.filterPeriods(periods, '2026', 'annual'), []);
  assert.deepEqual(ui.filterPeriods(periods, '2026', 'all').map(p => p.date), ['2026-06-30','2026-03-31']);
  assert.match(ui.periodLabel(periods[1]), /半年报.*累计/);
  assert.match(ui.periodLabel(periods[2]), /一季报.*累计/);
});

test('company search retries the same failed query and ignores an older search response', async () => {
  assert.equal(typeof ui.createMarketController, 'function', 'Public company workflow must be implemented');
  const first = deferred();
  let count = 0;
  const market = ui.createMarketController({request: async path => {
    count++;
    if (count === 1) return first.promise;
    if (count === 2) throw new Error('网络暂不可用');
    assert.equal(path, '/api/v1/companies/search?q=%E5%B9%B3%E5%AE%89');
    return {companies:[{code:'000001',name:'平安银行'}]};
  }});
  const old = market.search('旧查询');
  await market.search('平安');
  assert.match(market.getState().error, /网络暂不可用/);
  await market.search('平安');
  first.resolve({companies:[{code:'999999',name:'旧响应'}]});
  await old;
  assert.equal(market.getState().companies[0].code, '000001');
  assert.equal(market.getState().error, '');
});

test('changing company discards the previous pending period list', async () => {
  assert.equal(typeof ui.createMarketController, 'function');
  const old = deferred();
  const market = ui.createMarketController({request: path => path.includes('/000001/') ? old.promise : Promise.resolve({periods:[{date:'2025-12-31',year:2025,type:'annual'}],coverage:{note:'可用范围'}})});
  const first = market.selectCompany({code:'000001',name:'甲'});
  await market.selectCompany({code:'600000',name:'乙'});
  old.resolve({periods:[{date:'2000-12-31',year:2000,type:'annual'}]});
  await first;
  assert.equal(market.getState().company.name, '乙');
  assert.equal(market.getState().date, '2025-12-31');
});

test('unsupported analysis preserves the complete source statements', async () => {
  assert.equal(typeof ui.createMarketController, 'function');
  const rows = Array.from({length:35}, (_,i) => ({label:'源科目'+i,value:i,unit:'CNY_million'}));
  let source = null, analysisError = '';
  const market = ui.createMarketController({
    request: async path => {
      if (path.endsWith('/periods')) return {periods:[{date:'2025-12-31',year:2025,type:'annual'}]};
      if (path.includes('/report?')) return {payload:{industry:'financial'},raw_statements:[{title:'原始资产负债表',rows}],provenance:{provider:'public'}};
      throw new Error('金融行业指标暂不适用');
    },
    onRaw: result => { source = result; },
    onAnalysisError: error => { analysisError = error.message; }
  });
  await market.selectCompany({code:'000001',name:'金融示例'});
  await market.loadReport();
  assert.equal(source.raw_statements[0].rows.length,35);
  assert.match(analysisError,/金融行业/);
  assert.equal(market.getState().loading.report,false);
});

test('changing period blocks a late analysis response from replacing the new choice', async () => {
  assert.equal(typeof ui.createMarketController, 'function');
  const oldAnalysis = deferred();
  const started = deferred();
  const shown = [];
  const market = ui.createMarketController({
    request: async path => {
      if (path.endsWith('/periods')) return {periods:[{date:'2025-12-31',year:2025,type:'annual'},{date:'2025-06-30',year:2025,type:'halfyear'}]};
      if (path.includes('/report?')) return {payload:{company:'甲'},raw_statements:[]};
      started.resolve(); return oldAnalysis.promise;
    },
    onAnalysis: report => shown.push(report)
  });
  await market.selectCompany({code:'600000',name:'甲'});
  const loading = market.loadReport();
  await started.promise;
  market.selectDate('2025-06-30');
  oldAnalysis.resolve({metrics:[{key:'old'}]});
  await loading;
  assert.deepEqual(shown,[]);
  assert.equal(market.getState().date,'2025-06-30');
});

test('an unlisted report date cannot be selected or requested', async () => {
  let requestedDate = '';
  const market = ui.createMarketController({request: async path => {
    if (path.endsWith('/periods')) return {periods:[{date:'2026-06-30',year:2026,type:'halfyear'}]};
    if (path.includes('/report?')) { requestedDate = path; return {payload:{},raw_statements:[]}; }
    return {metrics:[]};
  }});
  await market.selectCompany({code:'600000',name:'甲'});
  market.selectDate('2026-12-31');
  await market.loadReport();
  assert.equal(market.getState().date,'2026-06-30');
  assert.match(requestedDate,/date=2026-06-30$/);
});

test('choosing a company hides any old in-flight search results', async () => {
  const pending = deferred();
  const market = ui.createMarketController({request: async path => path.includes('/search?') ? pending.promise : {periods:[]}});
  const searching = market.search('旧名字');
  await market.selectCompany({code:'600000',name:'已选择'});
  pending.resolve({companies:[{code:'000001',name:'旧名字结果'}]});
  await searching;
  assert.deepEqual(market.getState().companies,[]);
  assert.equal(market.getState().loading.search,false);
});

// These tests catch malformed/oversized input reaching analysis and broken API auth/error handling.
test('JSON import accepts an object and rejects malformed or non-object payloads', () => {
  assert.equal(typeof ui.parsePayload, 'function', 'JSON import must be implemented');
  assert.deepEqual(ui.parsePayload('{"company":"示例"}'), {company: '示例'});
  for (const input of ['', '{', 'null', '[]', '3']) assert.throws(() => ui.parsePayload(input));
});

test('JSON import enforces its byte limit for Chinese text', () => {
  assert.equal(typeof ui.parsePayload, 'function', 'JSON import must be implemented');
  assert.throws(() => ui.parsePayload(JSON.stringify({name: '财'.repeat(45000)})), /128/);
});

test('file import rejects non-JSON and oversized files before reading', () => {
  assert.equal(typeof ui.validateFile, 'function', 'File validation must be implemented');
  assert.doesNotThrow(() => ui.validateFile({name: 'report.JSON', size: 128}));
  assert.throws(() => ui.validateFile({name: 'report.html', size: 128}));
  assert.throws(() => ui.validateFile({name: 'report.json', size: 131073}), /128/);
});

test('missing and nonfinite metrics never become a misleading zero', () => {
  assert.equal(typeof ui.formatValue, 'function', 'Metric formatting must be implemented');
  assert.equal(ui.formatValue(null, '%'), '—');
  assert.equal(ui.formatValue(NaN, '%'), '—');
  assert.equal(ui.formatValue(0, '%'), '0%');
  assert.equal(ui.formatValue(25.125, '%'), '25.13%');
});

test('report values translate machine unit codes into readable Chinese', () => {
  assert.equal(ui.formatValue(1.25, 'times'), '1.25倍');
  assert.equal(ui.formatValue(2980, 'CNY_million'), '2,980百万元');
});

test('source numeric strings are grouped and rounded without losing large integer precision', () => {
  assert.equal(ui.formatValue('89389354416.840000', '元'), '89,389,354,416.84元');
  assert.equal(ui.formatValue('900719925474099312345.6789', '元'), '900,719,925,474,099,312,345.68元');
  assert.equal(ui.formatValue('999999999999999999999.999', '元'), '1,000,000,000,000,000,000,000元');
  assert.equal(ui.formatValue('-0.005', '元'), '-0.01元');
  assert.equal(ui.formatValue('2.530000', '元/股'), '2.53元/股');
});

test('source string zero, missing values and nonnumeric descriptions keep their meaning', () => {
  assert.equal(ui.formatValue('0.000000', '元'), '0元');
  assert.equal(ui.formatValue('', '元'), '—');
  assert.equal(ui.formatValue('  ', '元'), '—');
  assert.equal(ui.formatValue(null, '元'), '—');
  assert.equal(ui.formatValue('不适用', '元'), '不适用');
});

test('statement tables show entered amounts and preserve missing values and labels as text', () => {
  assert.equal(typeof ui.renderStatementRows, 'function', 'Statement rows must render into each section');
  // A tiny DOM boundary: all rendering and formatting below execute the real production helper.
  class Node {
    constructor(tag) { this.tagName = tag; this.children = []; this.textContent = ''; }
    append(...children) { this.children.push(...children); }
    get text() { return this.textContent + this.children.map(child => child.text).join(''); }
  }
  const doc = {createElement: tag => new Node(tag)};
  const table = ui.renderStatementRows([
    {label: '总资产', value: 29500, unit: 'CNY_million'},
    {label: '缺失项', value: null, unit: 'CNY_million'},
    {label: '<img src=x onerror=alert(1)>', value: 0, unit: 'CNY_million'}
  ], doc);
  assert.equal(table.tagName, 'table');
  assert.equal(table.children[0].text, '科目金额');
  assert.equal(table.children[1].children[0].text, '总资产29,500百万元');
  assert.equal(table.children[1].children[1].text, '缺失项—');
  assert.equal(table.children[1].children[2].children[0].textContent, '<img src=x onerror=alert(1)>');
  assert.equal(table.children[1].children[2].text, '<img src=x onerror=alert(1)>0百万元');
});

test('printing opens all metric formulas and restores each original disclosure state afterward', () => {
  assert.equal(typeof ui.installPrintDetails, 'function', 'Printed reports must include closed formulas');
  const closedFormula = {open: false};
  const openFormula = {open: true};
  const events = new EventTarget();
  ui.installPrintDetails(events, () => [closedFormula, openFormula]);
  events.dispatchEvent(new Event('beforeprint'));
  assert.equal(closedFormula.open, true);
  assert.equal(openFormula.open, true);
  // Repeated print-preview events must not overwrite the screen's original state.
  events.dispatchEvent(new Event('beforeprint'));
  events.dispatchEvent(new Event('afterprint'));
  assert.equal(closedFormula.open, false);
  assert.equal(openFormula.open, true);
  closedFormula.open = true;
  openFormula.open = false;
  events.dispatchEvent(new Event('beforeprint'));
  events.dispatchEvent(new Event('afterprint'));
  assert.equal(closedFormula.open, true);
  assert.equal(openFormula.open, false);
});

test('API calls transmit the token and surface server validation errors', async () => {
  assert.equal(typeof ui.requestJson, 'function', 'API transport must be implemented');
  const server = http.createServer((req, res) => {
    res.setHeader('Content-Type', 'application/json');
    if (req.headers['x-chaibiao-token'] !== 'test-token') {
      res.writeHead(403); res.end('{"error":"token missing"}'); return;
    }
    if (req.url === '/bad') {
      res.writeHead(422); res.end('{"error":"报告期不一致"}'); return;
    }
    res.end('{"status":"ok"}');
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const base = 'http://127.0.0.1:' + server.address().port;
  try {
    assert.deepEqual(await ui.requestJson(base + '/ok', {}, 'test-token'), {status: 'ok'});
    await assert.rejects(ui.requestJson(base + '/bad', {}, 'test-token'), /报告期不一致/);
  } finally {
    await new Promise(resolve => server.close(resolve));
  }
});
