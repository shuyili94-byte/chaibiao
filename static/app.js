'use strict';

(function () {
  const MAX_BYTES = 128 * 1024;

  function parsePayload(text) {
    if (new TextEncoder().encode(text).length > MAX_BYTES) throw new Error('JSON 数据不能超过 128 KB。');
    let data;
    try { data = JSON.parse(text); } catch (_) { throw new Error('请输入有效的 JSON。可以先下载模板，或载入示例。'); }
    if (!data || typeof data !== 'object' || Array.isArray(data)) throw new Error('JSON 最外层必须是一个对象。');
    return data;
  }

  function validateFile(file) {
    if (!file || !/\.json$/i.test(file.name)) throw new Error('请选择 .json 文件。');
    if (file.size > MAX_BYTES) throw new Error('JSON 文件不能超过 128 KB。');
  }

  function formatValue(value, unit) {
    if (value === null || value === undefined || (typeof value === 'number' && !Number.isFinite(value))) return '—';
    const unitLabel = {times: '倍', CNY_million: '百万元', CNY: '元'}[unit] || unit || '';
    if (typeof value === 'number') return new Intl.NumberFormat('zh-CN', {maximumFractionDigits: 2}).format(value) + unitLabel;
    const text = String(value).trim();
    if (!text) return '—';
    const match = /^([+-]?)(\d+)(?:\.(\d*))?$/.exec(text);
    if (!match) return text;
    // BigInt keeps source strings exact, including values beyond Number.MAX_SAFE_INTEGER.
    const fraction = (match[3] || '').padEnd(3, '0');
    let cents = BigInt(match[2]) * 100n + BigInt(fraction.slice(0, 2));
    if (fraction[2] >= '5') cents += 1n;
    const integer = (cents / 100n).toString().replace(/\B(?=(\d{3})+(?!\d))/g, ',');
    const decimal = (cents % 100n).toString().padStart(2, '0').replace(/0+$/, '');
    return (match[1] === '-' && cents !== 0n ? '-' : '') + integer + (decimal ? '.' + decimal : '') + unitLabel;
  }

  function renderStatementRows(rows, doc) {
    const table = doc.createElement('table');
    table.className = 'statement-table';
    const head = doc.createElement('thead');
    const heading = doc.createElement('tr');
    for (const label of ['科目', '金额']) {
      const cell = doc.createElement('th'); cell.textContent = label; heading.append(cell);
    }
    head.append(heading); table.append(head);
    const body = doc.createElement('tbody');
    for (const item of rows) {
      const row = doc.createElement('tr');
      const label = doc.createElement('td'); label.textContent = String(item.label || '未命名科目');
      const value = doc.createElement('td'); value.textContent = formatValue(item.value, item.unit);
      row.append(label, value); body.append(row);
    }
    table.append(body);
    return table;
  }

  function installPrintDetails(events, getDetails) {
    let previous = null;
    events.addEventListener('beforeprint', () => {
      if (previous) return;
      previous = Array.from(getDetails(), node => ({node, open: node.open}));
      previous.forEach(({node}) => { node.open = true; });
    });
    events.addEventListener('afterprint', () => {
      if (!previous) return;
      previous.forEach(({node, open}) => { node.open = open; });
      previous = null;
    });
  }

  const PERIOD_TYPES = {annual: '年报', halfyear: '半年报', q1: '一季报', q3: '三季报'};

  function filterPeriods(periods, year = 'all', type = 'all') {
    const seen = new Set();
    return periods.filter(period => {
      if (!/^\d{4}-\d{2}-\d{2}$/.test(period.date) || !PERIOD_TYPES[period.type] || seen.has(period.date)) return false;
      seen.add(period.date);
      return (year === 'all' || String(period.year) === String(year)) && (type === 'all' || period.type === type);
    }).sort((a, b) => b.date.localeCompare(a.date));
  }

  function periodLabel(period) {
    return period.year + '年 ' + (PERIOD_TYPES[period.type] || '财报') + ' · 年初累计至 ' + period.date;
  }

  function createMarketController(callbacks) {
    const state = {query: '', companies: [], company: null, periods: [], coverage: null,
      year: 'all', type: 'all', date: '', error: '', errorStage: '', loading: {search: false, periods: false, report: false}};
    let searchVersion = 0, selectionVersion = 0;
    const emit = () => callbacks.onChange?.({...state, loading: {...state.loading}});
    const clear = () => callbacks.onClear?.();
    const fail = (error, stage) => { state.error = error.message || '查询失败，请重试。'; state.errorStage = stage; };
    const visible = () => filterPeriods(state.periods, state.year, state.type);
    return {
      getState: () => ({...state, loading: {...state.loading}}),
      async search(query) {
        const version = ++searchVersion;
        state.query = query.trim(); state.companies = []; state.error = ''; state.errorStage = '';
        if (!state.query) { state.loading.search = false; emit(); return; }
        state.loading.search = true; emit();
        try {
          const result = await callbacks.request('/api/v1/companies/search?q=' + encodeURIComponent(state.query));
          if (version !== searchVersion) return;
          state.companies = Array.isArray(result.companies) ? result.companies : [];
        } catch (error) { if (version === searchVersion) fail(error, 'search'); }
        finally { if (version === searchVersion) { state.loading.search = false; emit(); } }
      },
      async selectCompany(company) {
        ++searchVersion; state.loading.search = false;
        const version = ++selectionVersion;
        state.company = company; state.periods = []; state.coverage = null; state.date = '';
        state.year = 'all'; state.type = 'all'; state.error = ''; state.errorStage = ''; state.companies = [];
        state.loading.periods = true; state.loading.report = false; clear(); emit();
        try {
          const result = await callbacks.request('/api/v1/companies/' + encodeURIComponent(company.code) + '/periods');
          if (version !== selectionVersion) return;
          if (result.company && typeof result.company === 'object') state.company = {...company, ...result.company, code: company.code};
          state.periods = filterPeriods(Array.isArray(result.periods) ? result.periods : []);
          state.coverage = result.coverage || null;
          state.date = state.periods[0]?.date || '';
          state.year = state.periods[0] ? String(state.periods[0].year) : 'all';
        } catch (error) { if (version === selectionVersion) fail(error, 'periods'); }
        finally { if (version === selectionVersion) { state.loading.periods = false; emit(); } }
      },
      clearSelection() {
        ++searchVersion; state.loading.search = false; state.companies = [];
        ++selectionVersion;
        state.company = null; state.periods = []; state.coverage = null; state.date = '';
        state.year = 'all'; state.type = 'all'; state.error = ''; state.errorStage = '';
        state.loading.periods = false; state.loading.report = false; clear(); emit();
      },
      setFilters(year, type) {
        ++selectionVersion;
        state.year = year; state.type = type;
        const periods = visible();
        if (!periods.some(period => period.date === state.date)) state.date = periods[0]?.date || '';
        state.error = ''; state.errorStage = ''; state.loading.report = false; clear(); emit();
      },
      selectDate(date) {
        if (!visible().some(period => period.date === date)) return;
        ++selectionVersion; state.date = date; state.error = ''; state.errorStage = '';
        state.loading.report = false; clear(); emit();
      },
      async loadReport() {
        if (!state.company || !visible().some(period => period.date === state.date)) return;
        const version = ++selectionVersion;
        const context = {company: state.company, period: state.periods.find(period => period.date === state.date)};
        state.error = ''; state.errorStage = ''; state.loading.report = true; clear(); emit();
        try {
          const result = await callbacks.request('/api/v1/companies/' + encodeURIComponent(state.company.code) + '/report?date=' + encodeURIComponent(state.date));
          if (version !== selectionVersion) return;
          callbacks.onRaw?.(result, context);
          try {
            if (!result.payload) throw new Error('此报告缺少可解读的标准化数据，仍可阅读下方原始报表。');
            const report = await callbacks.request('/api/v1/analyze', {method: 'POST', body: JSON.stringify(result.payload)});
            if (version === selectionVersion) callbacks.onAnalysis?.(report, context);
          } catch (error) { if (version === selectionVersion) callbacks.onAnalysisError?.(error, context); }
        } catch (error) { if (version === selectionVersion) fail(error, 'report'); }
        finally { if (version === selectionVersion) { state.loading.report = false; emit(); } }
      }
    };
  }

  async function requestJson(path, options, token) {
    const abort = new AbortController();
    const timeout = path.includes('/companies/') ? 60000 : 20000;
    const timer = setTimeout(() => abort.abort(), timeout);
    try {
      const response = await fetch(path, {
        ...options, credentials: 'same-origin', cache: 'no-store', signal: abort.signal,
        headers: {'Content-Type': 'application/json', ...(options.headers || {}), 'X-Chaibiao-Token': token}
      });
      let result;
      try { result = await response.json(); } catch (_) { throw new Error('服务没有返回有效的 JSON，请检查本地服务是否正常。'); }
      if (!response.ok) {
        const detail = result.error || result.message || result.detail;
        throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail || '请求失败（' + response.status + '）'));
      }
      return result;
    } catch (error) {
      if (error.name === 'AbortError') throw new Error('请求超过 ' + timeout / 1000 + ' 秒，请检查服务状态后重试。');
      if (error instanceof TypeError) throw new Error('无法连接本地服务。请确认服务正在运行。');
      throw error;
    } finally { clearTimeout(timer); }
  }

  if (typeof module !== 'undefined' && module.exports) module.exports = {parsePayload, validateFile, formatValue, renderStatementRows, installPrintDetails, requestJson, filterPeriods, periodLabel, createMarketController};
  if (typeof document === 'undefined') return;

  installPrintDetails(window, () => document.querySelectorAll('.metric-card details, .raw-statement'));

  const $ = id => document.getElementById(id);
  const token = document.querySelector('meta[name="api-token"]').content;
  const api = (path, options = {}) => requestJson(path, options, token);
  let lastReport = null;
  let lastSource = null;
  let marketState = null;
  let market = null;
  let currentOrder = null;
  let orderKey = null;
  let orderPayload = null;
  let busy = false;

  function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }

  function asText(value) {
    if (value === undefined || value === null) return '未提供';
    return typeof value === 'object' ? JSON.stringify(value, null, 2) : String(value);
  }

  function section(title, caption) {
    const block = element('section', 'report-section');
    const heading = element('div', 'section-heading');
    heading.append(element('h3', '', title));
    if (caption) heading.append(element('span', '', caption));
    block.append(heading);
    return block;
  }

  function badge(text, style = 'gray') { return element('span', 'pill ' + style, text); }

  function updateActions() {
    const fetching = marketState?.loading.report || marketState?.loading.periods || false;
    for (const id of ['demo-button', 'analyze-button', 'template-button']) $(id).disabled = busy || fetching;
    $('order-create').disabled = busy || fetching || !$('payload-input').value.trim();
    $('file-input').disabled = busy || fetching;
    $('order-pay').disabled = busy || !currentOrder || currentOrder.status !== 'created';
    $('order-deliver').disabled = busy || !currentOrder || !['paid', 'delivered'].includes(currentOrder.status);
    $('download-button').disabled = (!lastReport && !lastSource) || busy || fetching;
    $('print-button').disabled = (!lastReport && !lastSource) || busy || fetching;
    $('search-button').disabled = busy;
    $('clear-company').disabled = busy;
    $('load-report').disabled = busy || fetching || !marketState?.date;
    $('year-filter').disabled = busy || !marketState?.periods.length;
    $('type-filter').disabled = busy || !marketState?.periods.length;
    $('period-select').disabled = busy || !marketState?.date;
  }

  function clearOutput() {
    lastReport = null; lastSource = null; currentOrder = null; orderKey = null; orderPayload = null;
    $('report').replaceChildren(); $('report').hidden = true;
    $('raw-report').replaceChildren(); $('raw-report').hidden = true;
    $('analysis-state').hidden = true; $('empty-state').hidden = false; $('error-message').hidden = true;
    $('order-status').textContent = '尚未创建订单';
    $('payload-input').value = ''; updateSize();
    updateActions();
  }

  function option(value, label, disabled = false) {
    const node = element('option', '', label); node.value = value; node.disabled = disabled; return node;
  }

  function renderMarketState(state) {
    marketState = state;
    $('search-button').textContent = state.loading.search ? '重新搜索' : '搜索';
    const results = $('company-results'); results.replaceChildren();
    for (const company of state.companies) {
      const button = element('button', 'company-option'); button.type = 'button';
      button.append(element('strong', '', company.name), element('span', '', [company.code, company.exchange].filter(Boolean).join(' · ')));
      button.addEventListener('click', () => { $('company-query').value = company.code; market.selectCompany(company); });
      results.append(button);
    }
    $('search-status').textContent = state.loading.search ? '正在查询公司，请稍候；可输入新关键词重新搜索。' :
      state.errorStage === 'search' ? '搜索未完成，可保留同一关键词再次搜索重试。' :
      state.companies.length ? '找到 ' + state.companies.length + ' 家公司，请选择：' :
      state.company ? '已选择公司，可更换公司或选择报告期。' :
      state.query ? '未找到匹配公司，请换用六位股票代码或公司名称。' : '搜索后请选择准确的公司。';
    const selected = $('selected-company'); selected.replaceChildren(); selected.hidden = !state.company;
    $('clear-company').hidden = !state.company;
    $('period-picker').hidden = !state.company;
    if (state.company) {
      selected.append(element('strong', '', state.company.name));
      selected.append(element('span', '', [state.company.code, state.company.exchange, state.company.listing_date ? '上市 ' + state.company.listing_date : ''].filter(Boolean).join(' · ')));
    }
    const coverage = state.coverage || {};
    $('coverage-note').textContent = state.loading.periods ? '正在获取真实可用报告期…' :
      [coverage.earliest && coverage.latest ? '源数据可用区间：' + coverage.earliest + ' 至 ' + coverage.latest : '', coverage.note || '仅展示数据源实际返回的报告期，历史覆盖可能不完整。'].filter(Boolean).join('。');
    const years = [...new Set(state.periods.map(period => String(period.year)))].sort().reverse();
    $('year-filter').replaceChildren(option('all', '全部可用年度'), ...years.map(year => option(year, year + '年')));
    $('year-filter').value = state.year;
    const types = [option('all', '全部类型')];
    for (const [type, label] of Object.entries(PERIOD_TYPES)) {
      const available = filterPeriods(state.periods, state.year, type).length > 0;
      types.push(option(type, label + (available ? '' : '（未披露 / 不可用）'), !available));
    }
    $('type-filter').replaceChildren(...types); $('type-filter').value = state.type;
    const periods = filterPeriods(state.periods, state.year, state.type);
    $('period-select').replaceChildren(...(periods.length ? periods.map(period => option(period.date, periodLabel(period))) : [option('', state.loading.periods ? '正在载入可用期间…' : '本筛选下没有可用报告', true)]));
    $('period-select').value = state.date;
    $('period-note').textContent = state.periods.length ? '当前筛选可选 ' + periods.length + ' 期；全部源数据共 ' + state.periods.length + ' 期。' : state.loading.periods ? '首次获取可能需要一些时间。' : '数据源暂未返回可用报告，可重新查询。';
    $('retry-periods').hidden = !state.company || state.loading.periods || (!state.error && state.periods.length > 0);
    $('load-report').textContent = state.loading.report ? '正在读取三表与生成解读…' : '读取三表并生成解读 →';
    $('market-error').textContent = state.error; $('market-error').hidden = !state.error;
    if (state.loading.periods) $('feedback').textContent = '正在获取 ' + state.company.name + ' 的真实可用报告期。';
    if (state.loading.report) $('feedback').textContent = '正在读取本次选择的公开三表；切换公司或报告期会取消本次显示。';
    if (state.company && !state.loading.report && !state.loading.periods && !lastSource && !lastReport && !state.error) {
      const period = state.periods.find(item => item.date === state.date);
      $('feedback').textContent = period ? '已选择 ' + state.company.name + '，' + periodLabel(period) + '。点击读取三表开始。' : '本次筛选没有可用报告，请调整年度或报告类型。';
    }
    if (state.errorStage === 'report') $('feedback').textContent = '未能读取本次报告，可点击“读取三表并生成解读”重试。';
    updateActions();
  }

  async function run(message, work) {
    if (busy) return;
    busy = true;
    $('error-message').hidden = true;
    $('feedback').textContent = message;
    $('feedback').classList.add('busy');
    updateActions();
    try { await work(); }
    catch (error) {
      $('error-message').textContent = String(error.message || '操作失败，请重试。').slice(0, 1800);
      $('error-message').hidden = false;
      $('feedback').textContent = lastReport ? '本次操作未完成。下方仍是上次成功生成的报告，请查看错误提示。' : '本次操作未完成，请查看提示后重试。';
    } finally {
      busy = false;
      $('feedback').classList.remove('busy');
      updateActions();
    }
  }

  function updateSize() {
    const bytes = new TextEncoder().encode($('payload-input').value).length;
    $('input-size').textContent = bytes < 1024 ? bytes + ' B' : (bytes / 1024).toFixed(1) + ' KB';
  }

  function setPayload(data) { $('payload-input').value = JSON.stringify(data, null, 2); updateSize(); }

  function renderSource(container, source) {
    const items = Array.isArray(source) ? source : [source];
    for (const item of items) {
      const block = element('div', 'source-block');
      if (item && typeof item === 'object') {
        for (const [key, value] of Object.entries(item)) {
          const line = element('div');
          const sourceLabels = {document: '来源文件', kind: '数据类型', page: '页码 / 位置', url: '来源链接', provider: '数据提供方', fetched_at: '获取时间', report_date: '报告截止日', note: '说明'};
          line.append(element('span', '', (sourceLabels[key] || key) + '：'));
          let link = null;
          if (typeof value === 'string') {
            try { const url = new URL(value); if (['http:', 'https:'].includes(url.protocol)) link = url; } catch (_) { /* Render as plain text. */ }
          }
          if (link) { const anchor = element('a', '', value); anchor.href = link.href; anchor.target = '_blank'; anchor.rel = 'noopener noreferrer'; line.append(anchor); }
          else {
            const kindLabel = key === 'kind' ? {demo: '虚构示例', manual: '手工录入（未联网核验）', public_provider: '公开数据提供方'}[value] : null;
            line.append(element('span', '', kindLabel || (value === '' ? '未提供' : asText(value))));
          }
          block.append(line);
        }
      } else block.textContent = asText(item);
      container.append(block);
    }
  }

  function sourceLink(label, value) {
    if (typeof value !== 'string') return null;
    try {
      const url = new URL(value);
      if (!['http:', 'https:'].includes(url.protocol)) return null;
      const anchor = element('a', 'source-link', label + ' ↗');
      anchor.href = url.href; anchor.target = '_blank'; anchor.rel = 'noopener noreferrer';
      return anchor;
    } catch (_) { return null; }
  }

  function renderProvenance(container, data, context) {
    const provenance = data.provenance && typeof data.provenance === 'object' ? data.provenance : {};
    const source = data.payload?.source || {};
    const summary = element('dl', 'source-summary');
    for (const [label, value] of [
      ['数据提供方', provenance.provider || source.provider || '未标注'],
      ['报告截止日', context.period.date],
      ['获取时间', provenance.fetched_at || source.fetched_at || '未标注']
    ]) {
      const row = element('div'); row.append(element('dt', '', label), element('dd', '', asText(value))); summary.append(row);
    }
    container.append(summary);
    const versionNote = provenance.note || '当前为公开聚合版本，历史数据可能重述；比较期间口径、披露版本及原始公告仍需核对。';
    container.append(element('p', 'source-version-note', asText(versionNote)));
    const urls = provenance.statement_urls || {};
    const names = {income: '利润表', balance: '资产负债表', cashflow: '现金流量表'};
    const primary = element('div', 'source-links');
    for (const [key, values] of Object.entries(urls)) {
      const list = Array.isArray(values) ? values : [values];
      const anchor = list.map(value => sourceLink((names[key] || key) + '源数据', value)).find(Boolean);
      if (anchor) primary.append(anchor);
    }
    if (!primary.childElementCount) { const anchor = sourceLink('报表源数据', source.url); if (anchor) primary.append(anchor); }
    container.append(primary);

    const details = element('details', 'provenance-details');
    details.append(element('summary', '', '查看来源链接与版本详情'));
    const content = element('div', 'provenance-content');
    const completeLinks = element('div', 'source-links');
    for (const [key, values] of Object.entries(urls)) {
      const list = Array.isArray(values) ? values : [values];
      list.forEach((value, index) => { const anchor = sourceLink((names[key] || key) + '来源 ' + (index + 1), value); if (anchor) completeLinks.append(anchor); });
    }
    const profiles = Array.isArray(provenance.profile_urls) ? provenance.profile_urls : [];
    profiles.forEach((url, index) => { const anchor = sourceLink('公司资料来源 ' + (index + 1), url); if (anchor) completeLinks.append(anchor); });
    content.append(completeLinks);
    renderSource(content, {
      '报表版本元数据': provenance.statement_metadata || '未提供',
      '比较期基数': provenance.comparators || '未提供',
      '报表累计口径': provenance.income_basis || '本年年初至报告期末累计',
      '历史数据覆盖': provenance.coverage || '未提供'
    });
    details.append(content); container.append(details);
  }

  function renderRawStatements(data, context) {
    const fragment = document.createDocumentFragment();
    const cover = element('header', 'report-cover');
    cover.append(element('p', 'eyebrow', '拆表 / 公开财报查询'));
    cover.append(element('h2', '', context.company.name + ' · ' + context.company.code));
    const labels = element('div', 'cover-labels');
    labels.append(badge(periodLabel(context.period)), badge('公开数据源 · 以正式披露为准'));
    cover.append(labels);
    cover.append(element('p', '', '资产负债表为期末时点数据；利润表与现金流量表为本年年初至报告期末累计。'));
    fragment.append(cover);
    const statements = section('原始三张报表', '保留数据源返回的全部科目');
    statements.append(element('p', '', '展开报表查看源字段。空值表示数据源未提供，不自动补零；字段与原始披露仍需核对。'));
    const list = Array.isArray(data.raw_statements) ? data.raw_statements : [];
    for (let index = 0; index < list.length; index++) {
      const statement = list[index];
      const rows = Array.isArray(statement.rows) ? statement.rows : [];
      const details = element('details', 'raw-statement'); details.open = index === 0;
      const summary = element('summary');
      summary.append(element('strong', '', statement.title || '原始报表'), element('span', '', rows.length + ' 个源字段'));
      details.append(summary);
      if (rows.length) {
        const scroll = element('div', 'raw-table-scroll'); scroll.append(renderStatementRows(rows, document)); details.append(scroll);
      } else details.append(element('p', 'helper', '本期未返回该表的可展示字段。'));
      statements.append(details);
    }
    if (!list.length) statements.append(element('p', 'helper', '数据源本次未返回原始三表字段。'));
    fragment.append(statements);
    const provenance = section('本次查询来源', '公开聚合数据可能存在缺项、延迟或修订');
    renderProvenance(provenance, data, context); fragment.append(provenance);
    $('raw-report').replaceChildren(fragment); $('raw-report').hidden = false;
    $('empty-state').hidden = true;
    $('analysis-state').textContent = '原始报表已获取，正在计算适用指标并生成解读…';
    $('analysis-state').hidden = false;
    lastSource = data;
    if (data.payload) setPayload(data.payload);
    updateActions();
  }

  function renderReport(data, {publicData = false} = {}) {
    const report = data.report || data;
    if (!report || !Array.isArray(report.metrics)) throw new Error('报告结构不完整，未收到指标列表。');
    const fragment = document.createDocumentFragment();
    const meta = report.meta || {};
    const cover = element('header', 'report-cover');
    cover.append(element('p', 'eyebrow', '拆表 / 财报解读与复核'));
    cover.append(element('h2', '', asText(meta.company_name || meta.company || meta.name || '财报解读报告')));
    const labels = element('div', 'cover-labels');
    labels.append(badge(asText(meta.period_label || meta.report_period || meta.period || '报告期待核对')));
    const isDemo = meta.is_demo === true || meta.data_mode === 'demo' || meta.mode === 'demo' || /示例|虚构/.test(asText(meta.company_name || meta.company));
    labels.append(badge(isDemo ? '虚构示例 · 不对应上市公司' : '手动导入 · 来源需自行核实'));
    if (meta.currency || meta.unit) labels.append(badge(meta.unit === 'CNY_million' ? '金额单位：人民币百万元' : [meta.currency, meta.unit].filter(Boolean).join(' / ')));
    cover.append(labels);
    cover.append(element('p', '', '账面利润与现金收支分开呈现；核对结果用于发现需要复查的线索。'));
    if (!publicData) fragment.append(cover);

    if (Array.isArray(report.warnings) && report.warnings.length) {
      const warning = section('阅读前先确认'); warning.classList.add('warning-section');
      const list = element('ul', 'warning-list');
      report.warnings.forEach(text => list.append(element('li', '', asText(text))));
      warning.append(list); fragment.append(warning);
    }

    const metrics = section('核心指标', '保留公式与原始输入');
    const grid = element('div', 'metric-grid');
    for (const metric of report.metrics) {
      const card = element('div', 'metric-card');
      card.append(element('p', 'metric-label', metric.label || metric.key));
      card.append(element('p', 'metric-value', formatValue(metric.value, metric.unit)));
      if (metric.explanation) card.append(element('p', 'metric-explanation', asText(metric.explanation)));
      const details = element('details'); details.append(element('summary', '', '查看公式与输入'));
      details.append(element('p', 'formula', asText(metric.formula)));
      if (metric.inputs) details.append(element('pre', 'formula', asText(metric.inputs)));
      card.append(details); grid.append(card);
    }
    metrics.append(grid); fragment.append(metrics);

    for (const item of report.sections || []) {
      const block = section(item.title || '报表说明');
      const paragraphs = Array.isArray(item.paragraphs) ? item.paragraphs : [item.paragraphs || item.content || ''];
      paragraphs.filter(Boolean).forEach(p => block.append(element('p', '', asText(p))));
      if (Array.isArray(item.rows) && item.rows.length) {
        const scroll = element('div', 'table-scroll');
        scroll.append(renderStatementRows(item.rows, document));
        block.append(scroll);
      }
      fragment.append(block);
    }

    const checks = section('三表核对', '差异提示不等于财务错误或造假');
    const summary = element('div', 'checks-summary');
    const list = report.checks || [];
    const statusMap = {pass: ['通过', 'green'], review: ['待复核', 'amber'], insufficient: ['数据不足', 'gray']};
    for (const [key, [label, style]] of Object.entries(statusMap)) summary.append(badge(label + ' ' + list.filter(c => c.status === key).length, style));
    checks.append(summary);
    const scroll = element('div', 'table-scroll'); const table = element('table');
    const head = element('thead'); const header = element('tr');
    ['核对项', '状态', '数值与说明'].forEach(h => header.append(element('th', '', h))); head.append(header); table.append(head);
    const body = element('tbody');
    for (const check of list) {
      const row = element('tr'); row.append(element('td', '', check.label || check.key));
      const status = statusMap[check.status] || statusMap.insufficient;
      const state = element('td'); state.append(badge(status[0], status[1])); row.append(state);
      const detail = element('td');
      if (check.expected !== null && check.expected !== undefined) detail.append(element('div', '', '预期 ' + asText(check.expected) + ' / 实际 ' + asText(check.actual)));
      if (check.difference !== null && check.difference !== undefined) detail.append(element('div', 'check-detail', '差额：' + asText(check.difference)));
      if (check.explanation) detail.append(element('div', 'check-detail', asText(check.explanation)));
      row.append(detail); body.append(row);
    }
    table.append(body); scroll.append(table); checks.append(scroll); fragment.append(checks);
    const source = section('数据与来源', '请结合原始报表核实'); renderSource(source, report.source || meta.source || '未提供来源'); fragment.append(source);
    fragment.append(element('p', 'report-disclaimer', asText(report.disclaimer || '用于财报学习与信息整理，不提供估值、证券评级或买卖建议。')));
    $('report').replaceChildren(fragment);
    $('report').hidden = false; $('empty-state').hidden = true;
    lastReport = report;
    $('feedback').textContent = '报告已生成。展开指标卡片可查看公式与输入；来源与口径请结合原始报表核实。';
  }

  function download(data, filename) {
    const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], {type: 'application/json;charset=utf-8'}));
    const anchor = element('a'); anchor.href = url; anchor.download = filename; anchor.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  function showOrder() {
    const labels = {created: '已创建 · 待模拟支付', paid: '已模拟支付 · 无真实扣款', delivered: '已交付 · 无真实扣款'};
    $('order-status').textContent = '订单 ' + currentOrder.id + '\n' + (labels[currentOrder.status] || asText(currentOrder.status));
  }

  market = createMarketController({
    request: api,
    onChange: renderMarketState,
    onClear: clearOutput,
    onRaw: renderRawStatements,
    onAnalysis(report) {
      renderReport(report, {publicData: true});
      $('analysis-state').hidden = true;
      $('feedback').textContent = '公开三表与解读已载入。可展开原表查看全部源字段，或查看指标公式与口径。';
    },
    onAnalysisError(error) {
      $('analysis-state').textContent = '原始三表已获取；本期指标解读暂未完成 / 不适用：' + error.message + '。你仍可以阅读和导出上方原始报表。';
      $('analysis-state').hidden = false;
      $('feedback').textContent = '原始报表已保留。部分行业或期间的指标解读可能不适用。';
    }
  });

  $('search-form').addEventListener('submit', event => {
    event.preventDefault(); if (!busy) market.search($('company-query').value);
  });
  $('clear-company').addEventListener('click', () => {
    market.clearSelection(); $('company-query').value = ''; $('company-query').focus();
    $('feedback').textContent = '请选择新的公司；上一家公司的报表已清除。';
  });
  $('year-filter').addEventListener('change', () => market.setFilters($('year-filter').value, 'all'));
  $('type-filter').addEventListener('change', () => market.setFilters($('year-filter').value, $('type-filter').value));
  $('period-select').addEventListener('change', () => market.selectDate($('period-select').value));
  $('load-report').addEventListener('click', () => market.loadReport());
  $('retry-periods').addEventListener('click', () => { if (marketState?.company) market.selectCompany(marketState.company); });
  $('payload-input').addEventListener('input', () => { updateSize(); updateActions(); });
  $('demo-button').addEventListener('click', () => run('正在载入虚构示例并生成报告…', async () => {
    market.clearSelection();
    const payload = await api('/api/v1/demo'); setPayload(payload);
    renderReport(await api('/api/v1/analyze', {method: 'POST', body: JSON.stringify(payload)}));
  }));
  $('analyze-button').addEventListener('click', () => run('正在计算指标并核对报表…', async () => {
    const payload = parsePayload($('payload-input').value);
    market.clearSelection(); setPayload(payload);
    renderReport(await api('/api/v1/analyze', {method: 'POST', body: JSON.stringify(payload)}));
  }));
  $('template-button').addEventListener('click', () => run('正在准备导入模板…', async () => {
    download(await api('/api/v1/template'), '拆表-财报输入模板.json');
    $('feedback').textContent = '模板已下载。请按模板填写数据及来源，再导入生成报告。';
  }));
  $('file-input').addEventListener('change', event => run('正在读取本地 JSON 文件…', async () => {
    const file = event.target.files[0]; if (!file) { $('feedback').textContent = '未选择文件。'; return; }
    validateFile(file); const payload = parsePayload(await file.text()); market.clearSelection(); setPayload(payload);
    $('feedback').textContent = '文件已载入。请确认输入内容后生成报告。';
    event.target.value = '';
  }));
  $('download-button').addEventListener('click', () => {
    if (lastSource) download({...lastSource, report: lastReport}, '拆表-原始三表与解读.json');
    else if (lastReport) download(lastReport, '拆表-解读报告.json');
  });
  $('print-button').addEventListener('click', () => { if (lastReport || lastSource) window.print(); });
  $('order-create').addEventListener('click', () => run('正在创建本地模拟订单（无真实扣款）…', async () => {
    const payload = parsePayload($('payload-input').value);
    const serialized = JSON.stringify(payload);
    if (!orderKey || orderPayload !== serialized || currentOrder) {
      orderKey = crypto.randomUUID(); orderPayload = serialized;
    }
    currentOrder = await api('/api/v1/orders', {method: 'POST', headers: {'Idempotency-Key': orderKey}, body: JSON.stringify({data: payload})});
    showOrder(); $('feedback').textContent = '模拟订单已创建。下一步确认模拟支付，全程不会扣款。';
  }));
  $('order-pay').addEventListener('click', () => run('正在标记模拟支付（无真实扣款）…', async () => {
    currentOrder = await api('/api/v1/orders/' + encodeURIComponent(currentOrder.id) + '/simulate-pay', {method: 'POST', body: '{}'});
    showOrder(); $('feedback').textContent = '模拟支付已确认，没有发生真实扣款。可以获取报告。';
  }));
  $('order-deliver').addEventListener('click', () => run('正在交付模拟订单对应的报告…', async () => {
    currentOrder = await api('/api/v1/orders/' + encodeURIComponent(currentOrder.id) + '/deliver', {method: 'POST', body: '{}'});
    renderReport(currentOrder.report, {publicData: Boolean(lastSource)}); showOrder();
    $('feedback').textContent = '本地模拟订单已交付。此演示不代表已接入支付宝或完成真实支付。';
  }));
  renderMarketState(market.getState());
})();
