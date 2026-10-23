# NoSale — серверные источники рыночной аналитики (owner admin)

**Дата аудита:** 2026-10-23. **Статус:** исследование и постановка, не подключено; live API и права B2B SaaS не проверены. Актуальная продуктовая идея Smart описана в `SMART_MARKETPLACE_ADVISOR_PRODUCT_DECISION_2026-10-23.md`; два режима Standard/Smart **сохраняются**.

## 1. Бизнес-разделение источников

**A. Данные личного магазина:** авторизованный официальный WB/Ozon Seller API (реальные цены продавца, акции, заказы, начисления, удержания); это авторитетный источник *персональных* фактов, насколько определена семантика и временная задержка источника. Пользователь авторизует свой магазин, но **не настраивает AI API и чужие аналитические сервисы**. При нехватке себестоимости — явный user-provided import.

**B. Платформенные рыночные данные (лицензия NoSale, централизованно):** например, официальный платный Ozon Statistics API для внешних SaaS (товары, конкурентные цены, остатки, рейтинг/отзывы); публичный WB поиск/отчёты об анализе ниш на портале **не автоматически является машинным API с разрешением на серверную выгрузку**. Отдельно подтвердить market API и коммерческие условия. Наличие поля `sales` в рекламном описании НЕ доказательство доступности фактических продаж по конкурентному SKU; только документированный returned field.

**C. Коммерческая внешняя оценочная аналитика (лицензия NoSale):** MPSTATS API (документированные REST/JSON, в том числе `/api/analytics/v1/oz/items`, `/niche/...`; WB API `/api/wb/...`). Для неизвестных конкурентных продаж/выручки считать показатели **vendor-estimated**, а не факт WB/Ozon. Обычный MPSTATS API токен или корпоративный тариф ещё не означают право встраивать, перепродавать, переиздавать или генерировать платные customer-facing AI reports из этих данных: нужен отдельный письменный B2B agreement c `commercial use, derived outputs, distribution to tenants, caching/retention, tenant volume, pricing, attribution`. Конкретные лимиты/стоимость проверить у поставщика.
  - Moneyplace, «Маяк», SalesFinder, MarketGuru: подтверждена функциональность аналитических продуктов. Отдельное **server-to-server B2B API / white-label / redistributor use** на доступных публичных страницах не подтверждено, считать `requires_partner_agreement`. Moneyplace plugin API key для расширения ≠ юридически или технически доступный реселлерский API.
  - RDV API: подтверждён как **агрегатор работы с личными Seller API WB/Ozon/Яндекс Маркет** и унификация интеграций. Полезен для будущего расширения платформ, но **не является самостоятельным независимым источником конкурентного рынка**.
  - Sellerfox.com (нынешняя официальная документация sellerfox.com/en/docs/) относится к Amazon; не включать автоматически в shortlist российских WB/Ozon до подтверждения конкретного продукта.

**D. Контекстные исследования и справочники:** Data Insight — исследования рынка e-commerce, публичные исследования/сезонность; Bank of Russia API — официальный курс валют для документированного валютного пересчёта (не текущая цена закупки); публичные WB/Ozon seller-offer, changelog, тарифы, законодательные правила. Исследования не являются подтверждением участия SKU в промо или реального payout. В частности, Data Insight указывает ограничения на перепечатку и публичное использование материалов без письменного разрешения.

**E. Аналитический продукт NoSale:** расчёты, сценарии, оценки модели (с объяснением формул и входов). Это *производные* значения, не «пятый факт внешнего рынка».

## 2. Реестр кандидатов

| Источник | Подтверждено публично | Не подтверждено | Приоритет |
|---|---|---|---|
| Ozon Statistics API | Официальный Ozon for dev описывает платный независимый Statistics API для сторонних SaaS: ассортимент, цены, остатки, рейтинг, отзывы. Доступ по заявке `request-data@ozon.ru` | Цена/объём, договор и возвращаемые *фактические* продажи конкурентов, current 2026 availability, rate limits | **P0 переговоры** |
| MPSTATS Integration/Analytics API | REST+JSON, персональный `X-Mpstats-TOKEN`; документы Analytics Ozon содержат items, period_stats, niche category revenue/sales, позицию товара. 429 и тарифные квоты. Корпоративные планы упоминают API для собственных BI | Право resale/customer facing derived data, квоты, SLA и происхождение/model of estimates | **P0 B2B переговоры+pilot** |
| Moneyplace | Внешняя аналитика, WB/Ozon/Magnit и интеграции API-кабинета, плагин | Independent server-to-server supplier license, redistrib terms | P1 |
| «Маяк», SalesFinder, MarketGuru | Рыночная аналитика WB/Ozon; доступ к личным Seller API для внутренней аналитики | External B2B API, resale license, datamodel/refresh/errors, цена | P1 |
| RDV API | Документированный единый интерфейс Seller integrations WB/Ozon/Яндекс | Third-party market intelligence ≠ seller account data | P2 для платформенного расширения |
| Data Insight | Отраслевые обзоры/исследования | лицензия на повторное использование в платных AI отчётах, автоматический feed | P2 background |
| ЦБ РФ | Государственная SOAP API валютных курсов | доменная релевантность к конкретной закупке seller COGS | P2 unit economics |

## 3. NoSale Owner Admin — «Источники данных»

Экран `Owner → Данные и аналитика → Провайдеры` (виден только владельцу/доверенному системному админу):
- карточка: `source/provider/type [official-market | seller-private | licensed-vendor | reference | editorial]`, supported marketplace, supported capabilities/fields, auth status, `contract_type`, `redistribution_allowed`, `derived_reports_allowed`, `tenant_scope`, retention, attribution, region, contract expiry;
- server-side credential vault (API keys не в браузере, не у конечного продавца, ни в LLM); оператор видит masked ref и может обновить/проверить подключение;
- coverage matrix: `exact|observed|derived|estimated|unknown`, currency/units, timestamps, update interval, time lag, revisions, original field/schema, `last_success`, `last_error`, quota hit, rate limit, cost per API call;
- quotas/budget: тариф, фиксированная месячная плата, per-call cost, допустимые SKU/categories, LLM cost, cache hit, limit per tenant, общий лимит сервиса; остановить provider автоматически при превышении бюджета;
- job scheduler: один сбор **общих законно переиспользуемых рыночных данных** на площадку/категорию/временной период; shared cache только если лицензионно разрешено. Данные отдельных продавцов из WB/Ozon никогда не смешивать/передавать между tenants;
- provider adapter validator: response schema, observedAt, native permissions, unavailable/degraded/blocked, sampling QA, corrections; optional manual CSV research upload маркировать `editorial`, не `seller fact`.
- data output audit: какая рекомендация использовала какую точку данных; related IDs/hash/revision и формулу. Возможность отказаться от использования vendor в конкретном Smart report.

**Никакой автономной логики промо-изменений на основании рыночной аналитики**. Нет seller-consent/доказательства текущего seller discount ⇒ `unknown` ⇒ рекомендация без write. Existing Standard сохраняется 1:1.

## 4. Рекомендации и вероятности

- Анализ `safe | risks | unknown` должен различать *доказательство источника скидки* и *предполагаемую бизнес-выгоду*. Источник seller-funded/WB-funded подтверждается официальными данными и правилами, не похожестью цены конкурентов.
- Для оценки выгодности сравнивать `contribution_margin_before = actual_or_expected_seller_payout_before - variable_cost_before` и `contribution_margin_after`; `required_sales_volume_multiplier = margin_before / margin_after` если обе величины положительные и прочие факторы постоянны. При нулевой/отрицательной новой unit-margin никакого «роста продаж окупит потери» автоматически не заявлять. Сначала данные пользователя о costs, комиссии/удержания в источнике.
- Внешние оценки объёма, поискового спроса, цены конкурента и сезонности помогают **обосновать сценарий**, а не подтвердить эффект акции. При отсутствии экспериментальной/калиброванной истории 60/70% *вероятность прибыли* не отображать.
- Сравнение провайдеров на перекрывающихся собственных SKU с достоверными продажами продавца (только при условиях договоров) → bias/MAPE по категории/датам, коррекция качества, confidence interval. Если оба вендора используют один upstream/алгоритм — согласие между ними не является независимым подтверждением.
- Обязательно хранить методы расчёта, versioned model, uncertainty range, данные/права, timestamp, флаг `vendor-estimated`; при просроченной лицензии или данных — источник выключен.

## 5. Приоритет реализации

1. Source Registry schema, provenance, quality policy, owner-admin status/authorization contract — без внешних API пока.
2. Ozon Statistics API коммерческие условия и paid sandbox/образцы (если по договору), MPSTATS B2B agreement+минимальный live пилот 1–2 категории; принять решение по фактической стоимости/правам, не цене сайта.
3. Market-side historical price/category trends; проставить `vendor-estimated`, сделать первые сравнения с true seller facts без утечек данных; выводить продавцу доказательства.
4. Детерминированная unit economics + сценарии изменения количества продаж; AI narrative через общий runtime `platform-kits`, две-три сводки/сутки, все внешние источники NoSale owner-managed.
5. Один независимый дополнительный licensed vendor, если по измеримым QA он улучшает покрытие и рентабельность.
6. Новый Smart auto-write только в отдельном выпуске после принятого fail-closed business action guard, явного согласия и shadow validation.

**Отдельная проверка:** определить экономику и окупаемость `Smart` при предложенной стоимости >500 руб/месяц; корпоративные лицензии могут быть дороже стоимости многократных LLM вызовов. Не делать оплату крупных подписок до проверки прав и sample.

## 6. Источники на дату исследования

- Ozon developer официальный Statistics API: https://dev.ozon.ru/news/256-Novyi-API-dlia-polucheniia-dannykh-Statistics-API/
- Ozon developer о независимых аналитических SaaS и платном доступе: https://dev.ozon.ru/start/258-Ozon-zapustil-Statistics-API-dlia-polucheniia-dannykh-o-prodazhakh-na-marketpleise
- MPSTATS integration API: https://mpstats.io/integrations/docs/description/
- MPSTATS Analytics Ozon OpenAPI: https://mpstats.io/integrations/analytics-oz/
- MPSTATS corporate API note: https://mpstats.io/landing/segment/agencies
- WB Seller API general: https://dev.wildberries.ru/knowledge-base
- WB marketplace search queries / «Джем»: https://seller.wildberries.ru/instructions/ru/ru/material/search-analytics-report
- WB «Анализ ниш»: https://seller.wildberries.ru/instructions/ru/ru/material/questions-about-product-niche-analytics
- Moneyplace: https://moneyplace.kontur-f.ru/
- Маяк: https://mayak.bz/edu
- SalesFinder: https://salesfinder.ru/features
- MarketGuru: https://marketguru.io/docs/obzor-servisa-marketguru/
- RDV API: https://rdv-market.ru/rdv-api/
- Data Insight: https://datainsight.ru/
- Банк России официальный API: https://www.cbr.ru/development/DWS/

Ни один внешний ключ не получен, соглашение не подписано, платные тарифы не опробованы, NoSale production и Standard не менялись.
