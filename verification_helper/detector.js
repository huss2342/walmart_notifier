// Only signal visible challenge episodes. No page text or browser data is sent.
(() => {
  let episode = 0;
  let visibleSince = 0;
  let absentSince = 0;
  let active = false;
  let sending = false;
  function inspect() {
    const marker = [...document.querySelectorAll('#px-captcha,#px-captcha-wrapper')].some(el => {
      const s = getComputedStyle(el);
      return el.getClientRects().length && s.display !== 'none' &&
        !['hidden', 'collapse'].includes(s.visibility) && s.opacity !== '0';
    });
    const now = Date.now();
    if (!marker) {
      visibleSince = 0;
      absentSince ||= now;
      // Brief widget replacements must not manufacture new retry episodes.
      if (now - absentSince >= 5000) active = false;
      return;
    }
    absentSince = 0;
    visibleSince ||= now;
    if (now - visibleSince < 1000 || sending) return;
    if (!active) { active = true; episode++; }
    sending = true;
    chrome.runtime.sendMessage({ type: 'verification-observed', episode })
      .catch(() => undefined).finally(() => { sending = false; });
  }
  // A low-frequency read also recovers from enabling/restarting the worker while
  // an unchanged challenge is already visible. No extra site requests are made.
  setInterval(inspect, 2000);
  inspect();
})();
