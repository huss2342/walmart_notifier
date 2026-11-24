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
  const match = picks.match(new RegExp(`<li class="pick[^"]*">(?:(?!</li>\\s*<li class="pick)[\\s\\S])*?<h3>${title}[\\s\\S]*?</div>\\s*</li>`));
  assert.ok(match, `missing pick block ${title}`);
  return match[0];
};
const chips = (block) => [...block.matchAll(/<li(?: class="on")?>(?:<svg[\s\S]*?<\/svg>)?([^<]+)/g)]
  .map(([, name]) => name.trim());

const priceChannels = [...layout.matchAll(/"price-[^"]+":\s*"([^"]+)"/g)].map(([, name]) => name);
const categoryChannels = [...policy.match(/PRODUCT_CATEGORY_TOPICS = \(([\s\S]*?)\n\)/)[1]
  .matchAll(/\(\s*"([a-z-]+)",/g)].map(([, name]) => name);

test('price examples are real value channels and the count matches the bot', () => {
  assert.equal(priceChannels.length, 6);
  const block = pickBlock('Price ranges');
  assert.match(block, /Six value ranges, under \$10 to \$200\+/);
  const shown = chips(block);
  assert.ok(shown.length >= 1 && shown.length <= 3, 'keep the phone sample small');
  for (const name of shown) assert.ok(priceChannels.includes(name), name);
});

test('category examples are real category channels and the count matches the bot', () => {
  assert.equal(categoryChannels.length, 11);
  assert.ok(categoryChannels.includes('furniture') && categoryChannels.includes('pets'));
  const block = pickBlock('Categories');
  assert.match(block, /Eleven, from furniture to pets/);
  const shown = chips(block);
  assert.ok(shown.length >= 1 && shown.length <= 3, 'keep the phone sample small');
  for (const name of shown) assert.ok(categoryChannels.includes(name), name);
});

test('Pro-only features are stated once as Pro and auto-claim is labelled coming soon', () => {
  assert.match(picks, /<p class="picks-pro">Price, category and request alerts are Pro\./);
  assert.equal((picks.match(/\bPro\b/g) || []).length, 2, 'one Pro line for the whole section');
  const soon = pickBlock('Auto-claim');
  assert.match(soon, /Coming soon/);
  assert.match(soon, /Not live yet/);
  // The value and category channel groups and requests really are Pro-only in the bot.
  assert.match(layout, /Category\("pro-price", "PRO - BY VALUE", "pro"\)/);
  assert.match(layout, /Category\("pro-type", "PRO - BY CATEGORY", "pro"\)/);
  assert.match(read('../../discord_bridge/request_commands.py'),
    /Personal item requests are available to current Pro members only\./);
});

test('the request example uses the real command, option and $N+ suffix', () => {
  const commands = read('../../discord_bridge/request_commands.py');
  assert.match(commands, /name="request"/);
  assert.match(commands, /name="add"/);
  assert.match(commands, /item="Item keywords \(commas for bulk\), Walmart URL, or item ID; optional \$100\+ suffix"/);
  assert.match(commands, /min_value="Optional minimum item value in USD/);
  const block = pickBlock('Item requests');
  assert.match(block, /<b>\/request add<\/b> <span>item:<\/span> couch \$200\+/);
  assert.match(block, /minimum value/);
  assert.match(block, /DM/);
});

test('phone section stays lean: no Join button, no fine print, no long chip walls', () => {
  assert.doesNotMatch(picks, /data-discord-join/);
  assert.doesNotMatch(picks, /class="fine"/);
  assert.ok((picks.match(/<li(?: class="on")?>/g) || []).length <= 4);
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
