// Connection settings live in chrome.storage.local. Alert filters do NOT --
// they live on the notifier, which is the only thing that actually applies
// them. Keeping a second copy in the browser would mean two sources of truth
// that silently disagree the moment either is edited elsewhere.

const DEFAULT_PATH_PATTERN = '^/reviews/claim-product';
const DEFAULT_ENDPOINT = 'http://127.0.0.1:8787/ingest';
const UI_RULE_NAME = 'my-filters';

const $ = (id) => document.getElementById(id);

const endpointEl = $('endpoint');
const tokenEl = $('token');
const pathEl = $('pathPattern');
const pathErrorEl = $('pathError');
const refreshEl = $('refreshMinutes');
const pageDelayEl = $('pageDelaySeconds');
const savedEl = $('saved');
const statusEl = $('status');

const minEl = $('minValue');
const maxEl = $('maxValue');
const keywordsEl = $('keywords');
const excludeEl = $('excludeKeywords');
const priorityEl = $('priority');
const alertUnknownEl = $('alertUnknown');
const rulesBannerEl = $('rulesBanner');
const rulesSavedEl = $('rulesSaved');
const rulesErrorEl = $('rulesError');

// --- connection settings ----------------------------------------------------

chrome.storage.local
  .get(['endpoint', 'token', 'refreshMinutes', 'pageDelaySeconds', 'pathPattern'])
  .then(({ endpoint = DEFAULT_ENDPOINT, token = '', refreshMinutes = 0,
           pageDelaySeconds = 5, pathPattern = DEFAULT_PATH_PATTERN }) => {
    endpointEl.value = endpoint;
    tokenEl.value = token;
    refreshEl.value = refreshMinutes;
    pageDelayEl.value = pageDelaySeconds;
    pathEl.value = pathPattern;
    loadRules();
  });

$('save').addEventListener('click', async () => {
  const pattern = pathEl.value.trim() || DEFAULT_PATH_PATTERN;
  try {
    new RegExp(pattern);
  } catch (err) {
    pathErrorEl.textContent = `Not a valid regular expression: ${err.message}`;
    return;
  }
  pathErrorEl.textContent = '';

  await chrome.storage.local.set({
    endpoint: endpointEl.value.trim() || DEFAULT_ENDPOINT,
    token: tokenEl.value.trim(),
    pathPattern: pattern,
    pageDelaySeconds: Math.min(60, Math.max(1, parseInt(pageDelayEl.value, 10) || 5)),
    refreshMinutes: Math.max(0, parseInt(refreshEl.value, 10) || 0)
  });
  flash(savedEl, 'Saved');
});

// --- alert filters ----------------------------------------------------------

/** The rules endpoint sits alongside whatever /ingest the user configured. */
function rulesUrl() {
  try {
    return new URL('/rules', endpointEl.value.trim() || DEFAULT_ENDPOINT).toString();
  } catch {
    return new URL('/rules', DEFAULT_ENDPOINT).toString();
  }
}

async function rulesFetch(method, body) {
  const headers = {};
  const token = tokenEl.value.trim();
  if (token) headers['X-Ingest-Token'] = token;
  if (body !== undefined) headers['Content-Type'] = 'application/json';

  const resp = await fetch(rulesUrl(), {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body)
  });
  const text = await resp.text();
  let parsed = null;
  try {
    parsed = JSON.parse(text);
  } catch { /* server sent plain text */ }
  if (!resp.ok) {
    throw new Error(parsed?.error || text || `HTTP ${resp.status}`);
  }
  return parsed;
}

const csv = (list) => (list || []).join(', ');
const parseCsv = (value) =>
  (value || '').split(',').map((s) => s.trim()).filter(Boolean);

function fillForm(rule) {
  minEl.value = rule.min_value_usd ?? '';
  maxEl.value = rule.max_value_usd ?? '';
  keywordsEl.value = csv(rule.keywords);
  excludeEl.value = csv(rule.exclude_keywords);
  priorityEl.value = rule.priority || 'normal';
  alertUnknownEl.checked = rule.alert_on_unknown_value !== false;
}

function banner(text, tone = 'banner') {
  rulesBannerEl.replaceChildren();
  if (!text) return;
  const div = document.createElement('div');
  div.className = tone;
  div.textContent = text;
  rulesBannerEl.appendChild(div);
}

async function loadRules() {
  try {
    const { rules, source } = await rulesFetch('GET');
    fillForm(rules[0] || {});
    if (rules.length > 1) {
      // The form edits one rule. Saving would collapse a multi-rule setup, so
      // say that plainly instead of quietly discarding the rest.
      banner(
        `The notifier currently has ${rules.length} rules (${rules
          .map((r) => r.name)
          .join(', ')}) from the ${source} configuration. This form edits one ` +
        `combined rule — saving replaces all ${rules.length} with it. ` +
        `Edit src/rules.json directly if you want to keep them separate.`
      );
    } else if (source === 'env') {
      banner(
        'RULES_JSON is set in the environment, which overrides anything saved ' +
        'here. Remove it from notifier.env for this form to take effect.'
      );
    } else {
      banner('');
    }
    rulesErrorEl.textContent = '';
  } catch (err) {
    banner('');
    rulesErrorEl.textContent =
      `Could not reach the notifier at ${rulesUrl()} — is run.ps1 running? (${err.message})`;
  }
}

function formToRule() {
  const rule = {
    name: UI_RULE_NAME,
    keywords: parseCsv(keywordsEl.value),
    exclude_keywords: parseCsv(excludeEl.value),
    priority: priorityEl.value,
    alert_on_unknown_value: alertUnknownEl.checked
  };
  const min = parseFloat(minEl.value);
  const max = parseFloat(maxEl.value);
  if (Number.isFinite(min)) rule.min_value_usd = min;
  if (Number.isFinite(max)) rule.max_value_usd = max;
  return rule;
}

$('saveRules').addEventListener('click', async () => {
  const rule = formToRule();
  if (rule.min_value_usd !== undefined && rule.max_value_usd !== undefined &&
      rule.min_value_usd > rule.max_value_usd) {
    rulesErrorEl.textContent = 'Minimum value is above the maximum — nothing would ever match.';
    return;
  }
  rulesErrorEl.textContent = '';
  try {
    const { rules } = await rulesFetch('PUT', { rules: [rule] });
    fillForm(rules[0]);
    banner('');
    flash(rulesSavedEl, 'Filters saved');
  } catch (err) {
    rulesErrorEl.textContent = `Could not save: ${err.message}`;
  }
});

$('resetRules').addEventListener('click', async () => {
  try {
    const { rules } = await rulesFetch('DELETE');
    fillForm(rules[0] || {});
    await loadRules();
    flash(rulesSavedEl, 'Reset to defaults');
  } catch (err) {
    rulesErrorEl.textContent = `Could not reset: ${err.message}`;
  }
});

$('resetSweep').addEventListener('click', async () => {
  // Clears progress only. The dedupe store lives on the notifier and is
  // deliberately untouched -- wiping it would re-alert on the whole catalogue.
  await chrome.storage.local.remove(['sweep', 'lastSweepPages', 'lastSweepDone']);
  try {
    const tabs = await chrome.tabs.query({ url: 'https://www.walmart.com/reviews/*' });
    for (const tab of tabs) {
      const url = new URL(tab.url);
      url.searchParams.delete('page');
      await chrome.tabs.update(tab.id, { url: url.toString() });
    }
    flash(savedEl, tabs.length ? 'Sweep restarted' : 'Cleared (no reviewer tab open)');
  } catch (err) {
    flash(savedEl, `Cleared, but could not reload the tab: ${err.message}`);
  }
  renderStatus();
});

function flash(el, message) {
  el.textContent = message;
  setTimeout(() => { el.textContent = ''; }, 2500);
}

// --- status -----------------------------------------------------------------

function ago(ts) {
  const mins = Math.floor((Date.now() - ts) / 60000);
  if (mins < 1) return 'just now';
  if (mins === 1) return '1 minute ago';
  if (mins < 60) return `${mins} minutes ago`;
  const hrs = Math.floor(mins / 60);
  return hrs === 1 ? '1 hour ago' : `${hrs} hours ago`;
}

function line(cls, strong, rest = '') {
  const span = document.createElement('span');
  span.className = cls;
  span.textContent = strong;
  const div = document.createElement('div');
  div.appendChild(span);
  if (rest) div.appendChild(document.createTextNode(` ${rest}`));
  return div;
}

/** Total distinct items the notifier has ever recorded, or null if unreachable. */
async function knownItemCount() {
  try {
    const url = new URL('/health', endpointEl.value.trim() || DEFAULT_ENDPOINT);
    const resp = await fetch(url.toString());
    if (!resp.ok) return null;
    return (await resp.json()).seen_items ?? null;
  } catch {
    return null;
  }
}

async function renderStatus() {
  const { endpoint = DEFAULT_ENDPOINT, lastRelay, lastSeen = 0, lastNotified = 0,
          lastPage = 1, lastError = '', lastErrorAt, sweep, lastSweepPages,
          lastSweepDone } = await chrome.storage.local.get(
    ['endpoint', 'lastRelay', 'lastSeen', 'lastNotified', 'lastPage',
     'lastError', 'lastErrorAt', 'sweep', 'lastSweepPages', 'lastSweepDone']
  );

  // Built as nodes rather than an HTML string: lastError can contain a server
  // response, and that must never be parsed as markup.
  const frag = document.createDocumentFragment();
  if (!endpoint) {
    frag.appendChild(line('bad', 'No endpoint configured.', 'Set it above.'));
  } else if (!lastRelay) {
    frag.appendChild(line(
      'warn', 'Nothing relayed yet.',
      'Is run.ps1 running? Open your reviewer page in a tab — status updates once it sends.'
    ));
  } else {
    const stale = Date.now() - lastRelay > 60 * 60 * 1000;
    frag.appendChild(line(
      stale ? 'warn' : 'ok',
      `Last relayed ${ago(lastRelay)}`,
      `— page ${lastPage}, ${lastSeen} item${lastSeen === 1 ? '' : 's'}, ` +
      `${lastNotified} alerted.`
    ));
    // Per-page counts read like total coverage; show sweep progress too.
    if (sweep && sweep.total) {
      const done = (sweep.visited || []).length;
      frag.appendChild(line(
        'hint', `Sweep in progress: ${done} of ${sweep.total} pages done.`
      ));
    } else if (lastSweepPages) {
      frag.appendChild(line(
        'hint',
        `Last sweep covered ${lastSweepPages} pages` +
        (lastSweepDone ? `, finished ${ago(lastSweepDone)}.` : '.')
      ));
    }
    const total = await knownItemCount();
    if (total !== null) {
      frag.appendChild(line('hint', `${total} distinct items recorded so far.`));
    }
    if (stale) {
      frag.appendChild(line('hint', 'Over an hour ago.', 'Is the reviewer tab still open?'));
    }
  }
  if (lastError) {
    frag.appendChild(line(
      'bad', `Last error${lastErrorAt ? ` (${ago(lastErrorAt)})` : ''}:`, lastError
    ));
  }
  statusEl.replaceChildren(frag);
}

renderStatus();
setInterval(renderStatus, 30000);
