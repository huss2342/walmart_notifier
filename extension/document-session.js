// Own document-local intake, reading proofs and late observations. Revoking a
// lease releases its work immediately; late continuations cannot release a new
// generation's operation or acknowledge a different results page.
var ReviewerDocumentSession = (() => {
  function create({ href, setTimer = setTimeout, clearTimer = clearTimeout }) {
    const role = { active: false, tabId: null, primaryTabId: null, generation: 0, auxiliary: null };
    const reading = { controller: null, started: null, completed: null, proof: null,
      intakeKey: null, attemptKey: null, workMs: 0, failures: 0 };
    const visible = { acknowledgedIds: new Set(), timer: null, retries: 0 };
    const pending = new Set();
    let epoch = 0;
    let operation = null;
    let disposed = false;
    const cancelled = () => new Error('Reviewer document work was cancelled.');
    const capture = () => ({ epoch, generation: role.generation, href: href() });
    const current = token => !disposed && token?.epoch === epoch &&
      token.generation === role.generation && token.href === href();

    function begin() {
      if (disposed || operation) return null;
      operation = capture();
      return operation;
    }
    function finish(token) {
      if (!operation || operation !== token) return false;
      operation = null;
      return true;
    }
    function revoke() {
      epoch += 1;
      operation = null;
      for (const cancel of [...pending]) cancel();
    }
    function dispose() { disposed = true; revoke(); }

    function request(invoke, timeoutMs) {
      if (disposed) return Promise.reject(cancelled());
      const token = capture();
      return new Promise((resolve, reject) => {
        let timer;
        let settled = false;
        const complete = (error, value) => {
          if (settled) return;
          settled = true;
          clearTimer(timer);
          pending.delete(cancel);
          if (error) reject(error); else resolve(value);
        };
        const cancel = () => complete(cancelled());
        pending.add(cancel);
        timer = setTimer(() => complete(new Error('The reviewer coordinator did not reply before its deadline.')),
          Math.max(1_000, Math.min(180_000, Number(timeoutMs) || 15_000)));
        try {
          Promise.resolve(invoke()).then(value => {
            if (current(token)) complete(null, value); else cancel();
          }, error => complete(error));
        } catch (error) { complete(error); }
      });
    }
    return { role, reading, visible, capture, current, begin, finish, revoke, dispose, request };
  }
  return Object.freeze({ create });
})();
