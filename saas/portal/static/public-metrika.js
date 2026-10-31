/* NoSale public-only analytics. No emails, PINs or WB tokens are transmitted. */
(function () {
  'use strict';
  var element = document.currentScript;
  var id = element && element.dataset ? Number(element.dataset.metrikaId) : 0;
  if (!Number.isSafeInteger(id) || id <= 0 || location.pathname !== '/') return;
  var consent = document.getElementById('nosale-consent');
  var allow = document.getElementById('nosale-allow-analytics');
  var deny = document.getElementById('nosale-deny-analytics');
  var storageKey = 'nosale_public_analytics_consent';
  var state = '';
  try {
    state = localStorage.getItem(storageKey) || '';
  } catch (_) {
    state = '';
  }
  function initMetrika() {
    if (document.getElementById('nosale-metrika-script')) return;
    var win = window;
    win.ym = win.ym || function () { (win.ym.a = win.ym.a || []).push(arguments); };
    win.ym.l = Date.now();
    var script = document.createElement('script');
    script.id = 'nosale-metrika-script';
    script.async = true;
    script.src = 'https://mc.yandex.ru/metrika/tag.js?id=' + id;
    document.head.appendChild(script);
    win.ym(id, 'init', {
      clickmap: false,
      webvisor: false,
      trackLinks: true,
      accurateTrackBounce: true
    });
    document.querySelectorAll('a[data-nosale-goal]').forEach(function (link) {
      link.addEventListener('click', function () {
        if (typeof win.ym === 'function') win.ym(id, 'reachGoal', link.dataset.nosaleGoal);
      });
    });
  }
  function setConsent(value) {
    try { localStorage.setItem(storageKey, value); } catch (_) {}
    if (consent) consent.hidden = true;
    if (value === 'allow') initMetrika();
  }
  if (state === 'allow') {
    initMetrika();
  } else if (state !== 'deny' && consent) {
    consent.hidden = false;
  }
  if (allow) allow.addEventListener('click', function () { setConsent('allow'); });
  if (deny) deny.addEventListener('click', function () { setConsent('deny'); });
})();
