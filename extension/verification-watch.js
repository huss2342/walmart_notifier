// Passive detection on Walmart pages outside the scanner's own routes.
// Reviewer/blocked documents already monitor verification in content.js.
(() => {
  if (/^\/reviews\/|^\/blocked/.test(location.pathname)) return;
  let reported = false;
  let pending = false;
  let timer = null;
  const inspect = () => {
    timer = null;
    if (!chrome.runtime?.id) { observer.disconnect(); return; }
    const reason = verificationReason();
    if (!reason) { reported = false; return; }
    if (reported || pending) return;
    pending = true;
    try {
      Promise.resolve(chrome.runtime.sendMessage({ type: 'reviewer:verification', reason }))
        .then(reply => { reported = reply?.ok === true; })
        .catch(() => { reported = false; })
        .finally(() => {
          pending = false;
          if (!reported && timer === null) timer = setTimeout(inspect, 2000);
        });
    } catch {
      pending = false;
      if (timer === null) timer = setTimeout(inspect, 2000);
    }
  };
  const observer = new MutationObserver(() => {
    if (timer === null) timer = setTimeout(inspect, 100);
  });
  observer.observe(document, { childList: true, subtree: true, characterData: true,
    attributes: true, attributeFilter: ['style', 'class', 'hidden', 'src', 'title'] });
  chrome.runtime.onMessage.addListener((message, _sender, respond) => {
    if (message?.type !== 'reviewer:challenge-probe') return undefined;
    respond({ challenge: Boolean(verificationReason()) });
    return undefined;
  });
  inspect();
})();
