# NoSale public site / SEO

Site: https://nosale.pro. One public marketing homepage, no CMS or extra marketing routes.

## Routes

- Anonymous GET /: server-rendered one-page public site with features, how it works, tariffs and FAQ.
- Authenticated GET /: existing seller dashboard; protected WB actions unchanged.
- /login/: clean email-first login. Preserve the special owner /login/?next=/admin/ flow.
- /offer/, /privacy/, /contacts/: existing public legal pages.
- /robots.txt and /sitemap.xml: only public links are allowed/listed.
- Every private HTML response includes X-Robots-Tag noindex, nofollow, noarchive.
- Sitemap/canonical/Open Graph use NOSALE_PUBLIC_BASE_URL.
- Prices are read from OwnerTariffOffer. Do not claim sales are enabled while disabled.

## Menu

Возможности -> /#features; Как работает -> /#how; Тарифы -> /#pricing;
Вопросы -> /#faq; Войти and Начать бесплатно -> /login/.
No extra public FAQ, features, blog, tariffs routes.

## Analytics rollout

Create separate Yandex Metrica counter for NoSale. Set NOSALE_YM_COUNTER_ID
to its actual numeric identifier in private env only. Optional ownership tags:
NOSALE_YANDEX_VERIFICATION and NOSALE_GOOGLE_VERIFICATION. Do not reuse IDs from
BeClients and other sites.

Public landing requests consent before Yandex script is loaded; no analytics
runs on login, registration, seller dashboard, billing, WB tokens or admin.
Do not send emails, PINs, WB tokens or private shop data as analytics goals.
Webvisor and clickmap stay off. After enabling counter, verify the public
pageview and start_trial link goal in the real owner account.

## Acceptance

- Anonymous / returns 200, canonical, SEO description, social metadata.
- Authenticated / retains seller dashboard.
- /login/ and owner /login/?next=/admin/ keep PIN security and workflow.
- Robots and sitemap return 200 with valid contents, no private URLs in sitemap.
- Public and private robots headers are correct.
- Test 320px, mobile and desktop navigation, no JS errors.
- After deployment, verify E5 legacy redirect, new HTTPS, all services healthy.
- WB worker is not rebuilt or restarted during the public-site release.
- Submit sitemap to Yandex Webmaster and Google Search Console once connected.
- Rankings and indexing require subsequent observation; SEO markup alone is not proof.
