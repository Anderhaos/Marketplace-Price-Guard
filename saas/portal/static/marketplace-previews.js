/* Presentation-only switcher. Not a marketplace integration or account selector.
   All views and server-side permissions continue to use the selected WB account. */
(() => {
  'use strict';
  const themes = Object.freeze({
    wb: {label: 'Wildberries', icon: 'W', title: ''},
    ozon: {label: 'Ozon', icon: 'O', title: 'Ozon · предпросмотр дизайна'},
    yandex: {label: 'Яндекс Маркет', icon: 'М', title: 'Яндекс Маркет · предпросмотр дизайна'},
    mega: {label: 'Мегамаркет', icon: 'М', title: 'Мегамаркет · предпросмотр дизайна'},
  });
  const classes = Object.keys(themes).map(key => 'marketplace-' + key);
  const storageKey = 'nosale.marketplace-theme-preview.v1';
  const body = document.body;
  if (!body.classList.contains('marketplace-wb')) return;
  const picker = document.querySelector('.marketplace-picker');
  const label = document.getElementById('marketplace-current-label');
  const symbol = document.getElementById('marketplace-current-icon');
  const banner = document.getElementById('theme-preview-banner');
  if (!picker || !label || !symbol || !banner) return;
  const title = document.getElementById('theme-preview-title');

  function setTheme(key, save) {
    if (!Object.prototype.hasOwnProperty.call(themes, key)) key = 'wb';
    body.classList.remove(...classes);
    body.classList.add('marketplace-' + key);
    body.dataset.previewMarketplace = key;
    label.textContent = themes[key].label;
    symbol.textContent = themes[key].icon;
    symbol.className = 'marketplace-dot ' + ({
      wb:'wb-symbol', ozon:'ozon-symbol', yandex:'ym-symbol', mega:'mm-symbol',
    })[key];
    picker.querySelector('summary').setAttribute(
      'aria-label', key === 'wb'
        ? 'Маркетплейс Wildberries — рабочий кабинет, выбрать оформление'
        : themes[key].label + ' — предпросмотр оформления на данных WB, выбрать оформление'
    );
    banner.hidden = key === 'wb';
    if (key !== 'wb') title.textContent = themes[key].title;
    picker.querySelectorAll('[data-theme-preview]').forEach(button => {
      const active = button.dataset.themePreview === key;
      button.classList.toggle('is-selected', active);
      button.setAttribute('aria-pressed', String(active));
      button.querySelector('.marketplace-choice-check').textContent = active ? '✓' : '';
    });
    if (save) {
      try { window.localStorage.setItem(storageKey, key); } catch (_) { /* private browser */ }
    }
  }

  let preferred = 'wb';
  try { preferred = window.localStorage.getItem(storageKey) || 'wb'; } catch (_) { /* private browser */ }
  setTheme(preferred, false);
  picker.querySelectorAll('[data-theme-preview]').forEach(button => {
    button.addEventListener('click', () => {
      setTheme(button.dataset.themePreview, true);
      picker.open = false;
    });
  });
  document.getElementById('theme-preview-back').addEventListener('click', () => setTheme('wb', true));
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape') picker.open = false;
  });
})();
