// Page snapshot log (Nov 19 freshness study, 2.32.7): every direct read and
// every tab page delivered, with each product's id, position, price and stock,
// plus the freshness probes' records (fast-reads.js). Measurement only: it
// never requests anything from Walmart. Records are batched to the local
// notifier's /page-snapshots, which validates them field by field and keeps a
// size-bounded, rotating data/page-snapshots.jsonl. Searches appear only as the
// 32-bit hash of their normalised query, never as text.
// The buffer is bounded (oldest records dropped first, counted in the next
// batch); an old backend without the route (HTTP 404) pauses uploads for 10 min.
var ReviewerPageSnapshots = (() => {
  const FLUSH_MS = 20_000;
  const FLUSH_RECORDS = 150;
  const MAX_BUFFER = 1_500;
  const MAX_BATCH = 400;
  const MISSING_PAUSE_MS = 10 * 60_000;
  const RETRY_MS = 60_000;

  function create({ upload, setTimer = setTimeout, clearTimer = clearTimeout, now = () => Date.now() }) {
    let buffer = [];
    let dropped = 0;
    let timer = null;
    let flushing = false;
    let pausedUntil = 0;
    const stats = { sent: 0, failures: 0, dropped: 0, lastStatus: 0 };

    // One timer at a time; a full batch moves a waiting timer forward to now.
    let urgent = false;
    let generation = 0;
    function schedule(delay) {
      if (timer !== null) {
        if (delay > 0 || urgent) return;
        clearTimer(timer);
      }
      urgent = delay <= 0;
      const mine = ++generation;
      timer = setTimer(() => {
        if (mine !== generation) return; // A replaced timer that fired anyway.
        timer = null;
        urgent = false;
        flush().catch(() => {});
      }, Math.max(0, delay));
      timer?.unref?.(); // Test runtimes only; the extension worker has no unref.
    }

    function add(record) {
      if (!record || typeof record !== 'object' || now() < pausedUntil) return;
      buffer.push(record);
      if (buffer.length > MAX_BUFFER) {
        const excess = buffer.length - MAX_BUFFER;
        buffer = buffer.slice(excess);
        dropped += excess;
        stats.dropped += excess;
      }
      schedule(buffer.length >= FLUSH_RECORDS ? 0 : FLUSH_MS);
    }

    async function flush() {
      if (flushing || !buffer.length) return false;
      flushing = true;
      const records = buffer.slice(0, MAX_BATCH);
      const gap = dropped;
      let status = 0;
      try {
        try {
          const result = await upload({ schemaVersion: 1, ...(gap ? { dropped: gap } : {}), records });
          status = Number.isInteger(result?.status) ? result.status : result?.ok ? 200 : 0;
        } catch (error) {
          status = Number.isInteger(error?.status) ? error.status : 0;
        }
        stats.lastStatus = status;
        if (status >= 200 && status < 300) {
          buffer = buffer.slice(records.length);
          dropped = Math.max(0, dropped - gap);
          stats.sent += records.length;
          if (buffer.length) schedule(buffer.length >= FLUSH_RECORDS ? 0 : FLUSH_MS);
          return true;
        }
        stats.failures += 1;
        if (status === 404) {
          // A backend without this route gets nothing for a while.
          stats.dropped += buffer.length;
          buffer = [];
          dropped = 0;
          pausedUntil = now() + MISSING_PAUSE_MS;
          return false;
        }
        if (status === 400 || status === 413) {
          // A refused batch is never resent; the rest still goes.
          buffer = buffer.slice(records.length);
          dropped += records.length;
          stats.dropped += records.length;
          if (buffer.length) schedule(FLUSH_MS);
          return false;
        }
        schedule(RETRY_MS); // Network error or 5xx: keep the records (bounded) and retry.
        return false;
      } finally {
        flushing = false;
      }
    }

    return { add, flush, status: () => ({ ...stats, buffered: buffer.length, pausedUntil }) };
  }

  return { create, FLUSH_MS, FLUSH_RECORDS, MAX_BUFFER, MAX_BATCH };
})();
