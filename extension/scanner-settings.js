// Pure scanner schema/defaults shared by Options, the worker and diagnostics.
var ReviewerScannerSettings = (() => {
  const defaults = Object.freeze({ schemaVersion: 1, auxiliaryDepartments: true,
    auxiliaryKeywords: true, auxiliaryBrands: false, auxiliaryKeywordQueries: null,
    auxiliaryPriority: false, auxiliaryPriorityQueries: null, auxiliaryPriorityRefreshMinutes: 1,
    groupScannerTabs: true, rotateScannerTabs: true, verificationAssistEnabled: false,
    verificationNativeEnabled: false, pathPattern: '^/reviews/claim-product' });
  const keys = Object.freeze(Object.keys(defaults).filter(key => key !== 'schemaVersion'));
  const relayDefaults = Object.freeze({ schemaVersion: 1, refreshMinutes: 30,
    pageDelaySeconds: 15, sweepMode: 'none' });
  function validateRelay(value) {
    const fields = Object.keys(relayDefaults);
    if (!value || typeof value !== 'object' || Array.isArray(value) ||
        Object.keys(value).length !== fields.length || !fields.every(key => Object.hasOwn(value, key)) ||
        value.schemaVersion !== 1 || !Number.isInteger(value.refreshMinutes) ||
        value.refreshMinutes < 0 || value.refreshMinutes > 120 ||
        !Number.isInteger(value.pageDelaySeconds) || value.pageDelaySeconds < 5 ||
        value.pageDelaySeconds > 60 || !['none', 'sweep', 'high-value'].includes(value.sweepMode)) {
      throw new Error('Invalid scan settings.');
    }
    return Object.fromEntries(fields.map(key => [key, value[key]]));
  }
  function validate(value) {
    const optional = ['auxiliaryPriority', 'auxiliaryPriorityQueries', 'auxiliaryPriorityRefreshMinutes'];
    const booleans = keys.filter(key => !['auxiliaryKeywordQueries', 'auxiliaryPriorityQueries',
      'auxiliaryPriorityRefreshMinutes', 'pathPattern'].includes(key));
    const fields = ['schemaVersion', ...keys];
    if (!value || typeof value !== 'object' || Array.isArray(value) || value.schemaVersion !== 1 ||
        !fields.filter(key => !optional.includes(key)).every(key => Object.hasOwn(value, key)) ||
        Object.keys(value).some(key => !fields.includes(key)) ||
        booleans.some(key => Object.hasOwn(value, key) && typeof value[key] !== 'boolean') ||
        typeof value.pathPattern !== 'string' || !value.pathPattern.trim() || value.pathPattern.length > 200 ||
        /[\u0000-\u001f\u007f]/.test(value.pathPattern)) throw new Error('Invalid scanner settings.');
    try { new RegExp(value.pathPattern, 'i'); } catch { throw new Error('Invalid scanner settings.'); }
    let queries = value.auxiliaryKeywordQueries;
    if (queries !== null) {
      if (!Array.isArray(queries) || queries.length > 128 || queries.some(query =>
        typeof query !== 'string' || !query.trim() || query.trim().length > 80 ||
        /[\u0000-\u001f\u007f]/.test(query))) throw new Error('Invalid scanner settings.');
      const seen = new Set();
      queries = queries.map(query => query.trim()).filter(query => {
        const key = query.toLocaleLowerCase('en-US');
        if (seen.has(key)) return false;
        seen.add(key); return true;
      });
    }
    const clean = { schemaVersion: 1,
      ...Object.fromEntries(booleans.filter(key => Object.hasOwn(value, key)).map(key => [key, value[key]])),
      auxiliaryKeywordQueries: queries, pathPattern: value.pathPattern };
    if (Object.hasOwn(value, 'auxiliaryPriorityQueries')) {
      const incoming = value.auxiliaryPriorityQueries;
      if (incoming !== null && (!Array.isArray(incoming) || incoming.length > 24 || incoming.some(query =>
        typeof query !== 'string' || !query.trim() || [...query.trim()].length > 80 ||
          /[\p{Cc}\p{Cs}]/u.test(query)))) throw new Error('Invalid scanner settings.');
      const seen = new Set();
      clean.auxiliaryPriorityQueries = incoming === null ? null : incoming.map(query => {
        const trimmed = query.trim();
        const folded = trimmed.toLocaleLowerCase('en-US');
        if (seen.has(folded)) throw new Error('Invalid scanner settings.');
        seen.add(folded);
        return trimmed;
      });
    }
    if (Object.hasOwn(value, 'auxiliaryPriorityRefreshMinutes')) {
      const interval = value.auxiliaryPriorityRefreshMinutes;
      if (!Number.isInteger(interval) || interval < 1 || interval > 120) throw new Error('Invalid scanner settings.');
      clean.auxiliaryPriorityRefreshMinutes = interval;
    }
    return clean;
  }
  function project(value = {}, overrides = {}) {
    return validate({ schemaVersion: 1, ...Object.fromEntries(keys.map(key =>
      [key, value[key] ?? defaults[key]])), ...overrides });
  }
  function changes(value, baseline) {
    return Object.fromEntries(Object.entries(value).filter(([key, entry]) =>
      key !== 'schemaVersion' && JSON.stringify(entry) !== JSON.stringify(baseline?.[key])));
  }
  return Object.freeze({ defaults, keys, validate, project, changes,
    relay: Object.freeze({ defaults: relayDefaults, validate: validateRelay }) });
})();
