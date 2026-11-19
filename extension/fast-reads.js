// Direct reads of the reviewer catalogue's own page data. No rendering or
// scrolling: the server-rendered page already carries its 40 items as data.
// One read at a time; read starts 1.4-1.8 s apart (about 37 a minute; the
// owner raised the old 24), back to 2.5-2.9 s for a day after any bot-check
// sign. Each step reads the most important target that is due; a lower rank
// always goes first:
//   0  sort=price_high page 1 every 10 s (first of all), every other page holding $80+ items
//      every 20 s (the owner's two most valuable reads); the min_price=79 list
//      (about 8 pages), each page holding an $80+ offer every 40 s, the rest
//      every 3 min
//   1  the slow, fixed checks: sort=new page 1 (5 min) and member request
//      searches that had no products at their last read (30 min)
//   2  member request searches with products (page 1, every 4 min at best):
//      they take every read the ranks above leave
//   3  price_high pages down to $40 (one top-down pass), priority searches,
//      search pages 2-3
//   4  sort=new pages 2-4
//   5  department pages, deepest first, only in slots nothing above needs
// Within a rank, the target most overdue for its own interval goes first.
// Nov 19 (owner-approved, same read load): sort=new page 1 went from 30 s to
// 5 min (it found no $80+ item first in the Nov 16-19 audit) and empty searches
// from 12 to 30 min (a third of search reads were empty); the ~2 reads a minute
// this frees go to searches with products. The read rate is set by the spacing
// alone (every step reads one due target, departments fill the rest), so
// intervals only move reads between targets; they never add any
// (simulated on the Nov 19 workload: 34.68 reads a minute before and after).
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
const ReviewerFastReads = (() => {
  const HOME = 'https://www.walmart.com/reviews/claim-product';
  const ALARM = 'fastReads';
  const STATE_KEY = 'reviewerFastReads';
  const STEP_MS = 250; // Pause after each read.
  const MIN_SPACING_MS = 1_400; // Between read starts, plus up to SPACING_JITTER_MS.
  const SPACING_JITTER_MS = 400; // Irregular starts, not a metronome.
  const SAFE_SPACING_MS = 2_500; // The old rate, kept for a day after any bot-check sign.
  const SAFE_AFTER_BLOCK_MS = 24 * 60 * 60_000;
  // Resend an unchanged page this often so newly added requests still match.
  const RESEND_MS = 5 * 60_000;
  const BACKOFF_MS = [5 * 60_000]; // Flat: direct reads only; the tabs and the bot-check bypasser are unaffected.
  const SORTS = Object.freeze({ newest: 'new', value: 'price_high', value80: 'price_high', requests: 'new',
    priority: 'new' });
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
  // Owner-listed research reads wait behind the $80+ pages only.
  const RESEARCH_TIMEOUT_MS = 120_000;
  const MAX_RESEARCH = 4;
  const SECOND = 1_000;
  const MINUTE = 60_000;
  const INTERVALS = Object.freeze({
    value: Object.freeze([10 * SECOND]), valueHigh: 20 * SECOND, valueDeep: 4 * MINUTE,
    value80: 40 * SECOND, value80Idle: 3 * MINUTE,
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

  function urlFor(lane, page, query = '', catId = '') {
    const url = new URL(HOME);
    if (catId) url.searchParams.set('catId', catId);
    if (query) {
      url.searchParams.set('q', query);
      url.searchParams.set('queryNups', query);
    }
    if (!catId) url.searchParams.set('sort', SORTS[lane]);
    if (lane === 'value80') url.searchParams.set('min_price', String(VALUE80_MIN_PRICE));
    if (page > 1) url.searchParams.set('page', String(page));
    return url.toString();
  }

  const searchKey = query => query.trim().replace(/\s+/g, ' ').toLocaleLowerCase('en-US');

  function create(chrome, { tabIds, send, paused, diagnostic, queries = async () => [], departments = async () => [],
    onBlocked = async () => {}, mustSend = () => false, setTimer = setTimeout, clearTimer = clearTimeout,
    random = Math.random }) {
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

    // Member searches first: they become personal DMs. A query in both lists
    // counts as a member search.
    async function searchTargets() {
      let lists;
      try { lists = await queries(); } catch { lists = []; }
      const members = Array.isArray(lists) ? lists : Array.isArray(lists?.members) ? lists.members : [];
      const priority = !Array.isArray(lists) && Array.isArray(lists?.priority) ? lists.priority : [];
      const targets = [];
      const keys = new Set();
      const add = (query, index, lane) => {
        if (typeof query !== 'string' || !query.trim()) return;
        const key = searchKey(query);
        if (keys.has(key)) return;
        keys.add(key);
        const info = searches.get(key);
        const member = lane === 'requests';
        // An empty member search keeps its slow 30-minute check ahead of the
        // searches with products, which share every read left after it.
        targets.push({ key: `search:${key}:1`, lane, page: 1, query, index,
          rank: !member ? RANK.deep : info?.empty ? RANK.slow : RANK.member,
          interval: info?.empty ? INTERVALS.memberEmpty : member ? INTERVALS.member : INTERVALS.priority });
        for (let page = 2; page <= Math.min(info?.totalPages || 1, SEARCH_PAGES); page += 1) {
          targets.push({ key: `search:${key}:${page}`, lane, page, query, index, rank: RANK.deep,
            interval: member ? INTERVALS.memberPages : INTERVALS.priorityPages });
        }
      };
      members.forEach((query, index) => add(query, index + 1, 'requests'));
      priority.forEach((query, index) => add(query, index + 1, 'priority'));
      // Forget searches that are no longer configured.
      for (const key of searches.keys()) if (!keys.has(key)) searches.delete(key);
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

    async function nextTarget(now) {
      // A target never read yet counts as twice due: first in line, without
      // letting a large new batch shut out a short-interval target for minutes.
      const due = [...valueTargets(), ...await searchTargets(), ...newestTargets()]
        .map(target => ({ ...target, overdue: lastRead.has(target.key)
          ? (now - lastRead.get(target.key)) / target.interval : 2 }))
        .filter(target => target.overdue >= 1)
        .sort((a, b) => a.rank - b.rank || b.overdue - a.overdue);
      const highDue = due[0] && due[0].rank <= RANK.value;
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
      }
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
      const base = { lane, page,
        ...(Number.isSafeInteger(target.index) && target.index > 0 ? { targetIndex: target.index } : {}) };
      const elapsed = () => ({ durationMs: Date.now() - startedAt });
      const reply = await read(url, target.research ? 'reviewer:research-read' : 'reviewer:fast-read');
      const status = Number.isInteger(reply?.status) ? { responseStatus: reply.status } : {};
      if (!reply) {
        log({ ...base, outcome: 'unavailable', ...elapsed() });
        return null;
      }
      if (reply.blocked) {
        const blocks = (Number(state.blocks) || 0) + 1;
        const backoff = BACKOFF_MS[Math.min(blocks, BACKOFF_MS.length) - 1];
        await writeState({ blocks, until: Date.now() + backoff, slowUntil: Date.now() + SAFE_AFTER_BLOCK_MS });
        departmentQueue = [];
        settleProbes(null);
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
      const totals = { ...(Number.isInteger(reply.totalPages) ? { totalPages: reply.totalPages } : {}),
        // Stock mix of the free offers read, response size and CDN age: what
        // tells a one-item flicker from a whole page that changed or came stale.
        ...(reply.stock ? { stockIn: reply.stock.in, stockOut: reply.stock.out, stockOther: reply.stock.other } : {}),
        ...(Number.isSafeInteger(reply.bytes) ? { bytes: reply.bytes } : {}),
        ...(Number.isSafeInteger(reply.age) ? { ageSeconds: reply.age } : {}),
        ...(Number.isSafeInteger(reply.products) ? { products: reply.products } : {}),
        ...(lane === 'departments' && Number.isSafeInteger(reply.totalItems) ? { count: reply.totalItems } : {}),
        // For price_high pages: how many $80+ items (in stock or not) the page holds.
        ...(['value', 'value80'].includes(lane)
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
      log({ ...base, ...status, ...totals, outcome: result?.ok ? 'items' : 'error', items: items.length,
        new: result?.summary?.new || 0, duplicates: result?.summary?.duplicates || 0, ...elapsed() });
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
          return;
        }
        const probe = probes.shift();
        const target = probe ? probe.target : await nextTarget(Date.now());
        if (!target) return;
        lastStartAt = Date.now();
        const slow = lastStartAt < (Number(state.slowUntil) || 0);
        gap = (slow ? SAFE_SPACING_MS : MIN_SPACING_MS) + Math.round(random() * SPACING_JITTER_MS);
        if (!probe) lastRead.set(target.key, lastStartAt);
        if (!probe && target.rank === RANK.slow) lastSlowAt = lastStartAt;
        const reply = await readTarget(target, state, Boolean(probe));
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
    // ingested. Waits behind the $80+ pages only; null on backoff or timeout.
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

    return { ensure, run, probe, research, recentlyEmpty, alarmName: ALARM };
  }

  return { create, urlFor, SORTS, INTERVALS, RECHECK_MS, MIN_SPACING_MS, SAFE_SPACING_MS, VALUE_FLOOR, VALUE_DEEP_FLOOR,
    VALUE_MAX_PAGES };
})();
