// Shared read-only observations. Policies intentionally retain each consumer's
// existing route, visibility and readiness rules; this adds no input behavior.
var ReviewerVerificationObservation = (() => {
  // Keep this callback self-contained: Chrome/CUA serialize it into a page world.
  function observePage(policy = 'operator', doc = document, current = location) {
    const ids = '#px-captcha, #px-captcha-wrapper';
    const frames = 'iframe[src*="captcha.px-cloud.net"],iframe[src*="px-captcha"],' +
      'iframe[title*="human challenge" i]';
    const blockedPrefix = /^\/blocked(?:\/|$)/i.test(current.pathname);
    if (policy === 'passive' && blockedPrefix) return { reason: 'redirect' };
    const driver = policy === 'driver';
    const passive = policy === 'passive';
    const reviewerRoute = driver ? /^\/reviews\/claim-product\/?$/.test(current.pathname) :
      /^\/reviews\/claim-product(?:\/|$)/.test(current.pathname);
    const blocked = driver ? /^\/blocked\/?$/.test(current.pathname) :
      /^\/blocked(?:\/|$)/.test(current.pathname);
    const eligible = current.protocol === 'https:' && current.hostname === 'www.walmart.com' &&
      (!driver || !current.port) && (reviewerRoute || blocked);
    if (!passive && !eligible) return { eligible: false };
    function visible(element) {
      if (!element?.getClientRects().length) return false;
      if (!passive) {
        const rect = element.getBoundingClientRect();
        if (driver ? rect.width <= 0 || rect.height <= 0 : !(rect.width > 0 && rect.height > 0)) return false;
      }
      for (let node = element, depth = 0; node && depth < (driver ? 20 : 1);
        node = node.parentElement, depth++) {
        const style = getComputedStyle(node);
        if (style.display === 'none' || ['hidden', 'collapse'].includes(style.visibility) ||
            (!passive && style.opacity === '0')) return false;
      }
      return true;
    }
    if (passive) {
      if ([...doc.querySelectorAll(`${ids}, ${frames}`)].some(visible)) return { reason: 'dom-marker' };
      const text = doc.body?.innerText || '';
      const press = /press\s*(?:&|and)\s*hold/i.test(text);
      return { reason: press && (/robot\s+or\s+human\s*\?/i.test(text) ||
        /(?:confirm|verify)\s+(?:that\s+)?you(?:'re|\s+are)\s+(?:a\s+)?human/i.test(text))
        ? 'text' : null };
    }
    const markers = [...doc.querySelectorAll(ids)].filter(visible);
    const dialogs = driver ? [] : [...doc.querySelectorAll('[role="dialog"]')].filter(element =>
      visible(element) && /robot\s+or\s+human/i.test(
        element.querySelector('h1, h2, h3')?.textContent || ''));
    const heading = driver ? [...doc.querySelectorAll('[role="dialog"] h1,[role="dialog"] h2')]
      .some(element => visible(element) && /robot\s+or\s+human/i.test(element.textContent || '')) :
      dialogs.length > 0;
    const frame = [...doc.querySelectorAll(frames)].some(visible);
    const text = driver ? '' : doc.body?.innerText || '';
    const pressText = !driver && /press\s*(?:&|and)\s*hold/i.test(text) &&
      /(?:robot\s+or\s+human|(?:verify|confirm).{0,30}human)/i.test(text);
    const challenge = blocked || heading || frame || markers.length > 0 || pressText;
    const root = dialogs[0] || markers[0];
    const candidates = driver ? markers : root ? [...new Set([
      ...[...root.querySelectorAll('iframe')].map(frame => frame.parentElement),
      ...markers.filter(marker => marker === root || root.contains(marker)),
    ])] : [];
    const targets = new Map();
    for (const container of candidates) {
      if (!visible(container)) continue;
      const rect = container.getBoundingClientRect();
      if (rect.width < 200 || rect.width > 700 || rect.height < 50 || rect.height > 250) continue;
      const point = { x: Math.round(rect.x + rect.width * 0.6),
        y: Math.round(rect.y + rect.height * 0.5) };
      if (point.x < 0 || point.y < 0 || point.x >= doc.documentElement.clientWidth ||
          point.y >= doc.documentElement.clientHeight) continue;
      const hit = doc.elementFromPoint(point.x, point.y);
      if (hit && (hit === container || container.contains(hit))) targets.set(`${point.x}:${point.y}`, point);
    }
    const target = targets.size === 1 ? [...targets.values()][0] : null;
    const productNodes = [...doc.querySelectorAll(driver ? 'main a[href*="/ip/"]' : 'a[href*="/ip/"]')];
    const products = driver ? Number(productNodes.some(visible)) : productNodes.filter(visible).length;
    const empty = visible(doc.getElementById('search-item-stack-no-results-header'));
    if (driver) return { eligible, challenge, clock: performance.timeOrigin,
      ready: !blocked && !challenge && (products > 0 || empty), target,
      viewport: { width: innerWidth, height: innerHeight, dpr: devicePixelRatio } };
    return { eligible, reviewerRoute, blockedRedirect: blocked, challengeVisible: challenge,
      layout: dialogs.length ? 'modal' : blocked ? 'blocked' : challenge ? 'page' : 'none',
      productLinks: products, emptyResults: empty,
      reviewerReady: reviewerRoute && !challenge && (products > 0 || empty), target };
  }
  return { observePage };
})();

// Node operator tooling consumes the same function without a bundler or eval.
if (typeof module !== 'undefined' && module.exports) module.exports = ReviewerVerificationObservation;
