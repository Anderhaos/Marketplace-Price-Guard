/* Mark only notification rows actually seen on screen. No read side effects on GET. */
(() => {
  const center = document.querySelector('.notice-center[data-viewed-url]');
  const csrf = document.querySelector('.notice-center-head input[name="csrfmiddlewaretoken"]')?.value;
  if (!center || !csrf || !('IntersectionObserver' in window)) return;
  const nodes = center.querySelectorAll('.notice-row[data-notice-source][data-notice-id]');
  if (!nodes.length) return;

  const pending = new Map();
  let scheduled = false;
  let inflight = false;
  const schedule = () => {
    if (scheduled) return;
    scheduled = true;
    window.setTimeout(() => { scheduled = false; send(); }, 200);
  };
  async function send() {
    if (inflight || !pending.size || document.visibilityState !== 'visible') return;
    const batch = [...pending.values()];
    pending.clear();
    inflight = true;
    let success = false;
    try {
      const res = await fetch(center.dataset.viewedUrl, {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf },
        body: JSON.stringify({
          events: batch.filter(x => x.source === 'event').map(x => x.id),
          reports: batch.filter(x => x.source === 'report').map(x => x.id)
        })
      });
      if (!res.ok) throw new Error('read receipt not accepted');
      const result = await res.json();
      if (!Number.isSafeInteger(result.unread) || result.unread < 0) throw new Error('invalid count');
      success = true;
      for (const row of batch) {
        row.node.querySelector('.notice-new')?.remove();
        row.node.removeAttribute('data-notice-source');
        row.node.removeAttribute('data-notice-id');
      }
      const count = result.unread;
      const navCount = document.querySelector('.nav-notification-count');
      if (navCount) {
        navCount.textContent = String(count);
        navCount.style.display = count === 0 ? 'none' : '';
      }
      document.querySelector('.nav-notifications')?.setAttribute('aria-label', 'Уведомления, непрочитано: ' + count);
      const heading = center.querySelector('.notice-center-head .compact-page-count');
      if (heading) {
        heading.textContent = count ? count + ' новых тем' : '';
        heading.style.display = count === 0 ? 'none' : '';
      }
      const markAll = center.querySelector('.notice-center-head form');
      if (markAll) markAll.style.display = count === 0 ? 'none' : '';
    } catch (_error) {
      // Retain "Новое" on an HTTP/network failure. A reload can retry.
    } finally {
      inflight = false;
      if (success && pending.size) schedule();
    }
  }
  const observer = new IntersectionObserver(entries => {
    if (document.visibilityState !== 'visible') return;
    for (const entry of entries) {
      if (!entry.isIntersecting) continue;
      const node = entry.target;
      const source = node.dataset.noticeSource;
      const id = Number(node.dataset.noticeId);
      if (!['event', 'report'].includes(source) || !Number.isSafeInteger(id) || id < 1) continue;
      pending.set(source + ':' + id, { source, id, node });
      observer.unobserve(node);
    }
    if (pending.size) schedule();
  }, { threshold: 0.45 });
  nodes.forEach(node => observer.observe(node));
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible' && pending.size) schedule();
  });
})();
