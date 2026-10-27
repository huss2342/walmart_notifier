// Inspect the site's normal controls. No injected input telemetry or changes
// to Walmart's verification scripts, cookies, or browser identity.
function verificationReason(doc = document, currentLocation = location) {
  if (/^\/blocked(?:\/|$)/i.test(currentLocation.pathname)) return 'redirect';
  const markers = doc.querySelectorAll(
    '#px-captcha, #px-captcha-wrapper, iframe[src*="captcha.px-cloud.net"], ' +
    'iframe[src*="px-captcha"], iframe[title*="human challenge" i]'
  );
  for (const marker of markers) {
    const style = getComputedStyle(marker);
    if (marker.getClientRects().length && style.display !== 'none' &&
        style.visibility !== 'hidden' && style.visibility !== 'collapse') return 'dom-marker';
  }
  const text = doc.body?.innerText || '';
  const press = /press\s*(?:&|and)\s*hold/i.test(text);
  return press && (/robot\s+or\s+human\s*\?/i.test(text) ||
    /(?:confirm|verify)\s+(?:that\s+)?you(?:'re|\s+are)\s+(?:a\s+)?human/i.test(text))
    ? 'text' : null;
}

function comparablePageUrl(raw, base) {
  const url = new URL(raw, base);
  url.hash = '';
  if (url.searchParams.get('page') === '1') url.searchParams.delete('page');
  // The regular portal accepts both /claim-product and /claim-product?q=.
  // Pager links may omit these empty search fields; real filters still match
  // exactly, so a link cannot silently drop a nonempty search restriction.
  for (const key of ['q', 'queryNups']) {
    if (url.searchParams.get(key) === '') url.searchParams.delete(key);
  }
  // Walmart can append this neutral mode after a site-control click. Without
  // normalization a rendered destination looked stalled and caused a reload.
  const affinity = url.searchParams.getAll('affinityOverride');
  if (affinity.length && affinity.every(value => value === 'default')) {
    url.searchParams.delete('affinityOverride');
  }
  url.searchParams.sort();
  return url.toString();
}

function pageControlRoot() {
  return document.querySelector('[data-automation-id="page-number"]')
    ?.closest('ul, ol, nav, [role="navigation"]') ||
    document.querySelector('nav[aria-label*="pagination" i]');
}

function placeholderPageHref(href) {
  if (href == null) return true;
  const value = href.trim();
  return value === '' || value === '#' ||
    /^javascript:\s*(?:void\s*\(\s*0\s*\)|void\s+0)\s*;?\s*$/i.test(value);
}

function nativeControlKey(target, control) {
  const url = new URL(target, location.href);
  const page = url.searchParams.get('page') || '1';
  url.searchParams.delete('page');
  const label = (control?.getAttribute('aria-label') || control?.textContent || '').trim();
  const kind = !placeholderPageHref(control?.getAttribute('href')) ? 'link' :
    /^(?:go to )?next(?: page)?$/i.test(label) ? 'next' :
      /^(?:go to )?previous(?: page)?$/i.test(label) ? 'previous' : 'number';
  // Number buttons are separate destinations, not one shared capability. A
  // single no-op must not disable every other page or its preparatory scroll.
  // Legacy unscoped "number" failures therefore cannot suppress these keys.
  return `${comparablePageUrl(url, location.href)}|${kind === 'number' ? `number:${page}` : kind}`;
}

function findPageControl(target, page) {
  const pager = pageControlRoot();
  // A site's pager wrapper can change without changing its destinations. An
  // exact URL match is safe anywhere in the document; label-only buttons still
  // require a recognized pager so unrelated numbered buttons cannot be clicked.
  // Numbered anchors can use click handlers without href, and page-number
  // markers can themselves be controls. Prefer interactive descendants before
  // their marker wrappers so a click reaches the site's actual handler.
  const scopedControls = [...new Set([
    ...(pager?.querySelectorAll?.('a, button, [role="button"], [role="link"]') || []),
    ...(pager?.querySelectorAll?.('[data-automation-id="page-number"]') || [])
  ])];
  const controls = new Set([
    ...document.querySelectorAll('a[href]'),
    ...scopedControls
  ]);
  const visible = [...controls].filter(control => {
    if (control.disabled || control.getAttribute('aria-disabled') === 'true') return false;
    const style = getComputedStyle(control);
    return control.getClientRects().length && style.display !== 'none' &&
      style.visibility !== 'hidden' && style.visibility !== 'collapse';
  });
  for (const control of visible) {
    const href = control.getAttribute('href');
    if (!placeholderPageHref(href)) {
      try {
        if (comparablePageUrl(href, location.href) === comparablePageUrl(target, location.href)) {
          return control;
        }
      } catch { /* An invalid or unrelated link is never a navigation target. */ }
    }
  }
  // Label-only controls act on the existing search. They cannot be used for
  // a different query/filter, even if a caller supplies a matching page number.
  const expected = new URL(location.href);
  expected.searchParams.set('page', String(page));
  if (comparablePageUrl(expected, location.href) !== comparablePageUrl(target, location.href)) return null;
  const scoped = visible.filter(control => scopedControls.includes(control) &&
    placeholderPageHref(control.getAttribute('href')));
  const current = Number(new URL(location.href).searchParams.get('page') || 1);
  const marked = Number(pager?.querySelector?.('[aria-current="page"], [aria-current="true"]')
    ?.textContent?.trim());
  if (current > 0 && page === current + 1 && marked === current) {
    const next = scoped.find(control => /^(?:go to )?next(?: page)?$/i.test(
      (control.getAttribute('aria-label') || control.textContent || '').trim()));
    if (next) return next;
  }
  for (const control of scoped) {
    const label = (control.getAttribute('aria-label') || control.textContent || '').trim();
    if (new RegExp(`^(?:(?:go to )?page\\s+)?${page}$`, 'i').test(label)) return control;
  }
  return null;
}

let pageControlScroll = null;

function cancelPageControlScroll() {
  if (pageControlScroll) window.cancelAnimationFrame(pageControlScroll.frame);
  pageControlScroll = null;
}

// Prepare the real next control within the existing wait. Vary the short eased
// movement and landing point; this remains DOM scrolling, not human input.
function scrollPageControlIntoView(control, canContinue = () => true) {
  cancelPageControlScroll();
  if (document.visibilityState !== 'visible') return false;
  try {
    const rect = control?.getBoundingClientRect();
    if (!rect || !(window.innerHeight > 0 && window.innerWidth > 0)) return false;
    if (rect.top >= 0 && rect.bottom <= window.innerHeight &&
        rect.left >= 0 && rect.right <= window.innerWidth) return false;
    const reducedMotion = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
    const root = document.scrollingElement;
    if (reducedMotion || !root || !window.requestAnimationFrame ||
        rect.left < 0 || rect.right > window.innerWidth) {
      control.scrollIntoView({ behavior: reducedMotion ? 'instant' : 'smooth',
        block: 'center', inline: 'nearest' });
      return true;
    }
    const start = window.scrollY;
    const max = Math.max(0, root.scrollHeight - window.innerHeight);
    const clamp = value => Math.max(0, Math.min(max, value));
    const target = clamp(start + (rect.top + rect.bottom) / 2 -
      window.innerHeight * (0.45 + Math.random() * 0.15));
    const duration = 600 + Math.random() * 300;
    const correction = Math.random() < 0.3 ? 6 + Math.random() * 12 : 0;
    const peak = clamp(target + Math.sign(target - start) *
      Math.min(correction, Math.abs(target - start) * 0.04));
    const split = peak === target ? 1 : 0.82;
    const started = window.performance.now();
    const animation = { frame: null };
    pageControlScroll = animation;
    const ease = t => t * t * (3 - 2 * t);
    const step = timestamp => {
      if (pageControlScroll !== animation) return;
      try {
        if (document.visibilityState !== 'visible' || control.isConnected === false ||
            window.matchMedia?.('(prefers-reduced-motion: reduce)').matches || !canContinue()) {
          cancelPageControlScroll();
          return;
        }
        const progress = Math.max(0, Math.min(1, (timestamp - started) / duration));
        const top = progress <= split
          ? start + (peak - start) * ease(progress / split)
          : peak + (target - peak) * ease((progress - split) / (1 - split));
        // Avoid the page's CSS scroll-behavior applying a second animation.
        window.scrollTo({ top, left: window.scrollX, behavior: 'instant' });
        if (progress < 1) animation.frame = window.requestAnimationFrame(step);
        else pageControlScroll = null;
      } catch { cancelPageControlScroll(); }
    };
    animation.frame = window.requestAnimationFrame(step);
    return true;
  } catch { cancelPageControlScroll(); return false; } // Never cancel the scan.
}

// Counts only: discard labels/URLs in this function. Inspect at most once per
// document, and cap the candidates so diagnostics cannot stall a large page.
let pagerInspected = false;
function inspectPageControls(target, page) {
  if (pagerInspected) return null;
  pagerInspected = true;
  const pager = pageControlRoot();
  const counts = { recognizedPagerCount: pager ? 1 : 0, exactHrefCount: 0,
    visibleExactHrefCount: 0, queryMismatchCount: 0, targetLabelButtonCount: 0,
    nextLabelButtonCount: 0, scopedButtonCount: 0, roleButtonCount: 0,
    pagerElementCount: 0, pagerAnchorCount: 0, pagerButtonCount: 0, pagerInputCount: 0,
    pagerNumericTextCount: 0, pagerTargetTextCount: 0, pagerTargetAriaCount: 0,
    pagerHiddenCount: 0, pagerMarkerCount: 0, pagerPlaceholderHrefCount: 0,
    pagerTargetPlaceholderCount: 0, pagerPageHrefCount: 0, pagerOtherHrefCount: 0 };
  for (const element of pager?.querySelectorAll?.('*') || []) {
    if (counts.pagerElementCount >= 200) break;
    counts.pagerElementCount += 1;
    if (element.tagName === 'A') counts.pagerAnchorCount += 1;
    if (element.tagName === 'BUTTON') counts.pagerButtonCount += 1;
    if (element.tagName === 'INPUT') counts.pagerInputCount += 1;
    if (element.getAttribute('data-automation-id') === 'page-number') counts.pagerMarkerCount += 1;
    const text = (element.textContent || '').trim();
    if (/^\d{1,3}$/.test(text)) counts.pagerNumericTextCount += 1;
    if (text === String(page)) counts.pagerTargetTextCount += 1;
    const label = (element.getAttribute('aria-label') || '').trim();
    if (new RegExp(`^(?:(?:go to )?page\\s+)?${page}$`, 'i').test(label)) {
      counts.pagerTargetAriaCount += 1;
    }
    if (element.tagName === 'A') {
      const href = element.getAttribute('href');
      if (placeholderPageHref(href)) {
        counts.pagerPlaceholderHrefCount += 1;
        if (new RegExp(`^(?:(?:go to )?page\\s+)?${page}$`, 'i').test(label || text)) {
          counts.pagerTargetPlaceholderCount += 1;
        }
      } else {
        try {
          if (new URL(href, location.href).searchParams.has('page')) counts.pagerPageHrefCount += 1;
          else counts.pagerOtherHrefCount += 1;
        } catch { counts.pagerOtherHrefCount += 1; }
      }
    }
    const style = getComputedStyle(element);
    if (!element.getClientRects().length || style.display === 'none' ||
        style.visibility === 'hidden' || style.visibility === 'collapse') counts.pagerHiddenCount += 1;
  }
  const destination = new URL(target, location.href);
  let inspected = 0;
  for (const control of document.querySelectorAll('a[href], button, [role="button"]')) {
    if (++inspected > 1000) break;
    const href = control.getAttribute('href');
    if (href) {
      try {
        const candidate = new URL(href, location.href);
        if (comparablePageUrl(candidate, location.href) === comparablePageUrl(destination, location.href)) {
          counts.exactHrefCount += 1;
          const style = getComputedStyle(control);
          if (!control.disabled && control.getAttribute('aria-disabled') !== 'true' &&
              control.getClientRects().length && style.display !== 'none' &&
              style.visibility !== 'hidden' && style.visibility !== 'collapse') {
            counts.visibleExactHrefCount += 1;
          }
        } else if (candidate.origin === destination.origin && candidate.pathname === destination.pathname &&
                   Number(candidate.searchParams.get('page') || 1) === page) {
          counts.queryMismatchCount += 1;
        }
      } catch { /* Invalid URLs are not diagnostic data. */ }
    } else {
      if (pager?.contains(control)) counts.scopedButtonCount += 1;
      if (control.getAttribute('role') === 'button') counts.roleButtonCount += 1;
      const label = (control.getAttribute('aria-label') || control.textContent || '').trim();
      if (new RegExp(`^(?:(?:go to )?page\\s+)?${page}$`, 'i').test(label)) {
        counts.targetLabelButtonCount += 1;
      }
      if (/^(?:go to )?next(?: page)?$/i.test(label)) counts.nextLabelButtonCount += 1;
    }
  }
  return counts;
}
