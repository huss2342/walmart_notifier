(() => {
  // Temporary campaign invites expire after this test; keep future joins working.
  if (Date.now() >= Date.parse('2026-11-19T23:00:00Z')) return;

  const invites = new Map([
    ['a', 'https://discord.gg/5nUpFYyUj'],
    ['b', 'https://discord.gg/CwFh53XD'],
  ]);
  const destination = invites.get(new URLSearchParams(location.search).get('utm_content'));
  if (!destination) return;

  for (const link of document.querySelectorAll('[data-discord-join]')) {
    link.href = destination;
  }
})();
