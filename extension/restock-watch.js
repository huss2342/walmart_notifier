// $80+ offers seen out of stock are remembered so their return reaches the
// backend as a restock. The backend decides whether the offer was away long
// enough to announce again. Since 2.32.5 the watch, the blip history and the
// unacknowledged confirmed returns live in chrome.storage.local (bounded), so an
// extension reload or browser restart no longer forgets them (session storage
// was cleared by every reload, resetting the blip counts).
//
// A return must be CONFIRMED before it is announced. On Nov 12 both restock
// alerts were single-read blips: Walmart's list data showed an offer that had
// been out of stock for days as IN_STOCK (every stock field agreeing, the rest
// of the page unchanged) for one read, and out of stock again 10-40 s later.
// Nobody can claim an offer that is gone before the alert is opened. So the
// first in-stock read only holds the offer back (it is not sent at all, which
// also keeps the backend's "away 15+ min" clock intact), and only a re-read of
// the SAME page (same lane, page and target) at least CONFIRM_MS later, still in
// stock, announces it. On Nov 19 a return held on price_high page 1 was
// "confirmed" 9.15 s later by an incidental read of the min_price=79 list, and
// page 1's own re-read 1.3 s after that showed it out of stock again. So an
// in-stock read of another page only starts that page's own check, while an
// out-of-stock read from any page or lane rejects the return at once; so does
// no confirmation within PENDING_MS.
//
// Some offers flicker all day (ip-20755219869 was held 14 times Nov 12-19). An
// offer that blipped BLIP_LIMIT+ times in BLIP_WINDOW_MS (held, then read out of
// stock before confirming; or confirmed, then out again within
// BLIP_AFTER_CONFIRM_MS) is "steady-checked": one page must read it in stock for
// STEADY_MS, at least STEADY_READS times, never more than STEADY_GAP_MS apart.
// Variant offers (Walmart's data marks the listing as one of several variants)
// are steady-checked from their first hold: Nov 17-19, 33 of the 34 blips were
// variants (71 variant returns, 33 blips; 184 other returns, 1 blip).
//
// A confirmed return is not consumed by its upload: it stays flagged on every
// in-stock read, and is re-sent on its own by retryable(), until an upload
// carrying it is acknowledged (2xx, ack()) or RETRY_MS passes. On Nov 19 a
// backend restart refused (503) the upload confirming 28 returns, and only the
// 5-minute flag on later reads of their pages could still deliver them. Out of
// stock again before that, it is dropped as a blip.
//
// Every step is logged as a 'restock-watch' event:
//   returned  - first in-stock read, held (with Walmart's stock fields;
//               `count` = blips in the last day, when any)
//   confirmed - announced as a restock (`reads` = that page's in-stock reads)
//   rejected  - out of stock again (or gone from its pages) before confirming
//   followup  - what the reads after a confirmed restock showed
var ReviewerRestockWatch = (() => {
  const KEY = 'restockWatch';
  const BLIP_KEY = 'restockBlips';
  const RETURNS_KEY = 'restockReturns';
  const FLOOR_USD = 80;
  const LIMIT = 1000;
  // An offer out of stock this long is forgotten; if it ever comes back the
  // backend's "back again" rule (unseen 60+ min) still announces it.
  const WATCH_MAX_AGE_MS = 14 * 24 * 60 * 60_000;
  // The confirming re-read must come this long after its page's first in-stock
  // read: price_high page 1 is read every ~10-11 s and a held page is re-read
  // 10 s later, so a real restock is announced about 10 s after it is first seen.
  const CONFIRM_MS = 9_000;
  // A held return that is not confirmed by then is dropped: the pages holding
  // it were not re-sent, which means they no longer showed it in stock.
  const PENDING_MS = 2 * 60_000;
  const FOLLOW_READS = 3; // In-stock sightings logged after a confirmed restock.
  const FOLLOW_MS = 15 * 60_000; // Then the item is no longer followed.
  // A confirmed return stays flagged (and is retried on its own) until an upload
  // carrying it is acknowledged, for at most this long: the backend's alert
  // jobs live 30 min, and a restock older than this is no longer news. The
  // backend alerts at most once: only 15+ min after the last in-stock sighting,
  // once an hour.
  const RETRY_MS = 15 * 60_000;
  // Flickering offers. Every real restock audited Nov 12-19 stayed in stock 5+
  // minutes; the blips were back out within 90 s.
  const BLIP_LIMIT = 3;
  const BLIP_WINDOW_MS = 24 * 60 * 60_000;
  const BLIP_AFTER_CONFIRM_MS = 5 * 60_000;
  const RETURN_KEEP_MS = Math.max(RETRY_MS, BLIP_AFTER_CONFIRM_MS);
  const BLIP_KEEP = 20; // Timestamps remembered per offer.
  const STEADY_MS = 60_000;
  const STEADY_READS = 4; // The first in-stock read and three confirming re-reads.
  const STEADY_GAP_MS = 30_000; // A longer gap restarts that page's check.
  const STEADY_PENDING_MS = PENDING_MS + STEADY_MS;
  const SINGLE_LIST_LANES = ['value', 'value80'];

  const word = value => ['in', 'out', 'other', 'none'].includes(value) ? value : 'none';
  const isRecord = value => Boolean(value) && typeof value === 'object' && !Array.isArray(value);

  // Which page a read came from. Only a re-read of the same page confirms.
  // Searches and departments share lane and page numbers, so their target counts too.
  const pageKey = ctx => [typeof ctx.lane === 'string' ? ctx.lane : '',
    Number.isInteger(ctx.page) ? ctx.page : '',
    Number.isSafeInteger(ctx.targetIndex) ? ctx.targetIndex : '',
    typeof ctx.targetId === 'string' ? ctx.targetId : ''].join('|');

  // The stock facts of one sighting, flattened into diagnostic fields. A card
  // read has only the visible out-of-stock text; a data read has Walmart's fields.
  function signalFields(stock) {
    if (!stock || typeof stock !== 'object') return {};
    const fields = { stockSource: stock.source === 'card' ? 'card' : 'data' };
    if (stock.source !== 'card') {
      fields.signalV2 = word(stock.v2);
      fields.signalStatus = word(stock.status);
      fields.variant = stock.variant === true;
    }
    if (typeof stock.oos === 'boolean') fields.signalOos = stock.oos;
    // Walmart's own stock fields should all say the same thing.
    if (stock.source !== 'card') {
      const says = [stock.v2, stock.status, stock.oos === true ? 'out' : stock.oos === false ? 'in' : null]
        .filter(value => value === 'in' || value === 'out');
      fields.conflict = new Set(says).size > 1;
    }
    return fields;
  }

  function create(storage, { diagnostic = () => {} } = {}) {
    let watch = null; // item_id -> when first seen out of stock
    let blips = null; // item_id -> [when it blipped], most recently blipped last
    let loading = null;
    // Not persisted: details of the current worker's sightings only.
    const outSightings = new Map(); // item_id -> { last, reads }
    // item_id -> { at, lane, page, valueCents, steady, blips, pages }: held, awaiting
    // confirmation. `pages`: page key -> { at, last, reads } of its in-stock reads.
    const pending = new Map();
    const following = new Map(); // item_id -> { at, lane, page, reads, valueCents }
    // item_id -> { at, acked, item }: confirmed returns. Unacknowledged ones are
    // flagged and retried (RETRY_MS); all are kept BLIP_AFTER_CONFIRM_MS for blips.
    let returned = null;
    const tried = new Map(); // item_id -> when retryable() last handed it out (this worker only)

    const log = fields => { try { diagnostic('restock-watch', fields); } catch { /* Logging never stops intake. */ } };
    const read = key => Promise.resolve().then(() => storage.get(key)).then(saved => saved?.[key], () => undefined);
    const write = values => {
      try { Promise.resolve(storage.set(values)).catch(() => {}); } catch { /* Storage never stops intake. */ }
    };
    // Only what the backend accepts: never the stock evidence or the hold mark.
    const snapshot = item => {
      const { stock: _stock, restockHold: _hold, eligibility_url: _legacy, restocked: _flag, ...rest } = item;
      return rest;
    };

    async function load() {
      loading ||= Promise.all([read(KEY), read(BLIP_KEY), read(RETURNS_KEY)]).then(([saved, history, back]) => {
        const now = Date.now();
        watch = new Map(isRecord(saved) ? Object.entries(saved)
          .filter(([, at]) => Number.isFinite(Number(at)) && now - Number(at) < WATCH_MAX_AGE_MS) : []);
        blips = new Map(isRecord(history) ? Object.entries(history)
          .filter(([, times]) => Array.isArray(times) && times.every(Number.isFinite)) : []);
        returned = new Map(isRecord(back) ? Object.entries(back).filter(([id, entry]) => isRecord(entry) &&
          Number.isFinite(entry.at) && now - entry.at < RETURN_KEEP_MS && isRecord(entry.item) &&
          entry.item.item_id === id) : []);
      });
      await loading;
    }

    function savedReturns(now) {
      for (const [id, entry] of returned) if (now - entry.at >= RETURN_KEEP_MS) returned.delete(id);
      while (returned.size > LIMIT) returned.delete(returned.keys().next().value);
      return Object.fromEntries(returned);
    }

    const cents = item => Math.round(item.value_usd * 100);
    const context = (item, ctx) => ({
      itemId: item.item_id,
      valueCents: cents(item),
      ...(ctx.lane ? { lane: ctx.lane } : {}), ...(Number.isInteger(ctx.page) ? { page: ctx.page } : {}),
      ...(ctx.requestId ? { requestId: ctx.requestId } : {})
    });
    const held = item => ({ ...item, restockHold: true });
    const recentBlips = (id, now) => (blips.get(id) || []).filter(at => now - at < BLIP_WINDOW_MS);

    function addBlip(id, now) {
      const times = [...recentBlips(id, now), now].slice(-BLIP_KEEP);
      blips.delete(id); // Re-inserted last, so the longest-quiet offer is trimmed first.
      blips.set(id, times);
      while (blips.size > LIMIT) blips.delete(blips.keys().next().value);
    }

    function savedBlips(now) {
      for (const id of [...blips.keys()]) if (!recentBlips(id, now).length) blips.delete(id);
      return Object.fromEntries(blips);
    }

    async function mark(items, ctx = {}) {
      if (!Array.isArray(items)) return items;
      await load();
      const now = Date.now();
      const here = pageKey(ctx);
      let changed = false;
      let blipped = false;
      let returnsChanged = false;
      const present = new Set();
      for (const [id, wait] of pending) {
        if (now - wait.at > (wait.steady ? STEADY_PENDING_MS : PENDING_MS)) {
          pending.delete(id);
          log({ itemId: id, valueCents: wait.valueCents, ...(wait.lane ? { lane: wait.lane } : {}),
            ...(Number.isInteger(wait.page) ? { page: wait.page } : {}), stage: 'rejected',
            stockState: 'absent', sinceReturnMs: now - wait.at });
        }
      }
      const marked = items.map(item => {
        if (!(Number(item?.value_usd) >= FLOOR_USD) || typeof item?.item_id !== 'string') return item;
        present.add(item.item_id);
        const follow = following.get(item.item_id);
        const wait = pending.get(item.item_id);
        if (item.available === false) {
          const back = returned.get(item.item_id);
          if (back) {
            // Out again: an unacknowledged return is no longer worth re-sending.
            returned.delete(item.item_id);
            tried.delete(item.item_id);
            returnsChanged = true;
          }
          const seen = outSightings.get(item.item_id);
          outSightings.set(item.item_id, { last: now, reads: (seen?.reads || 0) + 1 });
          if (wait) {
            // Out again before it could be confirmed, on any page or lane: a blip, never announced.
            pending.delete(item.item_id);
            addBlip(item.item_id, now);
            blipped = true;
            log({ ...context(item, ctx), stage: 'rejected', stockState: 'out-of-stock',
              sinceReturnMs: now - wait.at, ...signalFields(item.stock) });
          } else if (back !== undefined && now - back.at < BLIP_AFTER_CONFIRM_MS) {
            // Announced, then out again within minutes: a blip that got through.
            addBlip(item.item_id, now);
            blipped = true;
          }
          if (follow) {
            // A confirmed restock went back out: it lasted this long.
            following.delete(item.item_id);
            log({ ...context(item, ctx), stage: 'followup', stockState: 'out-of-stock',
              sinceReturnMs: now - follow.at, reads: follow.reads, ...signalFields(item.stock) });
          }
          if (!watch.has(item.item_id)) {
            watch.set(item.item_id, now);
            outSightings.set(item.item_id, { last: now, reads: 1 });
            changed = true;
          }
          return item;
        }
        if (!watch.has(item.item_id)) {
          if (follow && now - follow.at <= FOLLOW_MS) {
            follow.reads += 1;
            log({ ...context(item, ctx), stage: 'followup', stockState: 'in-stock',
              sinceReturnMs: now - follow.at, reads: follow.reads, ...signalFields(item.stock) });
            if (follow.reads >= FOLLOW_READS) following.delete(item.item_id);
          }
          // A confirmed return rides on every in-stock read until an upload of it is acknowledged.
          const back = returned.get(item.item_id);
          if (back && !back.acked && now - back.at < RETRY_MS) return { ...item, restocked: true };
          return item;
        }
        const since = Number(watch.get(item.item_id));
        const seen = outSightings.get(item.item_id);
        const variant = item.stock?.source !== 'card' && item.stock?.variant === true;
        if (!wait) {
          // First in-stock read after being out: hold it until its page's re-read agrees.
          const recent = recentBlips(item.item_id, now).length;
          pending.set(item.item_id, { at: now, lane: ctx.lane, page: ctx.page, valueCents: cents(item),
            steady: recent >= BLIP_LIMIT || variant, blips: recent,
            pages: new Map([[here, { at: now, last: now, reads: 1 }]]) });
          log({ ...context(item, ctx), stage: 'returned', stockState: 'in-stock',
            ...(recent ? { count: recent } : {}),
            ...(Number.isFinite(since) ? { awayMs: Math.max(0, now - since) } : {}),
            ...(seen ? { sinceOutMs: Math.max(0, now - seen.last), outReads: seen.reads } : {}),
            ...signalFields(item.stock) });
          return held(item);
        }
        // A variant seen only on cards so far is steady-checked once a data read names it.
        if (variant) wait.steady = true;
        const check = wait.pages.get(here);
        if (!check || (wait.steady && now - check.last > STEADY_GAP_MS)) {
          // An incidental read of another page never confirms: it starts that
          // page's own check (as does a steady check that lost sight of it).
          wait.pages.set(here, { at: now, last: now, reads: 1 });
          return held(item);
        }
        check.reads += 1;
        check.last = now;
        if (now - check.at < (wait.steady ? STEADY_MS : CONFIRM_MS) ||
            check.reads < (wait.steady ? STEADY_READS : 2)) return held(item); // Not proven yet.
        // Its own page still shows it in stock: a restock worth announcing.
        pending.delete(item.item_id);
        watch.delete(item.item_id);
        outSightings.delete(item.item_id);
        changed = true;
        following.set(item.item_id, { at: now, lane: ctx.lane, page: ctx.page, reads: 0, valueCents: cents(item) });
        returned.delete(item.item_id); // Re-inserted last, so the oldest return is trimmed first.
        returned.set(item.item_id, { at: now, acked: false, item: snapshot(item) });
        returnsChanged = true;
        log({ ...context(item, ctx), stage: 'confirmed', stockState: 'in-stock',
          sinceReturnMs: now - wait.at, reads: check.reads, ...(wait.blips ? { count: wait.blips } : {}),
          ...(Number.isFinite(since) ? { awayMs: Math.max(0, now - since) } : {}),
          ...signalFields(item.stock) });
        return { ...item, restocked: true };
      });
      while (pending.size > LIMIT) pending.delete(pending.keys().next().value);
      // An offer missing from a page that read it in stock has left that page;
      // once it has left every such page, it has left the list. Only the two
      // single-list lanes identify a page by lane and number.
      if (SINGLE_LIST_LANES.includes(ctx.lane)) {
        for (const [id, wait] of pending) {
          if (present.has(id) || !wait.pages.delete(here) || wait.pages.size) continue;
          pending.delete(id);
          log({ itemId: id, valueCents: wait.valueCents, lane: ctx.lane, page: ctx.page,
            ...(ctx.requestId ? { requestId: ctx.requestId } : {}), stage: 'rejected',
            stockState: 'absent', sinceReturnMs: now - wait.at });
        }
      }
      for (const [id, follow] of following) {
        if (now - follow.at > FOLLOW_MS) following.delete(id);
        else if (SINGLE_LIST_LANES.includes(ctx.lane) && follow.lane === ctx.lane &&
            follow.page === ctx.page && !present.has(id)) {
          following.delete(id);
          log({ itemId: id, valueCents: follow.valueCents, lane: ctx.lane, page: ctx.page,
            ...(ctx.requestId ? { requestId: ctx.requestId } : {}), stage: 'followup',
            stockState: 'absent', sinceReturnMs: now - follow.at, reads: follow.reads });
        }
      }
      while (outSightings.size > LIMIT) outSightings.delete(outSightings.keys().next().value);
      if (changed || blipped || returnsChanged) {
        const values = {};
        if (changed) {
          for (const [id, at] of watch) if (now - Number(at) >= WATCH_MAX_AGE_MS) watch.delete(id);
          while (watch.size > LIMIT) watch.delete(watch.keys().next().value);
          values[KEY] = Object.fromEntries(watch);
        }
        if (blipped) values[BLIP_KEY] = savedBlips(now);
        if (returnsChanged) values[RETURNS_KEY] = savedReturns(now);
        write(values);
      }
      return marked;
    }

    // True when a page holds an offer awaiting confirmation: that page must be
    // sent even if it looks unchanged, so its next read confirms or rejects it
    // (an out-of-stock read on any page rejects it).
    function awaiting(items) {
      return pending.size > 0 && Array.isArray(items) && items.some(item => pending.has(item?.item_id));
    }

    // An upload carrying these restock flags was acknowledged (2xx): stop
    // flagging and retrying them.
    async function ack(ids) {
      if (!Array.isArray(ids) || !ids.length) return;
      await load();
      let acked = false;
      for (const id of ids) {
        const entry = returned.get(id);
        tried.delete(id);
        if (entry && !entry.acked) {
          entry.acked = true;
          acked = true;
        }
      }
      if (acked) write({ [RETURNS_KEY]: savedReturns(Date.now()) });
    }

    // Confirmed returns no acknowledged upload has carried yet, as items to
    // re-send on their own (flagged restocked). Each is handed out at most once
    // per `spacingMs`, and not within `spacingMs` of its confirmation (its own
    // page upload goes first).
    async function retryable(spacingMs = 15_000) {
      await load();
      const now = Date.now();
      const out = [];
      for (const [id, entry] of returned) {
        if (entry.acked || now - entry.at >= RETRY_MS || now - entry.at < spacingMs ||
            now - (tried.get(id) ?? 0) < spacingMs) continue;
        tried.set(id, now);
        out.push({ ...entry.item, restocked: true });
      }
      return out;
    }

    return { mark, awaiting, ack, retryable };
  }

  return { create, CONFIRM_MS, PENDING_MS, STEADY_MS, STEADY_READS, STEADY_GAP_MS, BLIP_LIMIT,
    BLIP_AFTER_CONFIRM_MS, RETRY_MS, WATCH_MAX_AGE_MS };
})();
