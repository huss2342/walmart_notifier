import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const read = (path) => readFileSync(new URL(path, import.meta.url), 'utf8');
const html = read('../public/index.html');
const layout = read('../../discord_bridge/layout.py');
const policy = read('../../discord_bridge/policy.py');

const section = (id) => {
  const match = html.match(new RegExp(`<section[^>]*aria-labelledby="${id}"[\\s\\S]*?</section>`));
  assert.ok(match, `missing section ${id}`);
  return match[0];
};
const picks = section('picks-title');
const pickBlock = (title) => {
  const match = picks.match(new RegExp(`<div class="pick[^"]*">\\s*<h3>${title}[\\s\\S]*?</div>`));
  assert.ok(match, `missing pick block ${title}`);
  return match[0];
};

test('price channels on the page are the real Discord value channels', () => {
  const names = [...layout.matchAll(/"price-[^"]+":\s*"([^"]+)"/g)].map(([, name]) => name);
  assert.equal(names.length, 6);
  const shown = [...pickBlock('Price ranges').matchAll(/<li[^>]*>(?:<svg[\s\S]*?<\/svg>)?([^<]+)/g)]
    .map(([, name]) => name.trim());
  assert.deepEqual(shown, names);
});

test('category channels on the page are the real Discord category channels', () => {
  const block = policy.match(/PRODUCT_CATEGORY_TOPICS = \(([\s\S]*?)\n\)/)[1];
  const names = [...block.matchAll(/\(\s*"([a-z-]+)",/g)].map(([, name]) => name);
  assert.equal(names.length, 11);
  const shown = [...pickBlock('Categories').matchAll(/<li[^>]*>(?:<svg[\s\S]*?<\/svg>)?([^<]+)/g)]
    .map(([, name]) => name.trim());
  assert.deepEqual(shown, names);
});

test('Pro-only features are labelled Pro and auto-claim is labelled coming soon', () => {
  for (const title of ['Price ranges', 'Categories', 'Item requests']) {
    assert.match(pickBlock(title), /<span class="tag">Pro<\/span>/);
  }
  const soon = pickBlock('Auto-claim');
  assert.match(soon, /Coming soon/);
  assert.match(soon, /Not live yet/);
  // The value and category channel groups really are Pro-only in the bot.
  assert.match(layout, /Category\("pro-price", "PRO - BY VALUE", "pro"\)/);
  assert.match(layout, /Category\("pro-type", "PRO - BY CATEGORY", "pro"\)/);
  assert.match(layout, /CHANNEL_DISPLAY_NAMES[\s\S]*FREE_ROUTE_KEY: "🆓24h-delayed-alerts"/);
  assert.match(picks, /🆓24h-delayed-alerts/);
});

test('the request example uses the real command and option names', () => {
  const commands = read('../../discord_bridge/request_commands.py');
  assert.match(commands, /name="request"/);
  assert.match(commands, /name="add"/);
  assert.match(commands, /name="listed"/);
  assert.match(commands, /async def add\(interaction, item: item_query, min_value: minimum_value = None\)/);
  const block = pickBlock('Item requests');
  assert.match(block, /\/request add/);
  assert.match(block, /\/request listed/);
  assert.match(block, /<span>item<\/span>/);
  assert.match(block, /<span>min_value<\/span>/);
});

test('page keeps the CSP contract: no scripts, inline styles or data: URLs', () => {
  for (const page of ['index.html', 'privacy.html', 'terms.html']) {
    const source = read(`../public/${page}`);
    assert.doesNotMatch(source, /<script\b/i, page);
    assert.doesNotMatch(source, /\sstyle\s*=/i, page);
    assert.doesNotMatch(source, /<style\b/i, page);
    assert.doesNotMatch(source, /data:/i, page);
  }
  assert.match(html, /<meta name="darkreader-lock">/);
  assert.match(read('../public/styles.css'), /@media\(prefers-color-scheme:dark\)/);
});
