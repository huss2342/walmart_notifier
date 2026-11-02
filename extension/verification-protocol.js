// Generated from native_verification/protocol.json; run node tools/generate-native-protocol.mjs.
var VerificationNativeProtocol = (() => {
  const spec = {
    "version": 1,
    "hostName": "com.reviewer_item_relay.verification",
    "extensionOrigin": "chrome-extension://oidgbnehgnikmniolojjkmhlpifdoiil/",
    "limits": {
      "messageBytes": 16384,
      "holdMs": 30000,
      "idleMs": 5000,
      "prepareTtlMs": 2000,
      "viewportMax": 20000,
      "dprMin": 0.5,
      "dprMax": 4
    },
    "errorCodes": [
      "unsupported-platform",
      "invalid-request",
      "invalid-origin",
      "invalid-window",
      "helper-busy",
      "desktop-locked",
      "user-busy",
      "button-held",
      "focus-changed",
      "window-changed",
      "viewport-mismatch",
      "target-obscured",
      "target-expired",
      "user-input",
      "input-failed",
      "release-failed",
      "watchdog-unavailable",
      "already-used",
      "not-prepared",
      "host-unavailable",
      "max-duration",
      "closed"
    ],
    "phases": [
      "idle",
      "prepared",
      "holding",
      "released",
      "aborted",
      "closed"
    ],
    "operations": [
      "probe",
      "prepare",
      "press",
      "status",
      "release",
      "close"
    ],
    "requestIdPattern": "[A-Za-z0-9:_-]{1,80}",
    "prepareTokenPattern": "[a-f0-9]{48}",
    "stateTokenPattern": "[a-zA-Z0-9_-]{8,128}"
  };
  const reasons = new Set(spec.errorCodes);
  const token = new RegExp('^(?:' + spec.stateTokenPattern + ')$');
  function validState(s) {
    return s && typeof s.ready === 'boolean' && (s.reason === null || reasons.has(s.reason)) &&
      spec.phases.includes(s.phase) &&
      ['pressSent', 'pressConfirmed', 'releaseConfirmed'].every(k => typeof s[k] === 'boolean') &&
      Number.isFinite(s.idleMs) && s.idleMs >= 0 && Number.isFinite(s.holdMs) && s.holdMs >= 0 &&
      (s.token === undefined || (typeof s.token === 'string' && token.test(s.token)));
  }
  return Object.freeze({ ...spec, validState });
})();
