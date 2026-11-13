import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const source = () => readFileSync(new URL('../public/campaign.js', import.meta.url), 'utf8');

function destination(search, now = '2026-11-13T00:00:00Z') {
  const link = { href: 'https://discord.gg/VzwNcSgWBk', dataset: {} };
  vm.runInNewContext(source(), {
    URLSearchParams,
    Date: { now: () => Date.parse(now), parse: Date.parse },
    location: { search },
    document: { querySelectorAll: () => [link] },
  });
  return link.href;
}

test('both ad variants always use the permanent Discord invite', () => {
  const a = destination('?utm_source=reddit&utm_content=a');
  const b = destination('?utm_source=reddit&utm_content=b');
  assert.equal(a, 'https://discord.gg/VzwNcSgWBk');
  assert.equal(b, 'https://discord.gg/VzwNcSgWBk');
  for (const value of [a, b]) {
    const url = new URL(value);
    assert.equal(url.origin, 'https://discord.gg');
    assert.match(url.pathname, /^\/[A-Za-z0-9-]+$/);
  }
});

test('every current Join button uses the permanent invite without a routing script', () => {
  const html = readFileSync(new URL('../public/index.html', import.meta.url), 'utf8');
  const links = [...html.matchAll(/<a\b[^>]*data-discord-join[^>]*href="([^"]+)"/g)];
  assert.equal(links.length, 2);
  for (const [, href] of links) assert.equal(href, 'https://discord.gg/VzwNcSgWBk');
  assert.doesNotMatch(html, /<script\b/i);
});

test('organic visits and hostile or inherited-property parameters keep the default', () => {
  for (const search of ['', '?utm_content=c', '?utm_content=constructor',
    '?utm_content=__proto__', '?utm_content=https://example.com', '?utm_content=%3Cscript%3E']) {
    assert.equal(destination(search), 'https://discord.gg/VzwNcSgWBk');
  }
});

test('expired campaign links fall back to the permanent server invitation', () => {
  for (const variant of ['a', 'b']) {
    assert.equal(destination(`?utm_content=${variant}`, '2026-11-20T00:00:00Z'),
      'https://discord.gg/VzwNcSgWBk');
  }
});
