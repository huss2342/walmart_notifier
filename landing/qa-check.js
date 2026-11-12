() => ({
  title: document.title,
  viewport: innerWidth,
  overflow: document.documentElement.scrollWidth > innerWidth,
  headings: [...document.querySelectorAll('h1,h2')].map(el => el.textContent),
  joins: [...document.querySelectorAll('[data-discord-join]')].map(el => ({
    text: el.textContent.trim(),
    href: el.href,
    height: el.getBoundingClientRect().height,
  })),
  images: [...document.images].map(el => ({
    src: el.getAttribute('src'),
    loaded: el.complete && el.naturalWidth > 0,
    width: el.getBoundingClientRect().width,
    visible: getComputedStyle(el).display !== 'none',
  })),
  focused: { tag: document.activeElement.tagName, text: document.activeElement.textContent.trim().slice(0, 80) },
  navigation: performance.getEntriesByType('navigation').map(entry => ({
    domContentLoadedMs: Math.round(entry.domContentLoadedEventEnd),
    loadMs: Math.round(entry.loadEventEnd),
  })),
})
