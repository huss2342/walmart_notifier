// Direct reads of the reviewer catalogue's own page data. No rendering or
// scrolling: the server-rendered page already carries its 40 items as data.
// One read at a time; a read starts 1.05-1.45 s after the previous start and
// at least 0.1 s after the previous read (and its upload) finished: about 41 a
// minute at Nov 19's read durations (see hot searches below). Back to 2.5-2.9 s
// for a day after any bot-check sign.
// Each step reads the most important target that is due; a lower rank always
// goes first:
//   0  sort=price_high page 1 every 10 s (first of all), every other page holding $80+ items
//      every 20 s (the owner's two most valuable reads); the min_price=79 list
//      (about 8 pages), each page holding an $80+ offer every 40 s, the rest
//      every 3 min; the 6 hot searches (page 1) every 60 s
//   1  the slow, fixed checks: sort=new page 1 (5 min) and member request
//      searches that had no products at their last read (30 min)
//   2  member request searches with products (page 1, every 4 min at best):
//      they take every read the ranks above leave
//   3  price_high pages down to $40 (one top-down pass), priority searches,
//      search pages 2-3
//   4  sort=new pages 2-4
//   5  department pages, deepest first, only in slots nothing above needs
// Within a rank, the target most overdue for its own interval goes first.
// Hot searches (Nov 19, owner-approved, about +6 reads a minute): in 72 hours,
// 46 new $80+ items (29%) showed in a keyword search minutes before the
// price_high and min_price=79 lists did (median 8.4 min; 19 never reached them).
// A reviewer search returns only the claimable items matching its word right
// now ("chair" 4 items, "dresser" none), so a new item is on its page 1 at once,
// while the 309-item catalogue lists update late. Searches ignore sort=price_high
// and min_price, so no search-only $80+ list exists. Watching chair, ktaxon,
// sofa, tablet, trampoline and rack every 60 s would have caught 29 of the 46.
// Those six are the seeds; then the set follows the yield. Every page-1 read of
// a member, priority or hot search scores the in-stock $80+ items new to that
// search (absent from its reads of the last 6 hours; its first read only sets
// the baseline). Scores halve every 3 days; seeds start at 2. The hot set is the
// top 6: a newcomer must score above the weakest x 1.25 + 0.5, so ties keep the
// current members and the set does not flap. A hot member or priority search is
// the same target read sooner, never twice; its later pages keep their cadence.
// Scores, the set and each search's recent $80+ items persist in storage.local
// (bounded). Hot reads are rank 0: they share with the $80+ pages by overdue
// ratio and price_high page 1 still goes first. Their reads are new budget. The
// rate was limited both by the spacing (1.4-1.8 s) and by each read's own
// duration (median 1.2 s with its upload) plus a 0.25 s pause, so lowering the
// spacing alone could add at most ~4 reads a minute; both went down. On the
// measured durations that is 34.7 -> 40.9 reads a minute, and every other rank
// keeps its cadence (data/hot-searches-20261119/cadence_sim.mjs). The rate
// follows Walmart's response time: 30% faster answers would give ~46 a minute
// (36.8 before). During the gentler pace after a bot-check sign no search is hot.
// Each search read logs a 32-bit FNV-1a hash of its normalised query
// (searchHash), whether it was a hot read (hot), its $80+ count and its
// upload's requestId; never the query text. That makes per-search yield auditable.
// Nov 19 (owner-approved, same read load): sort=new page 1 went from 30 s to
// 5 min (it found no $80+ item first in the Nov 16-19 audit) and empty searches
// from 12 to 30 min (a third of search reads were empty); the ~2 reads a minute
// this frees go to searches with products. The read rate is set by the spacing
// and the reads' own durations (every step reads one due target, departments
// fill the rest), so intervals only move reads between targets; they never add
// any (simulated on the Nov 19 workload: 34.68 reads a minute before and after).
// Walmart's price order is approximate: each page ends with a few cheaper items
// (page 1 ending at $64 while page 2 runs $142-$91), so a page counts by
// whether it holds any item above a floor, never by its last item.
// Some $80+ offers are variants Walmart sorts by a cheaper sibling, so
// price_high never shows them near the top (school supply boxes, $119.99, in
// stock for hours). Walmart's min_price filter keeps listings with a version at
// or above it, whatever the offer is valued at. On Nov 12 min_price=80 held 71
// of the 72 $80+ offers seen anywhere (262 items, 7 pages), both hidden ones
// included; min_price=79 held all 72 for one page more (287 items, 8 pages).
// Nov 10-12: price_high pages 1-4 found 70% of all $80+ items first, while
// sort=new found nothing worth $10 or more, so it yields to everything else.
// The fetch runs inside an open reviewer tab, so it is an ordinary same-origin
// page request. Any bot-check sign stops direct reads for a back-off period;
// the load-and-scroll tabs keep running throughout, so nothing depends on this.
// Freshness measurement (Nov 19, 2.32.7, owner-approved for ~2 days): every read
// goes to the page snapshot log (lane, page, sort, min_price, catId, search hash,
// outcome and each product's id, position, price and stock; no extra reads).
// When a member, priority or hot search shows an in-stock $80+ item that no
// catalogue list (price_high, min_price=79, sort=new) showed in the last 30 min,
// a freshness probe starts: at once it reads the min_price=79 page whose price
// range should hold the item (and its neighbour when the price sits near a page
// edge), sort=new page 1, sort=new&min_price=79 page 1 and the item's department
// page 1 when the page data names a known department. Then the predicted page is
// checked every 60 s until the item shows or 20 min pass; the normal 40 s reads
// of those pages count, so an extra read happens only when no normal read came.
// Probe reads queue behind price_high page 1 only, one at a time under the same
// spacing and back-off; never during the gentler day after a bot-check sign.
// Caps (persisted): 2 probes at once, 8 starts an hour, 100 extra reads a day.
// Probe reads are never ingested. data/freshness-20261119/analyze.py reads the log.
const ReviewerFastReads = (() => {
  const HOME = 'https://www.walmart.com/reviews/claim-product';
  const ALARM = 'fastReads';
  const STATE_KEY = 'reviewerFastReads';
  const STEP_MS = 100; // Pause after each read (250 until Nov 19).
  const MIN_SPACING_MS = 1_050; // Between read starts, plus up to SPACING_JITTER_MS (1_400 until Nov 19).
  const SPACING_JITTER_MS = 400; // Irregular starts, not a metronome.
  const SAFE_SPACING_MS = 2_500; // The old rate, kept for a day after any bot-check sign.
  const SAFE_AFTER_BLOCK_MS = 24 * 60 * 60_000;
  // Resend an unchanged page this often so newly added requests still match.
  const RESEND_MS = 5 * 60_000;
  const BACKOFF_MS = [5 * 60_000]; // Flat: direct reads only; the tabs and the bot-check bypasser are unaffected.
  // new80 (sort=new&min_price=79) is read by freshness probes only.
  const SORTS = Object.freeze({ newest: 'new', value: 'price_high', value80: 'price_high', requests: 'new',
    priority: 'new', new80: 'new' });
  const SEARCH_PAGES = 3;
  const VALUE_FLOOR = 80; // price_high pages that can still hold $80+ items: the most valuable reads.
  const VALUE_DEEP_FLOOR = 40; // The chain continues, more slowly, down to $40.
  const VALUE_MAX_PAGES = 12;
  const VALUE80_MAX_PAGES = 12; // The min_price=79 list ran to 8 pages on Nov 12.
  const VALUE80_MIN_PRICE = 79; // $80+ offers can sit on listings Walmart prices at $79.xx.
  const DEPARTMENT_PAGES = 25; // Walmart's own page ceiling.
  const DEPARTMENT_FAILURES = 3; // Unreadable department pages in a row pause that filler.
  const DEPARTMENT_PAUSE_MS = 30 * 60_000;
  // A search read empty stays proven empty until just after its next 30-minute
  // read, so the requests tab skips it rather than loading it instead.
  const EMPTY_PROOF_MS = 35 * 60_000;
  // A page whose read held back a restock (first in-stock read of an offer that
  // was out) is read again this soon, so the restock is confirmed or rejected
  // about 10 s after it was first seen rather than at the page's usual interval.
  const RECHECK_MS = 10_000;
  const PROBE_TIMEOUT_MS = 10_000;
  const MAX_PROBES = 8;
  // Owner-listed research reads wait behind the rank-0 reads ($80+ pages, hot searches) only.
  const RESEARCH_TIMEOUT_MS = 120_000;
  const MAX_RESEARCH = 4;
  const SECOND = 1_000;
  const MINUTE = 60_000;
  // Hot searches (see the header): seeds, set size, scoring and storage bounds.
  const HOT_SEEDS = Object.freeze(['chair', 'ktaxon', 'sofa', 'tablet', 'trampoline', 'rack']);
  const HOT_KEY = 'reviewerHotSearches';
  const HOT_SIZE = 6;
  const HOT_HALF_LIFE_MS = 3 * 24 * 60 * MINUTE;
  const HOT_PRIOR = 2; // A seed starts as if it had surfaced two new $80+ items.
  const HOT_SWAP_RATIO = 1.25; // A newcomer must score above the weakest hot search x 1.25 + 0.5,
  const HOT_SWAP_MARGIN = 0.5; // and at least 0.5 to fill a free place.
  const HOT_REMEMBER_MS = 6 * 60 * MINUTE; // An item a search showed in this window is not new to it.
  const HOT_MEMORY_ITEMS = 16; // $80+ item ids remembered per search.
  const HOT_MAX_SCORES = 400; // Scored searches kept (lowest dropped first).
  const HOT_MIN_SCORE = 0.01; // A forgotten search's score below this is dropped.
  const HOT_SAVE_MS = MINUTE; // Memory-only changes are saved at most this often.
  const INTERVALS = Object.freeze({
    value: Object.freeze([10 * SECOND]), valueHigh: 20 * SECOND, valueDeep: 4 * MINUTE,
    value80: 40 * SECOND, value80Idle: 3 * MINUTE, hot: MINUTE,
    // Searches with products aim at 4 min and get whatever reads are left over;
    // searches empty at their last read (member or priority) wait 30 min.
    member: 4 * MINUTE, memberEmpty: 30 * MINUTE, priority: 12 * MINUTE,
    // Later pages of a search: members 12 min, broad priority searches 24 min
    // (pages 2-3 found no item worth $40 or more on Nov 10-12).
    memberPages: 12 * MINUTE, priorityPages: 24 * MINUTE,
    newest: Object.freeze([5 * MINUTE, 15 * MINUTE, 15 * MINUTE, 15 * MINUTE])
  });
  // Ranks; a lower rank always goes first (see the list above).
  const RANK = Object.freeze({ valueTop: -1, value: 0, slow: 1, member: 2, deep: 3, newestDeep: 4, department: 5 });
  // About 2 slow checks a minute are due on average (62 empty searches / 30 min).
  const SLOW_SPACING_MS = 10_000;
  const CAT_ID = /^\d{1,16}(?:_\d{1,16})*$/;
  const ITEM_ID = /^ip-\d{6,20}$/;
  // Freshness probes (see the header): trigger window, caps and bounds.
  const FRESH = Object.freeze({
    KEY: 'reviewerFreshnessProbes',
    UNSEEN_MS: 30 * MINUTE, // No catalogue list showed the item for this long.
    MAX_ACTIVE: 2,
    MAX_PER_HOUR: 8,
    MAX_READS_PER_DAY: 100, // Extra reads in any 24 hours, persisted.
    WATCH_MS: 20 * MINUTE,
    REREAD_MS: MINUTE,
    REPROBE_MS: 24 * 60 * MINUTE, // An item is probed (or skipped) once a day at most.
    BOUNDARY: 0.05, // Within 5% (at least $5) of a page's price edge: check that neighbour too.
    MAX_SEEN: 6_000, // Catalogue item ids remembered for the trigger.
    MAX_PROBED: 400,
    MAX_CHECKS: 80
  });
  const CATALOGUE = new Set(['value', 'value80', 'newest', 'new80']);

  function urlFor(lane, page, query = '', catId = '') {
    const url = new URL(HOME);
    if (catId) url.searchParams.set('catId', catId);
    if (query) {
      url.searchParams.set('q', query);
      url.searchParams.set('queryNups', query);
    }
    if (!catId) url.searchParams.set('sort', SORTS[lane]);
    if (lane === 'value80' || lane === 'new80') url.searchParams.set('min_price', String(VALUE80_MIN_PRICE));
    if (page > 1) url.searchParams.set('page', String(page));
    return url.toString();
  }

  const searchKey = query => query.trim().replace(/\s+/g, ' ').toLocaleLowerCase('en-US');

  // 32-bit FNV-1a over the UTF-8 bytes of a normalised query: a short, stable
  // search identity for the diagnostics, never the query itself. In Python:
  //   h = 0x811c9dc5; for b in key.encode(): h = ((h ^ b) * 0x01000193) & 0xffffffff
  function searchHash(key) {
    let hash = 0x811c9dc5;
    const mix = byte => { hash = Math.imul(hash ^ byte, 0x01000193) >>> 0; };
    for (const char of key) {
      const code = char.codePointAt(0);
      if (code < 0x80) mix(code);
      else if (code < 0x800) { mix(0xc0 | code >> 6); mix(0x80 | code & 63); }
      else if (code < 0x10000) { mix(0xe0 | code >> 12); mix(0x80 | code >> 6 & 63); mix(0x80 | code & 63); }
      else { mix(0xf0 | code >> 18); mix(0x80 | code >> 12 & 63); mix(0x80 | code >> 6 & 63); mix(0x80 | code & 63); }
    }
    return hash;
  }

  // The search identity of a raw query (what the snapshot log records for a tab's q=).
  const queryHash = query => typeof query === 'string' && query.trim() ? searchHash(searchKey(query)) : null;

  // Product rows of a read for the snapshot log: [usItemId, position, free price
  // in cents or null, stock i/o/x/n, extra?] from the reader (content.js
  // snapshotRow). A reply without them (older reader) yields its free offers.
  function rowsOf(reply) {
    if (Array.isArray(reply?.snapshot)) return reply.snapshot.slice(0, 200);
    return (Array.isArray(reply?.items) ? reply.items : []).slice(0, 200).map((item, index) => {
      const id = /^ip-(\d{6,20})$/.exec(typeof item?.item_id === 'string' ? item.item_id : '')?.[1];
      const cents = Number.isFinite(item.value_usd) ? Math.round(item.value_usd * 100) : null;
      return id ? [id, index + 1, cents !== null && cents >= 0 && cents <= 1e9 ? cents : null,
        item.available === true ? 'i' : item.available === false ? 'o' : 'n'] : null;
    }).filter(Boolean);
  }

  // The price span of a min_price=79 page without its outliers: Walmart's order
  // is approximate (a stray high item first, cheaper items last).
  function coreRange(items) {
    const prices = items.map(item => item?.value_usd).filter(Number.isFinite);
    if (!prices.length) return null;
    const core = prices.length >= 6
      ? prices.slice(Math.floor(prices.length * 0.1), Math.ceil(prices.length * 0.85)) : prices;
    return { low: Math.min(...core), high: Math.max(...core) };
  }

  function create(chrome, { tabIds, send, paused, diagnostic, queries = async () => [], departments = async () => [],
    onBlocked = async () => {}, mustSend = () => false, setTimer = setTimeout, clearTimer = clearTimeout,
    random = Math.random, hotSeeds = HOT_SEEDS, snapshot = () => {} }) {
    let timer = null;
    let running = false;
    let lastStartAt = 0;
    let lastSlowAt = -Infinity; // When the last slow check (rank 1) started.
    let gap = 0; // Required time from the last read start to the next one.
    const lastRead = new Map(); // Target key -> when its last read started.
    const lastSent = new Map();
    const searches = new Map(); // Search key -> { totalPages, empty, at } from its last page-1 read.
    // Deepest price_high page of the main $80+ block, and the last page the
    // slower pass reads (the first one without a $40+ item ends it).
    const value = { high: 4, deep: 4 };
    // The min_price=79 list: its page count, and the pages holding an $80+ offer
    // (in stock or awaiting a restock) at their last read.
    const value80 = { pages: 8, hot: new Set([1, 2, 3, 4, 5, 6, 7, 8]) };
    // Pages still to read in the current top-down pass of the pages under $80.
    let valuePass = [];
    let departmentQueue = [];
    let departmentCursor = -1;
    let departmentFailures = 0;
    let departmentPausedUntil = 0;
    const probes = [];
    const researchQueue = [];
    // Hot searches: scores (key -> { score, at }, decaying from `at`), each
    // search's recent $80+ items (key -> Map id -> last seen), the hot set (keys,
    // in the order they joined) and when the state was last saved.
    const hot = { loading: null, scores: new Map(), memory: new Map(), set: [], dirty: false, savedAt: 0,
      candidates: null };
    const seeds = (Array.isArray(hotSeeds) ? hotSeeds : []).filter(query => typeof query === 'string' && query.trim());
    // Freshness probes: when a catalogue list last showed each item, each
    // min_price=79 page's core price span and last read, the running probes and
    // the persisted caps (extra-read and start times of the last day, items probed).
    const catalogueSeen = new Map();
    const value80Core = new Map();
    const value80ReadAt = new Map();
    const fresh = { loading: null, reads: [], starts: [], probed: new Map(), active: [], sequence: 0 };

    const readState = async () => {
      try { return (await chrome.storage.local.get(STATE_KEY))[STATE_KEY] || { blocks: 0, until: 0 }; }
      catch { return { blocks: 0, until: 0 }; }
    };
    const writeState = value => chrome.storage.local.set({ [STATE_KEY]: value }).catch(() => {});
    const log = details => { try { diagnostic(details); } catch { /* Logging never stops reads. */ } };

    function valueTargets() {
      // Page 1 has a rank of its own: nothing, not even a batch of new targets, goes ahead of it.
      const targets = Array.from({ length: value.high }, (_, index) => ({ key: `value:${index + 1}`,
        lane: 'value', page: index + 1, rank: index ? RANK.value : RANK.valueTop,
        interval: INTERVALS.value[index] ?? INTERVALS.valueHigh }));
      for (let page = 1; page <= value80.pages; page += 1) {
        targets.push({ key: `value80:${page}`, lane: 'value80', page, rank: RANK.value,
          interval: value80.hot.has(page) ? INTERVALS.value80 : INTERVALS.value80Idle });
      }
      // The pages under $80 are read as one pass, top-down and back to back. A
      // newly listed item only pushes the ones below it further down, onto a
      // page this pass has not read yet, so a shifting list cannot hide them.
      if (value.deep > value.high) {
        targets.push({ key: 'value:pass', lane: 'value', page: value.high + 1, rank: RANK.deep,
          interval: INTERVALS.valueDeep });
      }
      return targets;
    }

    const newestTargets = () => INTERVALS.newest.map((interval, index) =>
      ({ key: `newest:${index + 1}`, lane: 'newest', page: index + 1, rank: index ? RANK.newestDeep : RANK.slow,
        interval }));

    const decayed = (entry, now) => entry
      ? entry.score * 2 ** (-Math.max(0, now - entry.at) / HOT_HALF_LIFE_MS) : 0;

    // Scores, item memory and the hot set survive reloads; anything malformed is skipped.
    function loadHot() {
      hot.loading ||= (async () => {
        let stored = null;
        try { stored = (await chrome.storage.local.get(HOT_KEY))[HOT_KEY]; } catch { stored = null; }
        const now = Date.now();
        const key = value => typeof value === 'string' && value.length > 0 && value.length <= 200;
        if (stored && typeof stored === 'object' && stored.v === 1) {
          for (const row of Array.isArray(stored.scores) ? stored.scores.slice(0, HOT_MAX_SCORES) : []) {
            if (Array.isArray(row) && key(row[0]) && Number.isFinite(row[1]) && row[1] >= 0 && row[1] <= 1e6 &&
                Number.isFinite(row[2])) hot.scores.set(row[0], { score: row[1], at: Math.min(row[2], now) });
          }
          for (const row of Array.isArray(stored.memory) ? stored.memory.slice(0, HOT_MAX_SCORES) : []) {
            if (!Array.isArray(row) || !key(row[0]) || !Array.isArray(row[1])) continue;
            hot.memory.set(row[0], new Map(row[1].filter(entry => Array.isArray(entry) &&
              typeof entry[0] === 'string' && /^ip-\d{1,20}$/.test(entry[0]) && Number.isFinite(entry[1]) &&
              now - entry[1] <= HOT_REMEMBER_MS).slice(-HOT_MEMORY_ITEMS).map(entry => [entry[0], entry[1]])));
          }
          hot.set = [...new Set((Array.isArray(stored.hot) ? stored.hot : []).filter(key))].slice(0, HOT_SIZE);
        }
        // A seed without a score starts at the prior, once: a seed that then
        // finds nothing keeps its decaying score and can lose its place.
        for (const query of seeds) {
          const seed = searchKey(query);
          if (!hot.scores.has(seed)) {
            hot.scores.set(seed, { score: HOT_PRIOR, at: now });
            hot.dirty = true;
          }
        }
      })();
      return hot.loading;
    }

    // Saved at most once a minute, or at once when a score or the set changed.
    // Searches no longer configured keep only a score, until it decays away.
    function saveHot(now, force = false) {
      if (!hot.dirty || (!force && now - hot.savedAt < HOT_SAVE_MS)) return;
      const candidates = hot.candidates || new Map();
      const keep = key => candidates.has(key) || hot.set.includes(key);
      for (const key of hot.memory.keys()) if (!keep(key)) hot.memory.delete(key);
      for (const [key, entry] of hot.scores) if (!keep(key) && decayed(entry, now) < HOT_MIN_SCORE) hot.scores.delete(key);
      if (hot.scores.size > HOT_MAX_SCORES) {
        const order = [...hot.scores].sort((a, b) => Number(keep(a[0])) - Number(keep(b[0])) ||
          decayed(a[1], now) - decayed(b[1], now));
        for (const [key] of order.slice(0, hot.scores.size - HOT_MAX_SCORES)) hot.scores.delete(key);
      }
      hot.dirty = false;
      hot.savedAt = now;
      const value = { v: 1, hot: [...hot.set],
        scores: [...hot.scores].map(([key, entry]) => [key, Math.round(entry.score * 10_000) / 10_000, entry.at]),
        memory: [...hot.memory].slice(-HOT_MAX_SCORES).map(([key, items]) => [key, [...items]]) };
      Promise.resolve().then(() => chrome.storage.local.set({ [HOT_KEY]: value })).catch(() => {});
    }

    // One page-1 read of a search: counts the in-stock $80+ items new to it (not
    // in its reads of the last HOT_REMEMBER_MS). Its first read only sets the baseline.
    function scoreSearch(key, items, now) {
      const before = hot.memory.get(key);
      const memory = new Map();
      if (before) for (const [id, at] of before) if (now - at <= HOT_REMEMBER_MS) memory.set(id, at);
      let fresh = 0;
      for (const item of items) {
        if (typeof item?.item_id !== 'string' || !(item.value_usd >= VALUE_FLOOR)) continue;
        if (before && item.available !== false && !memory.has(item.item_id)) fresh += 1;
        memory.delete(item.item_id);
        memory.set(item.item_id, now);
      }
      while (memory.size > HOT_MEMORY_ITEMS) memory.delete(memory.keys().next().value);
      hot.memory.set(key, memory);
      hot.dirty = true;
      if (fresh) {
        hot.scores.set(key, { score: decayed(hot.scores.get(key), now) + fresh, at: now });
        if (hot.candidates) reviewHot(hot.candidates, now); // The only time the order can change.
      }
      saveHot(now, fresh > 0);
    }

    // The hot set: the top HOT_SIZE candidates by score. A newcomer takes the
    // weakest place only when it scores above HOT_SWAP_RATIO x that + HOT_SWAP_MARGIN
    // (ties keep the current members); a free place needs HOT_SWAP_MARGIN. All
    // scores decay alike, so the order changes only when a search finds something.
    function reviewHot(candidates, now) {
      hot.candidates = candidates;
      const score = key => decayed(hot.scores.get(key), now);
      const kept = hot.set.filter(key => candidates.has(key));
      let changed = kept.length !== hot.set.length;
      const others = [];
      for (const key of candidates.keys()) {
        if (!hot.scores.has(key) || kept.includes(key)) continue;
        const value = score(key);
        if (value >= HOT_SWAP_MARGIN) others.push({ key, value });
      }
      others.sort((a, b) => b.value - a.value); // Stable: ties keep the candidate order.
      for (const { key, value } of others) {
        if (kept.length < HOT_SIZE) {
          kept.push(key);
          changed = true;
          continue;
        }
        let weakest = 0;
        let low = Infinity;
        kept.forEach((member, index) => {
          const current = score(member);
          if (current <= low) { low = current; weakest = index; } // On a tie, the latest to join.
        });
        if (!(value > low * HOT_SWAP_RATIO + HOT_SWAP_MARGIN)) break; // Later ones score lower still.
        kept.splice(weakest, 1);
        kept.push(key);
        changed = true;
      }
      if (changed) {
        hot.set = kept;
        hot.dirty = true;
        saveHot(now, true);
      }
      return new Set(kept);
    }

    // Member searches first: they become personal DMs. A query in both lists
    // counts as a member search. A hot seed in neither list is read only while
    // hot, page 1, as a priority search numbered after the owner's list. A hot
    // search keeps its target (and so its last read): it is read sooner, never twice.
    // While the gentler pace after a bot-check sign is on, no search is hot.
    async function searchTargets(now, slow = false) {
      await loadHot();
      let lists;
      try { lists = await queries(); } catch { lists = []; }
      const members = Array.isArray(lists) ? lists : Array.isArray(lists?.members) ? lists.members : [];
      const priority = !Array.isArray(lists) && Array.isArray(lists?.priority) ? lists.priority : [];
      const candidates = new Map();
      const add = (query, index, lane, seed = false) => {
        if (typeof query !== 'string' || !query.trim()) return;
        const key = searchKey(query);
        if (!candidates.has(key)) candidates.set(key, { query, index, lane, seed });
      };
      members.forEach((query, index) => add(query, index + 1, 'requests'));
      priority.forEach((query, index) => add(query, index + 1, 'priority'));
      seeds.forEach((query, index) => add(query, priority.length + index + 1, 'priority', true));
      const hotKeys = reviewHot(candidates, now);
      const targets = [];
      for (const [key, { query, index, lane, seed }] of candidates) {
        const info = searches.get(key);
        const member = lane === 'requests';
        if (!slow && hotKeys.has(key)) {
          targets.push({ key: `search:${key}:1`, lane, page: 1, query, index, hot: true, rank: RANK.value,
            interval: INTERVALS.hot });
        } else if (!seed) {
          // An empty member search keeps its slow 30-minute check ahead of the
          // searches with products, which share every read left after it.
          targets.push({ key: `search:${key}:1`, lane, page: 1, query, index,
            rank: !member ? RANK.deep : info?.empty ? RANK.slow : RANK.member,
            interval: info?.empty ? INTERVALS.memberEmpty : member ? INTERVALS.member : INTERVALS.priority });
        }
        if (seed) continue;
        for (let page = 2; page <= Math.min(info?.totalPages || 1, SEARCH_PAGES); page += 1) {
          targets.push({ key: `search:${key}:${page}`, lane, page, query, index, rank: RANK.deep,
            interval: member ? INTERVALS.memberPages : INTERVALS.priorityPages });
        }
      }
      // Forget searches that are no longer configured.
      for (const key of searches.keys()) if (!candidates.has(key)) searches.delete(key);
      const current = new Set(targets.map(target => target.key));
      for (const key of lastRead.keys()) if (key.startsWith('search:') && !current.has(key)) lastRead.delete(key);
      return targets;
    }

    async function departmentTarget(now) {
      if (now < departmentPausedUntil) return null;
      if (!departmentQueue.length) {
        let list = [];
        try { list = await departments(); } catch { list = []; }
        list = Array.isArray(list) ? list.filter(entry => CAT_ID.test(entry?.catId || '')) : [];
        if (!list.length) return null;
        departmentCursor = (departmentCursor + 1) % list.length;
        const { catId, index } = list[departmentCursor];
        // Page 1 first: it reports the department's page count and size.
        departmentQueue = [{ key: `department:${catId}:1`, lane: 'departments', page: 1, catId, rank: RANK.department,
          index: Number.isSafeInteger(index) && index > 0 ? index : departmentCursor + 1 }];
      }
      return departmentQueue.shift();
    }

    async function nextTarget(now, slow = false) {
      freshTick(now, slow);
      // A target never read yet counts as twice due: first in line, without
      // letting a large new batch shut out a short-interval target for minutes.
      const due = [...valueTargets(), ...await searchTargets(now, slow), ...newestTargets()]
        .map(target => ({ ...target, overdue: lastRead.has(target.key)
          ? (now - lastRead.get(target.key)) / target.interval : 2 }))
        .filter(target => target.overdue >= 1)
        .sort((a, b) => a.rank - b.rank || b.overdue - a.overdue);
      const highDue = due[0] && due[0].rank <= RANK.value;
      // A freshness probe's reads wait for a due price_high page 1 only.
      if (!slow && fresh.active.length && due[0]?.rank !== RANK.valueTop) {
        const read = freshNext(now);
        if (read) return read;
      }
      if (researchQueue.length && !highDue) return researchQueue.shift().target;
      // A started pass continues before anything except the $80+ pages.
      if (valuePass.length && !highDue) {
        const page = valuePass.shift();
        return { key: `value:${page}`, lane: 'value', page, rank: RANK.deep, pass: true };
      }
      // Slow checks that fall due together (every empty search read in one
      // pass) are spread SLOW_SPACING_MS apart instead of taking every free
      // read for minutes; in between, the searches with products keep going.
      if (due[0]?.rank === RANK.slow && now - lastSlowAt < SLOW_SPACING_MS) {
        return due.find(target => target.rank > RANK.slow) || due[0];
      }
      return due[0] || departmentTarget(now);
    }

    // A page with any $80+ item belongs to the fast zone, and when at least a
    // quarter of its items are $80+ the next page joins it too; a page without
    // any leaves it. A page with any $40+ item lets the slower pass go one further.
    function learnValue(page, reply) {
      const limit = Math.min(VALUE_MAX_PAGES,
        Number.isInteger(reply.totalPages) && reply.totalPages > 0 ? reply.totalPages : VALUE_MAX_PAGES);
      const values = reply.items.map(item => item.value_usd).filter(Number.isFinite);
      if (reply.items.length && !values.length) return; // No prices to judge by.
      const holds = floor => values.some(price => price >= floor);
      const high = values.filter(price => price >= VALUE_FLOOR).length;
      // A lone $80+ offer far below the block does not drag the pages between
      // into the fast zone: the min_price=80 list carries such offers.
      if (page <= value.high + 1) {
        if (high && high * 4 >= values.length) value.high = Math.min(limit, Math.max(value.high, page + 1));
        else if (high) value.high = Math.min(limit, Math.max(value.high, page));
        else if (page <= value.high) value.high = Math.max(1, page - 1);
      }
      if (holds(VALUE_DEEP_FLOOR)) value.deep = Math.min(limit, Math.max(value.deep, page + 1));
      else if (page <= value.deep) value.deep = page;
      value.deep = Math.max(value.deep, value.high);
    }

    function learnValue80(page, reply) {
      if (Number.isInteger(reply.totalPages) && reply.totalPages > 0) {
        value80.pages = Math.min(VALUE80_MAX_PAGES, reply.totalPages);
      }
      if (reply.items.some(item => item.value_usd >= VALUE_FLOOR)) value80.hot.add(page);
      else value80.hot.delete(page);
      for (const hot of value80.hot) if (hot > value80.pages) value80.hot.delete(hot);
    }

    function learnDepartment(target, reply, probe) {
      if (probe) return;
      departmentFailures = 0;
      if (target.page !== 1 || !Number.isInteger(reply.totalPages) || reply.totalPages < 2) return;
      // Then the rest of the department, deepest first: deep pages have held the
      // most first sightings.
      const pages = Math.min(reply.totalPages, DEPARTMENT_PAGES);
      departmentQueue = Array.from({ length: pages - 1 }, (_, i) => pages - i)
        .map(page => ({ ...target, key: `department:${target.catId}:${page}`, page }));
    }

    function learn(target, reply, probe) {
      if (target.lane === 'value') learnValue(target.page, reply);
      else if (target.lane === 'value80') learnValue80(target.page, reply);
      else if (target.lane === 'departments' && target.catId) learnDepartment(target, reply, probe);
      else if (target.query && target.page === 1) {
        const products = Number.isSafeInteger(reply.products) ? reply.products : reply.items.length;
        searches.set(searchKey(target.query), { at: Date.now(), empty: products === 0,
          totalPages: Number.isInteger(reply.totalPages) && reply.totalPages > 0 ? reply.totalPages : 1 });
        // Probes are the scan coordinator's emptiness checks: they do not score.
        if (!probe) scoreSearch(searchKey(target.query), reply.items, Date.now());
      }
    }

    // --- freshness probes (measurement; see the header) -------------------------

    const emit = record => { try { snapshot(record); } catch { /* Measurement never stops reads. */ } };

    // Caps survive reloads: extra-read and start times of the last day, items probed.
    function loadFresh() {
      fresh.loading ||= (async () => {
        let stored = null;
        try { stored = (await chrome.storage.local.get(FRESH.KEY))[FRESH.KEY]; } catch { stored = null; }
        const now = Date.now();
        const recent = list => (Array.isArray(list) ? list : [])
          .filter(at => Number.isFinite(at) && at <= now && now - at < 24 * 60 * MINUTE).slice(-500);
        if (stored && typeof stored === 'object' && stored.v === 1) {
          fresh.reads = recent(stored.reads);
          fresh.starts = recent(stored.starts);
          for (const row of Array.isArray(stored.probed) ? stored.probed.slice(-FRESH.MAX_PROBED) : []) {
            if (Array.isArray(row) && typeof row[0] === 'string' && ITEM_ID.test(row[0]) && Number.isFinite(row[1]) &&
                now - row[1] < FRESH.REPROBE_MS) fresh.probed.set(row[0], row[1]);
          }
        }
      })();
      return fresh.loading;
    }

    function saveFresh(now) {
      const day = at => now - at < 24 * 60 * MINUTE;
      fresh.reads = fresh.reads.filter(day).slice(-500);
      fresh.starts = fresh.starts.filter(day).slice(-500);
      for (const [id, at] of fresh.probed) if (now - at >= FRESH.REPROBE_MS) fresh.probed.delete(id);
      while (fresh.probed.size > FRESH.MAX_PROBED) fresh.probed.delete(fresh.probed.keys().next().value);
      const value = { v: 1, reads: fresh.reads, starts: fresh.starts, probed: [...fresh.probed] };
      Promise.resolve().then(() => chrome.storage.local.set({ [FRESH.KEY]: value })).catch(() => {});
    }

    // The trigger needs a full view of the $80+ list: every min_price=79 page
    // read since the worker started, so a restart does not probe listed items.
    function freshWarm() {
      for (let page = 1; page <= value80.pages; page += 1) if (!value80ReadAt.has(page)) return false;
      return true;
    }

    // The min_price=79 page whose core price span holds the price (else the
    // nearest), plus a neighbour when the price is near that page's edge.
    function freshPages(price) {
      let best = null;
      let distance = Infinity;
      for (let page = 1; page <= value80.pages; page += 1) {
        const core = value80Core.get(page);
        if (!core) continue;
        const gap = price > core.high ? price - core.high : price < core.low ? core.low - price : 0;
        if (gap < distance) { best = { page, ...core }; distance = gap; }
      }
      if (!best) return { predicted: 1, pages: [1] };
      const pages = new Set([best.page]);
      const margin = Math.max(5, price * FRESH.BOUNDARY);
      if (best.page > 1 && best.high - price <= margin) pages.add(best.page - 1);
      if (best.page < value80.pages && price - best.low <= margin) pages.add(best.page + 1);
      return { predicted: best.page, pages: [...pages].sort((a, b) => a - b) };
    }

    // The longest known department catId on the item's category path, if any.
    async function freshDepartment(category) {
      if (typeof category !== 'string' || !/^[0-9:_]{1,200}$/.test(category)) return '';
      const path = category.split(/[:_]/).filter(Boolean);
      if (path[0] === '0') path.shift();
      let list = [];
      try { list = await departments(); } catch { list = []; }
      let best = '';
      let depth = 0;
      for (const entry of Array.isArray(list) ? list : []) {
        const catId = typeof entry?.catId === 'string' && CAT_ID.test(entry.catId) ? entry.catId : '';
        const parts = catId ? catId.split('_') : [];
        if (parts.length > depth && parts.length <= path.length && parts.every((part, i) => part === path[i])) {
          best = catId;
          depth = parts.length;
        }
      }
      return best;
    }

    function freshTarget(probe, lane, page, extra = {}) {
      probe.sequence += 1;
      return { key: `fresh:${probe.id}:${probe.sequence}`, lane, page, fresh: probe, ...extra };
    }

    // A search page-1..3 read: an in-stock $80+ item no catalogue list showed in
    // the last 30 minutes starts a probe, within the caps. Never in the safe period.
    async function freshTrigger(target, reply, state, at) {
      if (at < (Number(state.slowUntil) || 0) || !freshWarm()) return;
      const rows = new Map(rowsOf(reply).map(row => [`ip-${row[0]}`, row]));
      for (const item of reply.items) {
        const id = typeof item?.item_id === 'string' && ITEM_ID.test(item.item_id) ? item.item_id : '';
        if (!id || item.available !== true || !(item.value_usd >= VALUE_FLOOR)) continue;
        const seenAt = catalogueSeen.get(id);
        if ((seenAt !== undefined && at - seenAt <= FRESH.UNSEEN_MS) || fresh.active.some(probe => probe.item === id)) {
          continue;
        }
        await loadFresh();
        const now = Date.now();
        if (fresh.probed.has(id) && now - fresh.probed.get(id) < FRESH.REPROBE_MS) continue;
        const row = rows.get(id);
        const base = { kind: 'probe', itemId: id, valueCents: Math.round(item.value_usd * 100), at: now };
        const trigger = { lane: target.lane, page: target.page,
          ...(target.query ? { searchHash: searchHash(searchKey(target.query)) } : {}),
          ...(target.hot ? { hot: true } : {}), ...(Number.isSafeInteger(row?.[1]) ? { position: row[1] } : {}) };
        const { predicted, pages } = freshPages(item.value_usd);
        const category = typeof row?.[4]?.c === 'string' ? row[4].c : '';
        const department = await freshDepartment(category);
        const planned = pages.length + 2 + (department ? 1 : 0);
        const dayReads = fresh.reads.filter(time => now - time < 24 * 60 * MINUTE).length;
        const reason = fresh.active.length >= FRESH.MAX_ACTIVE ? 'active'
          : fresh.starts.filter(time => now - time < 60 * MINUTE).length >= FRESH.MAX_PER_HOUR ? 'hourly'
            : dayReads + planned > FRESH.MAX_READS_PER_DAY ? 'daily' : '';
        fresh.probed.delete(id);
        fresh.probed.set(id, now);
        if (reason) {
          saveFresh(now);
          emit({ ...base, event: 'skipped', reason, trigger });
          continue;
        }
        fresh.sequence += 1;
        const id32 = Math.floor(random() * 0x1_0000_0000).toString(16).padStart(8, '0');
        const probe = { id: `${id32}${(fresh.sequence & 0xffff).toString(16).padStart(4, '0')}`, item: id,
          price: item.value_usd, startedAt: at, pages: new Set(pages), queue: [], checks: [], moreChecks: 0,
          first: {}, appearedAt: null, extraReads: 0, droppedReads: 0, sequence: 0 };
        probe.queue = [...pages.map(page => freshTarget(probe, 'value80', page, { initial: true })),
          freshTarget(probe, 'newest', 1, { initial: true }), freshTarget(probe, 'new80', 1, { initial: true }),
          ...(department ? [freshTarget(probe, 'departments', 1, { catId: department, initial: true })] : [])];
        fresh.active.push(probe);
        fresh.starts.push(now);
        saveFresh(now);
        emit({ ...base, event: 'start', probe: probe.id, startedAt: at, trigger, predictedPage: predicted, pages,
          plannedReads: planned, ...(category ? { category } : {}), ...(department ? { department } : {}) });
      }
    }

    function freshEnd(probe, outcome, now) {
      const index = fresh.active.indexOf(probe);
      if (index < 0) return;
      fresh.active.splice(index, 1);
      probe.droppedReads += probe.queue.length;
      probe.queue = [];
      emit({ kind: 'probe', event: 'end', probe: probe.id, itemId: probe.item, valueCents: Math.round(probe.price * 100),
        at: now, startedAt: probe.startedAt, outcome,
        ...(probe.appearedAt !== null ? { lagMs: Math.max(0, probe.appearedAt - probe.startedAt) } : {}),
        extraReads: probe.extraReads, droppedReads: probe.droppedReads, pages: [...probe.pages].sort((a, b) => a - b),
        first: probe.first, checks: probe.checks, ...(probe.moreChecks ? { moreChecks: probe.moreChecks } : {}) });
    }

    function freshStopAll(outcome, now = Date.now()) {
      for (const probe of [...fresh.active]) freshEnd(probe, outcome, now);
    }

    // Before each read choice: end probes that are done or out of time, and
    // queue a check of each predicted page no read has covered for 60 s.
    function freshTick(now, slow) {
      if (!fresh.active.length) return;
      if (slow) {
        freshStopAll('safe-period', now);
        return;
      }
      for (const probe of [...fresh.active]) {
        const initial = probe.queue.some(target => target.initial);
        if (probe.appearedAt !== null && !initial) freshEnd(probe, 'appeared', now);
        else if (now - probe.startedAt >= FRESH.WATCH_MS) freshEnd(probe, 'timeout', now);
        else if (!initial && probe.appearedAt === null) {
          const { pages } = freshPages(probe.price);
          for (const page of pages) {
            probe.pages.add(page);
            const last = value80ReadAt.get(page);
            if ((last === undefined || now - last >= FRESH.REREAD_MS) &&
                !probe.queue.some(target => target.lane === 'value80' && target.page === page)) {
              probe.queue.push(freshTarget(probe, 'value80', page, { reread: true }));
            }
          }
        }
      }
    }

    // The next probe read, oldest probe first, within the daily extra-read cap.
    // A re-check that a normal read has meanwhile covered is dropped, not read.
    function freshNext(now) {
      for (const probe of fresh.active) {
        while (probe.queue.length) {
          const target = probe.queue.shift();
          const last = value80ReadAt.get(target.page);
          if (target.reread && last !== undefined && now - last < FRESH.REREAD_MS) continue;
          fresh.reads = fresh.reads.filter(time => now - time < 24 * 60 * MINUTE);
          if (fresh.reads.length >= FRESH.MAX_READS_PER_DAY) {
            probe.droppedReads += 1 + probe.queue.length;
            probe.queue = [];
            break;
          }
          fresh.reads.push(now);
          probe.extraReads += 1;
          saveFresh(now);
          return target;
        }
      }
      return null;
    }

    // Every successful read: which items the catalogue lists show, the
    // min_price=79 page spans, and each running probe's checks and first sightings.
    function freshObserve(target, reply, at) {
      const { lane, page } = target;
      const catalogue = CATALOGUE.has(lane);
      if (!catalogue && lane !== 'departments') return;
      const rows = rowsOf(reply);
      if (catalogue) {
        for (const row of rows) {
          const id = `ip-${row[0]}`;
          catalogueSeen.delete(id);
          catalogueSeen.set(id, at);
        }
        if (catalogueSeen.size > FRESH.MAX_SEEN) {
          for (const [id, seenAt] of catalogueSeen) {
            if (catalogueSeen.size <= FRESH.MAX_SEEN || at - seenAt <= FRESH.UNSEEN_MS) break;
            catalogueSeen.delete(id);
          }
          while (catalogueSeen.size > FRESH.MAX_SEEN) catalogueSeen.delete(catalogueSeen.keys().next().value);
        }
      }
      if (lane === 'value80') {
        value80ReadAt.set(page, at);
        const core = coreRange(reply.items);
        if (core) value80Core.set(page, core);
        else value80Core.delete(page);
      }
      if (!fresh.active.length) return;
      const shape = lane === 'departments' ? 'department' : lane;
      for (const probe of fresh.active) {
        const row = rows.find(entry => `ip-${entry[0]}` === probe.item);
        const own = target.fresh === probe;
        const watched = lane === 'value80' && probe.pages.has(page);
        if (own || watched || row) {
          const check = { shape, page, at, found: Boolean(row), ...(row ? { position: row[1] } : {}),
            ...(own ? { extra: true } : {}), ...(target.catId ? { catId: target.catId } : {}) };
          if (probe.checks.length < FRESH.MAX_CHECKS) probe.checks.push(check);
          else probe.moreChecks += 1;
        }
        if (row && !probe.first[shape]) {
          probe.first[shape] = { at, page, position: row[1], ...(own ? { extra: true } : {}) };
        }
        if (row && lane === 'value80' && probe.appearedAt === null) probe.appearedAt = at;
      }
    }

    // Every read of the scheduler, its outcome and every product it showed.
    function snapshotRead(target, url, startedAt, outcome, reply) {
      if (target.research) return;
      let params = null;
      try { params = new URL(url).searchParams; } catch { params = new URL(HOME).searchParams; }
      const minPrice = Number.parseInt(params.get('min_price') || '', 10);
      const sort = params.get('sort') || '';
      emit({ kind: 'read', source: 'fast', at: startedAt, durationMs: Math.max(0, Date.now() - startedAt),
        lane: target.lane, page: target.page, outcome,
        ...(/^[a-z_]{1,24}$/.test(sort) ? { sort } : {}),
        ...(Number.isSafeInteger(minPrice) && minPrice >= 0 && minPrice <= 1e6 ? { minPrice } : {}),
        ...(target.catId ? { catId: target.catId } : {}),
        ...(target.query ? { searchHash: searchHash(searchKey(target.query)) } : {}),
        ...(target.hot ? { hot: true } : {}), ...(target.fresh ? { probe: target.fresh.id } : {}),
        ...(target.key === 'probe' ? { coordinator: true } : {}),
        ...(Number.isSafeInteger(target.index) && target.index > 0 ? { targetIndex: target.index } : {}),
        ...(Number.isInteger(reply?.status) ? { status: reply.status } : {}),
        ...(Number.isInteger(reply?.totalPages) && reply.totalPages >= 0 ? { totalPages: reply.totalPages } : {}),
        ...(Number.isSafeInteger(reply?.products) && reply.products >= 0 ? { products: reply.products } : {}),
        ...(Number.isSafeInteger(reply?.age) && reply.age >= 0 ? { ageSeconds: reply.age } : {}),
        items: outcome === 'items' ? rowsOf(reply) : [] });
    }

    async function read(url, type = 'reviewer:fast-read') {
      for (const tabId of await tabIds()) {
        try {
          const reply = await chrome.tabs.sendMessage(tabId, { type, url });
          // A request aborted by that tab navigating has no HTTP status: try the next tab.
          if (reply && typeof reply === 'object' && !(reply.ok === false && !reply.blocked &&
              ['network', 'error'].includes(reply.reason))) return { ...reply, tabId };
        } catch { /* Tab navigating or without a reader; try the next one. */ }
      }
      return null;
    }

    function settleProbes(reply) {
      for (const entry of [...probes, ...researchQueue]) entry.settle(reply);
    }

    async function readTarget(target, state, probe) {
      const { lane, page, query = '', catId = '' } = target;
      const url = target.url || urlFor(lane, page, query, catId);
      const startedAt = Date.now();
      // A search is logged by a hash of its query, never the query itself.
      // Freshness probe reads are marked `fresh`: extra reads, never ingested.
      const base = { lane, page,
        ...(Number.isSafeInteger(target.index) && target.index > 0 ? { targetIndex: target.index } : {}),
        ...(query ? { searchHash: searchHash(searchKey(query)) } : {}), ...(target.hot ? { hot: true } : {}),
        ...(target.fresh ? { fresh: true } : {}) };
      const elapsed = () => ({ durationMs: Date.now() - startedAt });
      const reply = await read(url, target.research ? 'reviewer:research-read' : 'reviewer:fast-read');
      const status = Number.isInteger(reply?.status) ? { responseStatus: reply.status } : {};
      if (!reply) {
        snapshotRead(target, url, startedAt, 'unavailable', null);
        log({ ...base, outcome: 'unavailable', ...elapsed() });
        return null;
      }
      if (reply.blocked) {
        snapshotRead(target, url, startedAt, 'blocked', reply);
        const blocks = (Number(state.blocks) || 0) + 1;
        const backoff = BACKOFF_MS[Math.min(blocks, BACKOFF_MS.length) - 1];
        await writeState({ blocks, until: Date.now() + backoff, slowUntil: Date.now() + SAFE_AFTER_BLOCK_MS });
        departmentQueue = [];
        settleProbes(null);
        freshStopAll('blocked');
        // Hands the challenged tab to the bot-check path at once; never blocks or breaks reads.
        Promise.resolve().then(() => onBlocked(Math.round(backoff / 60_000), reply.tabId)).catch(() => {});
        log({ ...base, ...status, outcome: 'blocked', count: blocks, ...elapsed() });
        return null;
      }
      if (target.research) {
        const totalPages = reply.pagination?.maxPage;
        log({ ...base, ...status, outcome: reply.ok ? 'items' : 'error',
          items: Array.isArray(reply.products) ? reply.products.length : 0,
          ...(Number.isInteger(totalPages) ? { totalPages } : {}), ...elapsed() });
        return reply;
      }
      if (!reply.ok || !Array.isArray(reply.items)) {
        snapshotRead(target, url, startedAt, 'error', reply);
        // A department page this reader cannot prove (for example a changed
        // page format) is retried later rather than spending every spare slot.
        if (lane === 'departments' && !probe && ++departmentFailures >= DEPARTMENT_FAILURES) {
          departmentPausedUntil = Date.now() + DEPARTMENT_PAUSE_MS;
          departmentFailures = 0;
          departmentQueue = [];
        }
        log({ ...base, ...status, outcome: 'error', ...elapsed() });
        return null;
      }
      if (state.blocks) await writeState({ blocks: 0, until: 0, slowUntil: Number(state.slowUntil) || 0 });
      learn(target, reply, probe);
      snapshotRead(target, url, startedAt, 'items', reply);
      freshObserve(target, reply, startedAt);
      // Only the scheduler's own search reads can start a freshness probe.
      if (!probe && query && lane !== 'departments') await freshTrigger(target, reply, state, startedAt);
      const totals = { ...(Number.isInteger(reply.totalPages) ? { totalPages: reply.totalPages } : {}),
        // Stock mix of the free offers read, response size and CDN age: what
        // tells a one-item flicker from a whole page that changed or came stale.
        ...(reply.stock ? { stockIn: reply.stock.in, stockOut: reply.stock.out, stockOther: reply.stock.other } : {}),
        ...(Number.isSafeInteger(reply.bytes) ? { bytes: reply.bytes } : {}),
        ...(Number.isSafeInteger(reply.age) ? { ageSeconds: reply.age } : {}),
        ...(Number.isSafeInteger(reply.products) ? { products: reply.products } : {}),
        ...(lane === 'departments' && Number.isSafeInteger(reply.totalItems) ? { count: reply.totalItems } : {}),
        // For price_high pages and searches: how many $80+ items (in stock or not) the page holds.
        ...(['value', 'value80'].includes(lane) || (query && lane !== 'departments')
          ? { count: reply.items.filter(item => item.value_usd >= VALUE_FLOOR).length } : {}) };
      const items = reply.items;
      if (probe) {
        log({ ...base, ...status, ...totals, outcome: 'items', items: items.length, ...elapsed() });
        return reply;
      }
      const signature = items.map(item => `${item.item_id}:${item.available}:${item.value_usd}`).join('|');
      const previous = lastSent.get(url);
      // A page holding a restock awaiting confirmation is sent even when it looks
      // unchanged: only a sent page can confirm or reject that restock.
      let awaiting = false;
      try { awaiting = Boolean(mustSend(items)); } catch { /* Never blocks a read. */ }
      if (!items.length || (!awaiting && previous?.signature === signature && Date.now() - previous.at < RESEND_MS)) {
        log({ ...base, ...status, ...totals, outcome: 'items', items: items.length, new: 0, ...elapsed() });
        return reply;
      }
      // The target index tells searches and departments apart: a held restock is
      // confirmed only by a re-read of the very page that held it.
      const result = await send(items, page, lane, base.targetIndex);
      // Only a confirmed delivery suppresses the next resend of this page, and
      // never one that held back a restock: its next read must be sent.
      if (result?.ok && !result.held) {
        lastSent.set(url, { signature, at: Date.now() });
        if (lastSent.size > 2000) lastSent.delete(lastSent.keys().next().value);
      } else if (result?.held) {
        lastSent.delete(url);
        // Read this page again in RECHECK_MS rather than at its usual interval.
        if (Number.isFinite(target.interval) && target.interval > RECHECK_MS && lastRead.has(target.key)) {
          lastRead.set(target.key, Date.now() + RECHECK_MS - target.interval);
        }
      }
      // The upload's request id joins this read to the items it delivered.
      log({ ...base, ...status, ...totals, outcome: result?.ok ? 'items' : 'error', items: items.length,
        new: result?.summary?.new || 0, duplicates: result?.summary?.duplicates || 0,
        ...(typeof result?.requestId === 'string' ? { requestId: result.requestId } : {}), ...elapsed() });
      return reply;
    }

    async function run() {
      if (running) return;
      running = true;
      let delay = STEP_MS;
      try {
        const wait = lastStartAt + gap - Date.now();
        if (wait > 0) {
          delay = wait;
          return;
        }
        const state = await readState();
        if (Date.now() < state.until || await paused()) {
          settleProbes(null);
          freshStopAll('paused');
          return;
        }
        const probe = probes.shift();
        const target = probe ? probe.target
          : await nextTarget(Date.now(), Date.now() < (Number(state.slowUntil) || 0));
        if (!target) return;
        lastStartAt = Date.now();
        const slow = lastStartAt < (Number(state.slowUntil) || 0);
        gap = (slow ? SAFE_SPACING_MS : MIN_SPACING_MS) + Math.round(random() * SPACING_JITTER_MS);
        // A freshness read is extra: it never stands in for a page's scheduled read.
        if (!probe && !target.fresh) lastRead.set(target.key, lastStartAt);
        if (!probe && target.rank === RANK.slow) lastSlowAt = lastStartAt;
        const reply = await readTarget(target, state, Boolean(probe || target.fresh));
        probe?.settle(reply);
        target.settle?.(reply);
        if (target.key === 'value:pass' || target.pass) {
          // Continue down from this page to the current $40 boundary; a failed
          // read ends the pass and the next one starts over on schedule.
          valuePass = reply?.ok ? Array.from({ length: Math.max(0, value.deep - target.page) },
            (_, index) => target.page + 1 + index) : [];
        }
      } finally {
        running = false;
        schedule(delay);
      }
    }

    function schedule(delay = STEP_MS) {
      if (timer !== null) clearTimer(timer);
      timer = setTimer(() => { timer = null; run().catch(() => {}); }, Math.max(0, delay));
      timer?.unref?.(); // Test runtimes only; the extension worker has no unref.
    }

    async function ensure() {
      try {
        if (!await chrome.alarms.get(ALARM)) await chrome.alarms.create(ALARM, { periodInMinutes: 0.5 });
      } catch { /* The timer chain still runs while the worker is alive. */ }
      if (timer === null && !running) schedule();
    }

    // One read of a department's or search's first page for the scan
    // coordinator, served before the next scheduled read under the same rate
    // and bot-check rules. Not ingested. Resolves null when no answer is possible.
    function probe(raw) {
      return new Promise(resolve => {
        let url;
        try { url = new URL(raw); } catch { resolve(null); return; }
        const catId = url.searchParams.get('catId') || '';
        const query = url.searchParams.get('q') || '';
        if ((!catId && !query) || probes.length >= MAX_PROBES) {
          resolve(null);
          return;
        }
        const entry = { target: { key: 'probe', lane: catId ? 'departments' : 'requests', page: 1, query, catId,
          url: url.toString() } };
        let timeout = null;
        entry.settle = reply => {
          if (timeout !== null) clearTimer(timeout);
          timeout = null;
          const index = probes.indexOf(entry);
          if (index >= 0) probes.splice(index, 1);
          resolve(reply);
        };
        timeout = setTimer(() => { timeout = null; entry.settle(null); }, PROBE_TIMEOUT_MS);
        probes.push(entry);
        if (!running) schedule(0);
      });
    }

    // One owner-listed reviewer list page, summarised in full and never
    // ingested. Waits behind the rank-0 reads only; null on backoff or timeout.
    function research(raw) {
      return new Promise(resolve => {
        let url;
        try { url = new URL(raw); } catch { resolve(null); return; }
        if (url.origin !== 'https://www.walmart.com' || url.pathname !== '/reviews/claim-product' ||
            researchQueue.length >= MAX_RESEARCH) {
          resolve(null);
          return;
        }
        const page = Math.min(99, Math.max(1, Number.parseInt(url.searchParams.get('page') || '1', 10) || 1));
        const entry = {};
        let timeout = null;
        entry.settle = reply => {
          if (timeout !== null) clearTimer(timeout);
          timeout = null;
          const index = researchQueue.indexOf(entry);
          if (index >= 0) researchQueue.splice(index, 1);
          resolve(reply);
        };
        entry.target = { key: 'research', lane: 'research', page, url: url.toString(), research: true,
          settle: entry.settle };
        timeout = setTimer(() => { timeout = null; entry.settle(null); }, RESEARCH_TIMEOUT_MS);
        researchQueue.push(entry);
        if (!running) schedule(0);
      });
    }

    // True when a direct read found this search with no products recently.
    function recentlyEmpty(query) {
      if (typeof query !== 'string' || !query.trim()) return false;
      const info = searches.get(searchKey(query));
      return Boolean(info?.empty && Date.now() - info.at < EMPTY_PROOF_MS);
    }

    // The hot set (normalised queries, in joining order) and current scores.
    async function hotSearches() {
      await loadHot();
      const now = Date.now();
      return { hot: [...hot.set], scores: Object.fromEntries([...hot.scores]
        .map(([key, entry]) => [key, Math.round(decayed(entry, now) * 1_000) / 1_000])) };
    }

    // Running freshness probes and the extra reads of the last day (status and tests).
    async function freshness() {
      await loadFresh();
      const now = Date.now();
      return { active: fresh.active.map(probe => ({ probe: probe.id, itemId: probe.item, startedAt: probe.startedAt,
        queued: probe.queue.length, extraReads: probe.extraReads })),
      readsToday: fresh.reads.filter(at => now - at < 24 * 60 * MINUTE).length,
      startsThisHour: fresh.starts.filter(at => now - at < 60 * MINUTE).length };
    }

    return { ensure, run, probe, research, recentlyEmpty, hotSearches, freshness, alarmName: ALARM };
  }

  return { create, urlFor, searchHash, queryHash, rowsOf, SORTS, INTERVALS, RECHECK_MS, MIN_SPACING_MS,
    SAFE_SPACING_MS, STEP_MS, VALUE_FLOOR, VALUE_DEEP_FLOOR, VALUE_MAX_PAGES, HOT_SEEDS, HOT_SIZE, HOT_HALF_LIFE_MS,
    FRESH };
})();
