import fs from 'node:fs';
import { pathToFileURL } from 'node:url';

export function renderNativeProtocol(spec) {
  return '// Generated from native_verification/protocol.json; run node tools/generate-native-protocol.mjs.\n' +
    `var VerificationNativeProtocol = (() => {\n  const spec = ${JSON.stringify(spec, null, 2).replaceAll('\n', '\n  ')};\n` +
    `  const reasons = new Set(spec.errorCodes);\n` +
    `  const token = new RegExp('^(?:' + spec.stateTokenPattern + ')$');\n` +
    `  function validState(s) {\n` +
    `    return s && typeof s.ready === 'boolean' && (s.reason === null || reasons.has(s.reason)) &&\n` +
    `      spec.phases.includes(s.phase) &&\n` +
    `      ['pressSent', 'pressConfirmed', 'releaseConfirmed'].every(k => typeof s[k] === 'boolean') &&\n` +
    `      Number.isFinite(s.idleMs) && s.idleMs >= 0 && Number.isFinite(s.holdMs) && s.holdMs >= 0 &&\n` +
    `      (s.token === undefined || (typeof s.token === 'string' && token.test(s.token)));\n` +
    `  }\n  return Object.freeze({ ...spec, validState });\n})();\n`;
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const spec = JSON.parse(fs.readFileSync(new URL('../native_verification/protocol.json', import.meta.url)));
  const path = new URL('../extension/verification-protocol.js', import.meta.url);
  const rendered = renderNativeProtocol(spec);
  if (process.argv.includes('--check')) {
    if (fs.readFileSync(path, 'utf8') !== rendered) throw new Error('Native protocol asset is out of date');
  } else fs.writeFileSync(path, rendered);
}
