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

// Search specific product names before broad catalogue terms. This extends the
// additional keyword tab without changing the legacy High Value sweep mode.
const HIGH_VALUE_ITEM_QUERIES = Object.freeze([
  'Smartwatch', 'Smart Watch', 'Fitness Tracker', 'Smart Ring',
  'Laptop', 'Tablet', 'Smartphone', 'Television', 'OLED TV', 'Camera', 'Drone',
  'Gaming Console', 'VR Headset', 'Graphics Card', 'Robot Vacuum',
  'Espresso Machine', 'Refrigerator', 'Washer', 'Dryer', 'Dishwasher',
  'Air Conditioner', 'Generator', 'Lawn Mower', 'E-Bike', 'Electric Scooter',
  'Mattress', 'Stroller', 'Car Seat', 'Vacuum', 'Monitor', 'Projector',
  'Pressure Washer', 'Power Tool Set', 'Luggage', 'Desk'
]);

// Reviewer catalogue wording can differ from the product title. Keep a small
// set of category searches and literal suffix trials ahead of the longer round.
const AUXILIARY_DISCOVERY_QUERIES = Object.freeze([
  'Large Appliance', 'Kitchen Appliance', 'Maker', 'Appliance', 'Cordless',
  'Large Appliance&', 'Kitchen Appliance&', 'Ride On', 'Sports Watch', 'AMOLED',
  'Large Appliance*', 'Kitchen Appliance*'
]);
const AUXILIARY_REPLACED_QUERIES = new Set([
  'Bamboo', 'Mesh', 'Vintage', 'Floral', 'Fold', 'Gold Decor', 'Hotel',
  'Stainless', 'Steel', 'Glass'
]);

// A separate short round revisits the observed high-value furniture tail.
// These are ordinary reviewer searches; each still reads every results page.
const PRIORITY_QUERIES = Object.freeze([
  'Furniture', 'Sofa', 'Couch', 'Sectional', 'Recliner', 'Mattress', 'Bed',
  'Desk', 'Dining Table', 'Dresser'
]);

function priorityQueries(value) {
  if (value == null) return [...PRIORITY_QUERIES];
  const fromText = typeof value === 'string';
  const values = fromText ? value.split(/\r?\n/).map(query => query.trim()).filter(Boolean) : value;
  if (!Array.isArray(values) || values.length > 24) {
    throw new Error('Use at most 24 priority searches.');
  }
  const seen = new Set();
  const queries = [];
  for (const entry of values) {
    if (typeof entry !== 'string' || (!fromText && /[\p{Cc}\p{Cs}]/u.test(entry))) {
      throw new Error('Every priority search must be text without control characters.');
    }
    const query = entry.trim().replace(/\s+/g, ' ');
    if (!query || /[\p{Cc}\p{Cs}]/u.test(query) ||
        [...(fromText ? query : entry.trim())].length > 80) {
      throw new Error('Each priority search must be 1 to 80 characters.');
    }
    const key = query.toLocaleLowerCase('en-US');
    if (!seen.has(key)) { seen.add(key); queries.push(query); }
  }
  return queries;
}

function auxiliaryQueries(value) {
  const values = value === undefined ? [...AUXILIARY_DISCOVERY_QUERIES, ...HIGH_VALUE_ITEM_QUERIES,
    'Furniture', 'Chair', 'Chairs', 'Couch', 'Sofa', 'Pet',
    ...HIGH_VALUE_QUERIES.filter(query => !AUXILIARY_REPLACED_QUERIES.has(query))] :
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
  if (queries.length > 128) throw new Error('Use at most 128 distinct keywords.');
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
    if (paginationFallback && ['q', 'queryNups'].some(key => url.searchParams.getAll(key).length > 1)) return false;
    // Regular monitoring owns a full catalogue. Searches/facets belong to the
    // user's browsing tab; unknown nonempty modifiers fail closed as filters.
    for (const [key, value] of url.searchParams) {
      // Automatic recovery owns only its preset searches, never a manual
      // search or filter in the primary tab.
      if (paginationFallback && ['q', 'queryNups'].includes(key) &&
          (!url.searchParams.get('q') || !url.searchParams.get('queryNups') ||
            url.searchParams.get('q') === url.searchParams.get('queryNups')) &&
          sweepQueries('high-value').some(query =>
            query.toLocaleLowerCase('en-US') === value.trim().toLocaleLowerCase('en-US'))) continue;
      if (key === 'page' && /^\d+$/.test(value) && Number(value) > 0) continue;
      // Walmart adds this neutral mode during ordinary unfiltered pagination.
      if (key === 'affinityOverride' && value === 'default') continue;
      if (value.trim() === '') continue;
      return false;
    }
    return true;
  } catch { return false; }
}
