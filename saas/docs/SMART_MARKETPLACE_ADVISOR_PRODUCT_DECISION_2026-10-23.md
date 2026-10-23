# NoSale Smart Marketplace Advisor — продуктовая концепция и контракт источников

> **ВАЖНОЕ УТОЧНЕНИЕ 23.10.2026:** главная дифференциация Smart — не покупная аналитика и не просто AI-текст, а [собственная событийная история, персональные сравнительные группы, проверенные результаты рекомендаций и (только когда договорно разрешено) коллективное обучение](./FIRST_PARTY_COLLECTIVE_INTELLIGENCE_DECISION_2026-10-23.md). При конфликте требований относительно кросс-магазинных данных/AI обучения применять новый документ. Ozon Developer Agreement п.6.1 запрещает обучение AI/ML на данных продавцов из API. Standard остаётся нетронутым; Smart изначально read-only рекомендации.

Дата: **2026-10-23**. Статус: **исследование/ТЗ, не реализовано**. Согласованная новая постановка уточняет и имеет приоритет над спорными пунктами `DUAL_PROTECTION_MODES_SPEC_2026-10-23.md`; детали кэширования см. `MINIBOOK_AI_REUSE_AUDIT_2026-10-23.md`. `saas/AGENTS.md` соблюдать; legacy и работающий Standard не менять.

## 1. Принцип «NoSale знает лишь то, что удалось подтвердить»

- **Факты о магазине — исключительно реальные ответы авторизованного официального API маркетплейса**, документированные правила маркетплейса или явно импортированные и помеченные **данные пользователя** (например, себестоимость). Нельзя додумывать комиссии, финансирование акций, долю продавца, прогноз спроса или создавать значения отсутствующих полей.
- **Косвенные данные не превращать в факты**: если API отдаёт только скидку продавца, нельзя делать вывод о полной цене покупателя или расходах по бонусной программе. «Ноль» и «нет данных» различать.
- Все наблюдения снабжать provenance: `platform/storeId/sku/sourceType/endpoint/rawField/observedAt/effectiveAt/requestId/dataVersion/currency/units/permissions/age`. Сохранять разрешённый raw proof или нормализованное свидетельство, ссылку на условия и версию правил, видеть расхождения между источниками.
- Форумы, аналитические статьи и сообщения продавцов могут создавать гипотезы и тестовые сценарии, **но не могут доказывать** состояние чужого магазина. Не использовать недокументированные методы, cookie scraping, обход авторизации.
- Для точного денежного эффекта нужны реальные финансовые операции (после их появления в отчётах), а для прибыли — ещё себестоимость и расходы продавца, которых API маркетплейса может не предоставлять.

## 2. Два режима продукта

| | Standard — сохранить 1:1 | Smart — новый режим |
|---|---|---|
| Прежний ориентир цены | 500 ₽/мес. — **не финально утверждено** | выше после расчёта расходов и правил |
| Контроль | текущий процентный лимит **скидки продавца** и существующие WB API guard/confirmation | отдельная политика, более широкий анализ; текущая защита по явно согласованному fallback |
| Информация | существующие товары, цены и скидки | официальные товары, акции, продажи, финансы, остатки, комиссии, реклама (только при подтверждённом разрешённом API) |
| AI | нет | включён в тариф, без пользовательских API ключей |
| Отчёты | текущие события | 2–3 AI-сводки/день, локальное время + срочные алерты от детерминированного монитора |
| Воздействие | текущий технически доступный способ изменения скидки | версия 1 **только рекомендации и мониторинг**; автоматическое изменение только отдельно после shadow/QA, явного согласия и документированного endpoint |
| Если данных нет | текущая политика | `unknown`, «недостаточно данных», без мнимой гарантии |

**Не смешивать 2–3 AI-анализов/день с критическим мониторингом цен** — price guard работает независимо и чаще согласно лимитам API.

## 3. Пользователь не подключает AI API; маркетплейс всё равно авторизует

- Собственник NoSale из **своей админки** задаёт provider/model (DeepSeek, OpenRouter, etc.), опубликованную роль `nosale_promotion_analyst`, fallback, prompt version, лимиты/бюджеты, мониторинг ошибок, частоту сводок. AI оплачивает сервис; никаких пользовательских BYOK.
- Пользователь NoSale подключает **свой кабинет маркетплейса через официальный OAuth/разрешённую платформой интеграцию**, с минимальными согласованными scopes. Это one-click onboarding, **не ручная настройка API**, но согласие на доступ к данным обязательно. Если конкретная авторизация/метод пока недоступна, честный fallback и явное предупреждение вместо ложного утверждения, что подключение автоматическое.
- Wildberries developer knowledge base прямо описывает **token или OAuth 2.0**; для NoSale необходимо подтвердить доступность сервисного OAuth и права методов до обещания one-click.
- Ozon developer App Store использует OAuth 2.0 и явное согласие продавца. Официальная модерация и минимальные scopes обязательны; некоторые методы Ozon Seller API ещё могут не поддерживать OAuth и потребовать отдельного ключа — показывать отсутствие доступа, а не обходить. Доступ отзывной.

## 4. Полноценный Source Registry: источники и доказательства

Каждый источник включать **только по фактически доказанной доступности**. Registry хранит platform, host/API, method, response schema, required scope/token type, auth mode, rate limit, paid gating, freshness lag, update notes, legal restrictions, permission granted, last_success, last_error, supported_fields.

| Данные | Wildberries: подтверждено по документации | Ozon: подтверждено по документации | Назначение |
|---|---|---|---|
| Карточки, остатки, розничная цена и discount | Content, Prices & Discounts, аналитика остатков | Product, prices, stock/report methods | SKU, варианты, история цены, наличие товара |
| Акции и условия | `GET /api/v1/calendar/promotions`, `/details`, `/nomenclatures` **кроме автоакций** | `/v1/actions`, `/v1/actions/candidates`, `/v1/actions/products` (версии перепроверить) | наличие, цена участия, периоды, правила |
| Фактические начисления, комиссии, скидки | Finance/Reports, `GET /api/v5/supplier/reportDetailByPeriod` и др. | `/v3/finance/transaction/list`, `/v3/finance/transaction/totals`, realization и отчёты | подтверждение seller proceeds, бонусы, возвраты |
| Продажи и конверсия | Analytics/Data, Sales Funnel v3, Reports | `/v1/analytics/data` и отчёты | движение спроса, сравнение периодов, а не фантазия о будущем |
| Продвижение и рекламные расходы | Marketing/Promotion stats | Ozon Ads/Performance — **доступность и scopes подтвердить отдельно** | расходы, эффект на unit economics |
| Логистика/остатки/схемы продаж | Tariffs, Stocks, financial itemization | warehouse/stock, finance | издержки и оценка задержек |
| Статус скидок платформы, WB Клуб, seller промокоды, кешбэк | только там, где это реально подтверждают финансовые поля и seller rules | verified action/financial fields | **не смешивать скидку покупателя с затратами продавца** |
| Документы и изменения правил | seller.wildberries.ru, dev.wildberries.ru release notes | dev.ozon.ru, Seller API docs, app rules | контекст + мониторинг новых методов, не состояние конкретного SKU |

**Отдельные пользовательские источники:** себестоимость, налоги, упаковка, договоры поставщика, прочие внутренние расходы — через ввод/CSV/подключённую учётную систему с авторизацией. Их нет автоматически в WB/Ozon API. Если их нет, NoSale **не вправе** заявлять, что знает чистую прибыль или «убыточность» по себестоимости. Он может обнаружить снижение seller payout или нарушение заданного ограничения.

**Отдельные исследовательские источники:** официальные инструкции/оферты и условия акций, changelog и форумы разработчиков, рассказы продавцов, независимые исследования; подтверждение цифр самого магазина из них запрещено.

## 5. Проверяемая аналитика вместо безусловной уверенности

Pipeline: raw authorized observations → validation → normalized immutable snapshots → deterministic unit-economics → classification `seller_funded/wb_funded/mixed/unknown` с ссылками на источники → AI narrative/advisory → action suggestions → пользователь, без автономных AI write.

**Раздельно отображать:**
1. `data_completeness` и сколько источников реально доступно (доля полей может иметь числовой %).
2. `evidence_status`: «подтверждено данными API», «не удалось проверить», «источники противоречат».
3. `risk_probability`: проценты **только из калиброванной статистической модели**, проверенной на реальных исторических исходах с backtesting + coverage/калибровкой. LLM не имеет права написать «70%», просто потому что выбрал число; до валидации показывать `n/a`/«оценка отсутствует».
4. `economic_effect`: факт прошлых начислений vs прогноз сценария, диапазон и зависимости, точка роста объёма для окупаемости; без себестоимости — нет net margin claim.
5. `actionability`: документированный разрешённый API-action или инструкция сделать руками.

Рекомендации: «оставить», «проверить условия», «установить предел», «пересмотреть ассортимент», «выгода не определена». Не писать «100% безопасно». При неизвестном или устаревшем источнике — `unknown` и, если продавец отдельно согласовал, процентный fallback для **той же** скидки продавца, на которую влияет Standard.

## 6. AI infrastructure, caching, schedule

- Не копировать provider SDK MiniBook. Повторно использовать `@anderhaos/ai-runtime` из `platform-kits` и AI Platform для control plane/roles/secret management. NoSale Django требует поддержанный мост к Node runtime или проверенный сервисный gateway — **сначала spike**, нельзя непосредственно импортировать TypeScript как Python.
- Prompt/prefix caching: неизменные rules/schema первыми, volatile seller observations последними. Semantic caching по MiniBook — **план запроса, а не устаревшие финансовые ответы**. Хранилище observations per-store+sku+promotion+source/revision; invalidate на обновлении цен, правил, выплат, моделей/данных. Before any future write always re-fetch permitted current facts.
- AI вызывается пакетно при важных изменениях и 2–3 сводками на магазин за день; deterministic price monitoring независимо. При падении AI Standard не деградирует, Smart показывает актуальные факты/алерты без сгенерированных выводов.
- Админка владельца: enable/disable Smart AI, providers/models/fallback, prompt versions, budgets, токены/cost, request counts, cache hits, coverage, freshness, moderation of dangerous suggestions, errors and audit.
- Клиентский UI: цена продавца, цена покупателя если доступна, начисления, расходы, дата/поле/API каждого факта; объяснение/гипотеза AI визуально отделена от доказательства.

## 7. Жёсткие ограничения и коммерческая модель

- **WB**: минимальные цены и блокировки защищают только от автоматически применённой скидки автоакции; они не отменяют участие и могут не сработать при специальных ручных способах подключения. Promo Calendar `nomenclatures` не работает с автоакциями. Нельзя обещать полный автоматический охват.
- **Ozon**: с 16.09.2026 `/v1/analytics/data` ограничен до **50 вызовов/сутки без подписки** и одним вызовом в минуту. Не обещать «все отчёты безлимитно»; требуются планировщик квот и paid scope compatibility.
- Ozon 30.04.2026 установил требования к app store (для публичного приложения: юридическое лицо/ИП и домен ≥6 месяцев, сайт с реквизитами, прозрачные тарифы, поддержка, HTTPS, документы) и запрещает неофициальный browser scraping. Мессенджерные боты как отдельные продукты для модерации не принимаются. NoSale — web SaaS с AI-внутри.
- Соглашение Ozon требует ограничивать доступ scope, соблюдать лимиты, удалять данные при отзыве доступа (указано 3 часа) и при длительном неиспользовании (60 дней), с особыми правилами для audit API logs; проверить обязанности с юристом при проектировании retention.
- Прежние **500 ₽/мес. Standard** — гипотеза, не опубликовывать без тарифа/договора. Smart должен учитывать реальные затраты на 2–3 AI сводки, токены, кэш, API, сервер и поддержку; продавец не оплачивает AI отдельно. В тарифе честно указать число магазинов, SKU, частоту, источники и fair-use.
- Отдельная оферта: **аналитическая рекомендация, не гарантия финансового результата**; границы API, момент актуальности, качество данных, действия продавца и ответственность NoSale. Оферта не может подменить техническую защиту.

## 8. План приемки

1. **Source audit** по каждому marketplace, endpoint, праву и платной подписке; проверенный реальный read-only response каждого метода и evidence mapping — до UI-обещаний.
2. Standard regression (без изменения логики или данных текущих продавцов).
3. Smart WB read-only ingestion + Source Registry + правдивый coverage dashboard; персональный доступ и согласие продавца.
4. Финансы, отчёты, привязка seller payouts к заказам/SKU, механизм себестоимости и расходов.
5. Реальный shared AI bridge + админская роль/модели/лимиты, scheduled 2–3 reports + дедупликация событий.
6. Eval: размеченные фактические акции, false-safe costs, калибровка вероятностей на реальных исходах; отображение «недостаточно данных» до этого.
7. Ozon OAuth/scope onboarding, аналогичная Source Registry интеграция, rate limits и retention; затем остальные маркетплейсы по официальным контрактам.
8. Только после shadow и отдельного соглашения — любые **новые** Smart auto-write действия; текущий Standard не изменять.

## Подтверждённые официальные ссылки

- WB API overview: https://dev.wildberries.ru/knowledge-base/articles/019d49a0-ea5d-74b7-a147-be50f8dda5e9/kak-pol-zovat-sia-dokumentatsiei-wb-api
- WB token/OAuth: https://dev.wildberries.ru/knowledge-base/articles/019d49a0-f60a-7b42-bcbb-15b1cfee9023/sposoby-podkliucheniia-k-wb-api-token-i-oauth-2-0
- WB promo calendar: https://dev.wildberries.cn/docs/openapi/promotion
- WB auto promotions constraints: https://seller.wildberries.ru/instructions/ru/ru/material/how-to-set-minimal-price-and-block-auto-promotions
- WB financial fields: https://dev.wildberries.ru/openapi/financial-reports-and-accounting
- Ozon Seller API method roles: https://dev.ozon.ru/start/300-Mapping-rolei-i-metodov-Seller-API
- Ozon analytics limits (16.09.2026): https://dev.ozon.ru/news/797-Izmenenie-limitov-na-metod-v1-analytics-data/
- Ozon App/OAuth FAQ: https://dev.ozon.ru/start/442-FAQ-Magazin-prilozhenii-i-Lichnyi-kabinet-razrabotchika/
- Ozon app requirements (30.04.2026): https://dev.ozon.ru/news/696-Pravila-dostupa-k-integratsii-s-Platformoi-i-razmescheniia-v-Magazine-Prilozhenii/
- Ozon developer agreement: https://dev.ozon.ru/docs/legal/appstore/
- Shared AI runtime: https://github.com/Anderhaos/platform-kits/tree/main/packages/ai-runtime

**Не сделано:** NoSale AI администрация, OAuth integration, реальный marketplace-wide Source Registry, финаналитика, live-методы и calibrated inference — всё это описано как требование, а не внедрённая возможность.
