// A document-owned port gives Chrome a nonzero --parent-window HWND. This page
// is private to the extension and is never exposed as a web-accessible resource.
(() => {
  const session = new URL(location.href).searchParams.get('session');
  if (!/^[0-9a-f-]{36}$/.test(session || '')) return;
  const worker = chrome.runtime.connect({ name: `reviewer-native:${session}` });
  let host;
  worker.onMessage.addListener(message => {
    if (message?.type === 'start' && !host) {
      try {
        host = chrome.runtime.connectNative('com.reviewer_item_relay.verification');
        host.onMessage.addListener(response => worker.postMessage({ response }));
        host.onDisconnect.addListener(() => { void chrome.runtime.lastError; worker.disconnect(); });
        worker.postMessage({ type: 'ready' });
      } catch { worker.disconnect(); }
    } else if (message?.type === 'request' && host) {
      try { host.postMessage(message.request); } catch { worker.disconnect(); }
    }
  });
  worker.onDisconnect.addListener(() => { void chrome.runtime.lastError; host?.disconnect(); });
  addEventListener('pagehide', () => host?.disconnect(), { once: true });
})();
