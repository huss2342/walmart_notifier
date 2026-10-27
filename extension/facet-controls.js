// Use the reviewer's rendered filter controls. This helper never derives facet
// IDs, reads private application state, or requests undocumented endpoints.
(function installReviewerFacetControls(global) {
  'use strict';

  const MAX_OPTIONS = 2000;
  const MAX_EXPANSIONS = 24;
  const OPEN_ATTEMPTS = 40;
  const SETTLE_ATTEMPTS = 8;
  const SETTLE_MS = 180;
  const CONTROL_SELECTOR = 'button, [role="button"]';
  const INPUT_SELECTOR = 'input[type="radio"], input[type="checkbox"]';

  function failure(code) {
    const error = new Error(`Reviewer filter: ${code}`);
    error.code = code;
    return error;
  }

  function visible(element, doc) {
    if (!element || element.isConnected === false) return false;
    const getStyle = doc.defaultView?.getComputedStyle?.bind(doc.defaultView);
    // Styled radio inputs may themselves have no rectangle, so inspect their
    // containing rendered tree instead of rejecting accessible hidden inputs.
    for (let node = element; node && node !== doc; node = node.parentElement) {
      if (node.hidden || node.getAttribute?.('aria-hidden') === 'true') return false;
      const style = getStyle?.(node);
      if (style?.display === 'none' || style?.visibility === 'hidden' ||
          style?.visibility === 'collapse') return false;
    }
    return true;
  }

  function enabled(element) {
    return !element.disabled && element.getAttribute('aria-disabled') !== 'true';
  }

  function label(element) {
    return String(element.value || '');
  }

  function ownInput(input, kind) {
    const name = String(input.name || input.getAttribute('name') || '').trim();
    if (kind === 'department') {
      if (String(input.type || '').toLowerCase() !== 'radio') return false;
      if (/^(?:Departments?|All departments)$/i.test(name)) return true;
      // Applied facets name the selected parent radio after the category (Home,
      // Furniture, etc.). Its sibling child group and Show More share the same
      // popover. Recognize that header so it cannot look like another facet and
      // truncate discovery; a leaf's header also proves the panel mounted.
      const header = input.closest?.('[role="radiogroup"][aria-label="Departments"]');
      return input.checked === true && name === label(input) &&
        Boolean(header?.closest('[data-dca-ctx="filterPopover"]'));
    }
    // Walmart names each brand checkbox after the brand itself (for example,
    // name="NIDA"), rather than using a shared Brand group name. Recognize its
    // rendered facet wrapper/popover instead of accepting arbitrary checkboxes.
    const option = input.closest?.('[data-facet-option]');
    return String(input.type || input.getAttribute('type') || '').toLowerCase() === 'checkbox' &&
      option?.getAttribute('data-facet-option') === `brand--${label(input)}` &&
      Boolean(option.closest('[data-dca-ctx="filterPopover"]'));
  }

  function inputs(root, kind, doc) {
    const values = [...root.querySelectorAll(INPUT_SELECTOR)]
      .filter(input => ownInput(input, kind) && visible(input, doc));
    if (values.length > MAX_OPTIONS) throw failure('option-overflow');
    return values;
  }

  function controls(root, doc) {
    return [...root.querySelectorAll(CONTROL_SELECTOR)].filter(control => visible(control, doc));
  }

  function moreButtons(root, doc) {
    return controls(root, doc).filter(control => enabled(control) &&
      /^show more$/i.test((control.textContent || '').trim()));
  }

  function findTrigger(root, kind, doc) {
    const testIds = kind === 'department' ? ['filter_chip_cat_id'] : ['filter_chip_brand'];
    const observed = testIds.flatMap(id => [...root.querySelectorAll(`[data-testid="${id}"]`)]
      .flatMap(chip => controls(chip, doc))).filter(enabled);
    if (observed.length > 1) throw failure('ambiguous-control');
    if (observed.length === 1) return observed[0];
    const name = kind === 'department' ? 'Departments?' : 'Brands?(?: \\(\\d+\\))?';
    const pattern = new RegExp(`^Filter by ${name} (?:not applied|applied), activate to change`, 'i');
    const matching = controls(root, doc).filter(control => enabled(control) &&
      pattern.test(control.getAttribute('aria-label') || ''));
    if (matching.length > 1) throw failure('ambiguous-control');
    if (!matching.length) throw failure('missing-control');
    return matching[0];
  }

  function findPanel(root, kind, doc) {
    const options = inputs(root, kind, doc);
    if (!options.length) return null;
    let common = options[0].parentElement;
    while (common && !options.every(option => common.contains(option))) common = common.parentElement;
    if (!common || !root.contains(common)) return null;

    // Radio groups and Show More are siblings on Walmart. Ascend to their
    // smallest shared owner, while refusing a container shared with another
    // open facet. Never search the document/header Departments menu.
    let panel = common;
    for (let owner = common; owner && root.contains(owner); owner = owner.parentElement) {
      const otherInputs = [...owner.querySelectorAll(INPUT_SELECTOR)]
        .filter(input => visible(input, doc) && !ownInput(input, kind));
      if (otherInputs.length) break;
      const more = moreButtons(owner, doc);
      if (more.length > 1) throw failure('ambiguous-expansion');
      if (more.length === 1) return owner;
      panel = owner;
      if (owner === root) break;
    }
    return panel;
  }

  function filterRoot(doc) {
    const roots = [...doc.querySelectorAll('[aria-label="Sort and Filter section"]')]
      .filter(root => visible(root, doc));
    if (roots.length > 1) throw failure('ambiguous-filter-section');
    if (!roots.length) throw failure('missing-filter-section');
    return roots[0];
  }

  function refreshEnvironment(env) {
    if (env.root.isConnected !== false && env.trigger.isConnected !== false) return;
    // React can replace the filter section while its popover opens. Reacquire
    // only the same uniquely identified scope and kind. A replacement chip was
    // not clicked by this operation and must never inherit cleanup ownership.
    env.opened = false;
    env.root = filterRoot(env.doc);
    env.trigger = findTrigger(env.root, env.kind, env.doc);
  }

  async function environment(args) {
    const doc = args.document || global.document;
    const kind = args.kind || args.selection?.kind || 'department';
    if (kind !== 'department' && kind !== 'brand') throw failure('unsupported-kind');
    if (!doc?.querySelectorAll) throw failure('missing-document');
    const canAct = args.canAct || (() => true);
    const pause = args.pause || (milliseconds => new Promise(resolve => setTimeout(resolve, milliseconds)));
    const guard = async () => { if (!await canAct()) throw failure('cancelled'); };
    let env;
    const wait = async () => {
      await guard();
      await pause(SETTLE_MS);
      await guard();
      refreshEnvironment(env);
    };
    await guard();
    const root = filterRoot(doc);
    const trigger = findTrigger(root, kind, doc);
    env = { doc, kind, root, trigger, guard, wait, opened: false };
    return env;
  }

  async function open(env) {
    await env.guard();
    refreshEnvironment(env);
    const { doc, kind } = env;
    let panel = findPanel(env.root, kind, doc);
    const expanded = env.trigger.getAttribute('aria-expanded');
    if (panel && (expanded === 'true' || expanded === null)) return panel;
    // The chip may become expanded before its options mount. Do not toggle an
    // opening menu closed, and allow its asynchronous panel up to 7.2 seconds.
    // Each bounded wait retains the caller's cancellation/verification guard.
    if (expanded !== 'true') {
      await env.guard();
      refreshEnvironment(env);
      if (env.trigger.getAttribute('aria-expanded') !== 'true') {
        if (!visible(env.trigger, doc) || !enabled(env.trigger)) throw failure('missing-control');
        env.trigger.click();
        env.opened = true;
      }
    }
    for (let attempt = 0; attempt < OPEN_ATTEMPTS; attempt++) {
      await env.wait();
      panel = findPanel(env.root, kind, doc);
      if (panel) return panel;
    }
    throw failure('missing-panel');
  }

  async function expand(env, panel) {
    for (let round = 0; round < MAX_EXPANSIONS; round++) {
      await env.guard();
      refreshEnvironment(env);
      panel = findPanel(env.root, env.kind, env.doc);
      if (!panel) throw failure('missing-panel');
      const more = moreButtons(panel, env.doc);
      if (more.length > 1) throw failure('ambiguous-expansion');
      if (!more.length) return inputs(panel, env.kind, env.doc);
      const beforeCount = inputs(panel, env.kind, env.doc).length;
      if (!visible(more[0], env.doc) || !enabled(more[0])) throw failure('missing-expansion');
      more[0].click();
      let changed = false;
      for (let attempt = 0; attempt < SETTLE_ATTEMPTS; attempt++) {
        await env.wait();
        const current = findPanel(env.root, env.kind, env.doc);
        if (!current) continue;
        panel = current;
        const after = inputs(panel, env.kind, env.doc);
        // Walmart may replace Show More with Show Less before its options
        // finish rendering. A changed button alone cannot prove expansion.
        if (after.length > beforeCount) {
          changed = true;
          break;
        }
      }
      if (!changed) throw failure('expansion-stalled');
    }
    // Even if every batch grew, an unexpanded remainder is not full coverage.
    if (moreButtons(panel, env.doc).length) throw failure('expansion-overflow');
    return inputs(panel, env.kind, env.doc);
  }

  async function close(env) {
    if (!env?.opened) return;
    // Cancellation means a human, verification screen, or newer operation owns
    // the page now; avoid a final cleanup click in that changed context.
    await env.guard();
    if (!visible(env.trigger, env.doc) || !enabled(env.trigger)) return;
    if (env.trigger.getAttribute('aria-expanded') === 'false') return;
    env.trigger.click();
    env.opened = false;
    await env.wait();
  }

  async function discover(args = {}) {
    const env = await environment(args);
    try {
      const panel = await open(env);
      if (!panel) throw failure('missing-panel');
      const options = await expand(env, panel);
      const parent = new URL(args.currentUrl || env.doc.location?.href || global.location?.href);
      parent.searchParams.delete('page');
      parent.hash = '';
      const seen = new Set();
      return options.filter(input => {
        const value = label(input);
        if (!value.trim() || input.checked || /^(?:All departments|All brands)$/i.test(value.trim()) || seen.has(value)) return false;
        seen.add(value);
        return true;
      }).map(input => ({ kind: env.kind, label: label(input), parentUrl: parent.toString() }));
    } finally {
      await close(env);
    }
  }

  async function select(args = {}) {
    let env;
    let selected = false;
    try {
      const wanted = String(args.selection?.label || '');
      if (!wanted.trim()) throw failure('missing-selection');
      env = await environment(args);
      const panel = await open(env);
      if (!panel) throw failure('missing-panel');
      const options = await expand(env, panel);
      const matching = options.filter(input => label(input) === wanted);
      if (!matching.length) throw failure('missing-selection');
      if (matching.length > 1) throw failure('ambiguous-selection');
      if (matching[0].checked) throw failure('already-selected');
      await env.guard();
      const input = matching[0];
      if (!visible(input, env.doc) || !enabled(input)) throw failure('missing-selection');
      // Styled department radios apply selection from their label's press
      // handler. Clicking the hidden input can be cancelled without navigation.
      // Only use a label that unambiguously owns this exact matched radio.
      const owner = env.kind === 'department' ? input.closest?.('label') : null;
      if (owner) {
        const controls = [...owner.querySelectorAll(INPUT_SELECTOR)];
        const forId = owner.getAttribute('for');
        if (controls.length !== 1 || controls[0] !== input ||
            (forId && forId !== input.id) ||
            (owner.control !== undefined && owner.control !== input)) throw failure('ambiguous-selection');
        if (!panel.contains(owner) || !visible(owner, env.doc) || !enabled(owner)) throw failure('missing-selection');
      }
      if (env.kind === 'department' && typeof args.activate === 'function') {
        if (!owner) throw failure('missing-selection');
        await env.guard();
        const result = await args.activate(owner);
        if (result?.ok !== true) throw failure(result?.reason === 'cancelled' ? 'cancelled' : 'facet-error');
      } else {
        (owner || input).click();
      }
      selected = true;
      // The caller verifies changed result cards/URL before treating a lane as
      // ready. A click alone cannot prove Walmart completed a navigation.
      return { ok: true };
    } catch (error) {
      return { ok: false, reason: error.code || 'facet-error' };
    } finally {
      if (!selected && env) {
        try { await close(env); } catch { /* Cancellation forbids cleanup clicks. */ }
      }
    }
  }

  global.ReviewerFacetControls = Object.freeze({ discover, select });
})(globalThis);
