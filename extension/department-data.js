// Public department facet records embedded in the reviewer HTML. No private
// application objects, guessed IDs, external endpoints or recursive traversal.
var ReviewerDepartmentData = (() => {
  const HOME = 'https://www.walmart.com/reviews/claim-product';
  const idPattern = /^[1-9]\d{0,15}$/;
  const text = value => typeof value === 'string' ? value.trim().replace(/\s+/g, ' ') : '';

  function reviewer(raw) {
    const u = new URL(raw);
    if (u.protocol !== 'https:' || u.hostname !== 'www.walmart.com' || u.username ||
        u.password || u.port || u.hash || !/^\/reviews\/claim-product\/?$/.test(u.pathname)) throw new Error('scope');
    return u;
  }

  function root(raw) {
    const u = reviewer(raw);
    for (const [key, value] of u.searchParams) {
      if (key === 'page' && /^\d+$/.test(value) && +value >= 1 && +value <= 100) continue;
      if (['q', 'queryNups', 'facet'].includes(key) && !value.trim()) continue;
      if (key === 'catId' && ['', '0'].includes(value)) continue;
      if (key === 'affinityOverride' && ['', 'default'].includes(value)) continue;
      throw new Error('filtered-root');
    }
    return u;
  }

  function destination(raw, parent) {
    try {
      root(parent);
      const u = reviewer(raw);
      if ([...u.searchParams.keys()].length !== 1 || !idPattern.test(u.searchParams.get('catId') || '')) return null;
      return `${HOME}?catId=${u.searchParams.get('catId')}`;
    } catch { return null; }
  }

  function catalogue(doc, currentUrl) {
    try {
      const current = root(currentUrl);
      const raw = doc.querySelector('#__NEXT_DATA__')?.textContent;
      if (!raw || raw.length > 10_000_000) return null;
      const page = JSON.parse(raw).props?.pageProps;
      const variables = page?.initialSearchQueryVariables;
      if (typeof variables?.query !== 'string' || variables.query.trim() ||
          !['', '0'].includes(variables.catId) || typeof variables.facet !== 'string' || variables.facet.trim() ||
          variables.page !== Number(current.searchParams.get('page') || 1)) return null;
      const modules = page.initialData?.contentLayout?.modules;
      if (!Array.isArray(modules)) return null;
      const facets = modules.filter(m => m?.type === 'SearchSortFilterModule')
        .flatMap(m => Array.isArray(m.configs?.allSortAndFilterFacets) ? m.configs.allSortAndFilterFacets : [])
        .filter(f => f?.type === 'cat_id' && f.name === 'Departments');
      if (facets.length !== 1 || !Array.isArray(facets[0].values) ||
          !facets[0].values.length || facets[0].values.length > 2000) return null;
      const ids = new Set(), names = new Set(), found = [];
      // Only immediate root values: children are deliberately not traversed.
      for (const value of facets[0].values) {
        const label = text(value?.name), id = value?.id;
        const params = new URLSearchParams(value?.url);
        if (!label || label.length > 150 || /[\u0000-\u001f\u007f]/.test(value.name) ||
            typeof id !== 'string' || !idPattern.test(id) || value.type !== 'cat_id' || value.isSelected === true ||
            params.getAll('cat_id').length !== 1 || params.get('cat_id') !== id ||
            ids.has(id) || names.has(label.toLocaleLowerCase('en-US'))) return null;
        ids.add(id); names.add(label.toLocaleLowerCase('en-US'));
        found.push({ kind: 'department', label, parentUrl: HOME, url: `${HOME}?catId=${id}` });
      }
      return found;
    } catch { return null; }
  }

  function applied(doc, label, expectedUrl = null) {
    try {
      if (expectedUrl) {
        const validated = destination(expectedUrl, HOME);
        if (!validated) return false;
        const raw = doc.querySelector('#__NEXT_DATA__')?.textContent;
        if (!raw || raw.length > 10_000_000) return false;
        const category = JSON.parse(raw).props?.pageProps?.initialData?.searchResult?.catInfo;
        if (category?.catId !== new URL(validated).searchParams.get('catId') || !text(category.name)) return false;
        // Applied chips can shorten root names: Clothing, Shoes & Accessories
        // becomes Clothing. The fresh page's exact category ID supplies identity.
        label = category.name;
      }
      const roots = [...doc.querySelectorAll('[aria-label="Sort and Filter section"]')]
        .filter(el => el.isConnected !== false && !el.hidden && el.getAttribute('aria-hidden') !== 'true');
      if (roots.length !== 1 || !text(label)) return false;
      const prefix = `Filter by ${text(label)} applied, activate to change`;
      const buttons = [...roots[0].querySelectorAll('button, [role="button"]')]
        .filter(el => el.isConnected !== false && !el.hidden && el.getAttribute('aria-hidden') !== 'true');
      return buttons.filter(el => {
        const name = text(el.getAttribute('aria-label'));
        return name.startsWith(prefix) && /^\s*(?:,\s*filter applied)?\s*$/.test(name.slice(prefix.length));
      }).length === 1;
    } catch { return false; }
  }

  return Object.freeze({ catalogue, destination, applied });
})();
