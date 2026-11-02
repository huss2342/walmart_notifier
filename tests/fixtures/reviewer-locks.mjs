// One instance can be shared by multiple VM origins to exercise the browser's
// same-origin exclusion contract without sending any network requests.
export function reviewerLocks() {
  const tails = new Map();
  return { request(name, options, task) {
    const invoke = () => {
      if (options.signal?.aborted) throw Object.assign(new Error('Lock wait aborted.'), { name: 'AbortError' });
      return task({ name, mode: options.mode });
    };
    const result = (tails.get(name) || Promise.resolve()).then(invoke, invoke);
    tails.set(name, result.catch(() => undefined));
    return result;
  } };
}
