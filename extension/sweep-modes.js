// Shared by the service worker, content script, and options page.
const HIGH_VALUE_QUERIES = Object.freeze([
  'Accessories', 'Appliance', 'Bag', 'Baking', 'Bamboo', 'Bath', 'Bathroom',
  'Bedding', 'Bedroom', 'Bulk', 'Car', 'Cat', 'Chairs', 'Cleaning', 'Clothes',
  'Cooking', 'Crafts', 'Dining', 'Dish', 'Dog', 'Electronics', 'Exercise',
  'Floral', 'Fold', 'Frame', 'Furniture', 'Gaming', 'Gifts', 'Glass',
  'Gold Decor', 'Home', 'Home Decor', 'Home Improvement', 'Hotel', 'Jewelry',
  'Kitchen', 'LED', 'Machine', 'Maker',
  'Mesh', 'Office/School Supplies', 'Organization', 'Pantry', 'Personal Care',
  'Pet', 'Playpen', 'Repair', 'Shade', 'Shelves', 'Shower', 'Stainless', 'Steel',
  'Storage', 'Supplies', 'Tech', 'Tools', 'Travel', 'Utensils', 'Vanity',
  'Vintage', 'Wireless', 'Wood'
]);
const ALPHABET_QUERIES = Object.freeze(Array.from(
  { length: 26 }, (_, index) => String.fromCharCode(97 + index)
));

function auxiliaryQueries(value) {
  const values = value === undefined ? ['Furniture', 'Chair', 'Chairs', 'Couch', 'Sofa', 'Pet', ...HIGH_VALUE_QUERIES] :
    typeof value === 'string' ? value.split(/\r?\n/) : value;
  if (!Array.isArray(values)) throw new Error('Keywords must be a list of search terms.');
  const seen = new Set();
  const queries = [];
  for (const entry of values) {
    if (typeof entry !== 'string') throw new Error('Every keyword must be text.');
    const query = entry.trim().replace(/\s+/g, ' ');
    if (!query) continue;
    if (query.length > 80) throw new Error('Each keyword must be 80 characters or fewer.');
    const key = query.toLocaleLowerCase('en-US');
    if (seen.has(key)) continue;
    seen.add(key);
    queries.push(query);
  }
  if (queries.length > 100) throw new Error('Use at most 100 distinct keywords.');
  return queries;
}

// Compare a managed assignment without broadening the ordinary homepage scope.
// Blank site defaults and a positive page number do not change a result set.
function auxiliaryScopeUrl(raw) {
  try {
    const url = new URL(raw);
    if (url.username || url.password) return null;
    for (const [key, value] of [...url.searchParams]) {
      if (key === 'page') {
        if (!/^\d+$/.test(value) || Number(value) < 1) return null;
        url.searchParams.delete(key);
      } else if (value.trim() === '' || (key === 'affinityOverride' && value === 'default')) {
        url.searchParams.delete(key);
      }
    }
    url.hash = '';
    url.searchParams.sort();
    return url.toString();
  } catch { return null; }
}

function normalizeSweepMode(value) {
  // Preserve settings saved by versions with the a-z checkbox.
  if (value === true || value === 'sweep') return 'sweep';
  return value === 'high-value' ? 'high-value' : 'none';
}

function sweepQueries(mode) {
  switch (normalizeSweepMode(mode)) {
    case 'sweep': return ALPHABET_QUERIES;
    case 'high-value': {
      // Keep one request per query even if this curated list is extended with
      // alternate capitalization or repeated terms later.
      const seen = new Set();
      return HIGH_VALUE_QUERIES.filter(query => {
        const key = query.trim().toLocaleLowerCase('en-US');
        if (!key || seen.has(key)) return false;
        seen.add(key);
        return true;
      });
    }
    default: return [];
  }
}

function sweepRecordMode(sweep) {
  return normalizeSweepMode(sweep.mode ?? sweep.alphabet);
}

function scanScopeAllowed(raw, mode = 'none', paginationFallback = false) {
  if (!paginationFallback && normalizeSweepMode(mode) !== 'none') return true;
  try {
    const url = new URL(raw);
    // Regular monitoring owns a full catalogue. Searches/facets belong to the
    // user's browsing tab; unknown nonempty modifiers fail closed as filters.
    for (const [key, value] of url.searchParams) {
      // Automatic recovery owns only its preset searches, never a manual
      // search or filter in the primary tab.
      if (paginationFallback && key === 'q' && sweepQueries('high-value')
          .some(query => query.toLocaleLowerCase('en-US') === value.trim().toLocaleLowerCase('en-US'))) continue;
      if (key === 'page' && /^\d+$/.test(value) && Number(value) > 0) continue;
      // Walmart adds this neutral mode during ordinary unfiltered pagination.
      if (key === 'affinityOverride' && value === 'default') continue;
      if (value.trim() === '') continue;
      return false;
    }
    return true;
  } catch { return false; }
}
