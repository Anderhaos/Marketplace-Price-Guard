# NoSale — первый коммерческий MVP: Standard + Smart Telegram assistant

> **Актуальный порядок реализации и критерии GO/NO-GO:** [RELEASE_IMPLEMENTATION_PLAN_STANDARD_SMART_2026-10-23.md](./RELEASE_IMPLEMENTATION_PLAN_STANDARD_SMART_2026-10-23.md). План заменяет более раннее предположение о необходимости отдельной `PlanEntitlement` — проверка BeClients подтвердила: текущих `AccessGrant.plan` и `SubscriptionPayment.plan` достаточно. Также план фиксирует упрощённую политику смены тарифа первого релиза и проверяемые релизные этапы. Не считать перечисленные будущие компоненты реализованными.

**Дата:** 2026-10-23. **Статус:** актуальное продуктовое решение/реализационный контракт, **НЕ внедрено**. **Приоритет:** выпускаем **оба тарифа с реальной полезностью**; собственная масштабная аналитика/кохорты/обучение — **этап после первого платного релиза**, а не его блокер. Этот документ имеет приоритет по **scope первого релиза** над большими `DUAL_PROTECTION_MODES_SPEC_2026-10-23.md`, `SMART_MARKETPLACE_ADVISOR_PRODUCT_DECISION_2026-10-23.md` и `FIRST_PARTY_COLLECTIVE_INTELLIGENCE_DECISION_2026-10-23.md`. Последние документы продолжают описывать **дорожную карту Smart v2+**, их научные/правовые ограничения применяются уже сейчас.

## 0. Решение владельца

- **Standard** — ориентир **500 ₽/мес.**, существующая процентная защита скидок WB + **обычный Telegram-бот NoSale** (вход/подключение магазина, уведомления, события, статус/действующие правила; без LLM). Защита Standard не изменяется, нельзя сломать живого legacy продавца.
- **Smart** — ориентир **800–1000 ₽/мес. (рекомендуемый тест цены 990 ₽)**, **всё из Standard** + **ограниченный AI-ассистент DeepSeek** через централизованный серверный доступ, например **1–2 фактологических отчёта/рекомендации в день** и небольшое число ручных вопросов (первоначально **2/день**, adjustable). AI не делает WB write, не обещает 100% точность и не выдаёт искусственно сгенерированные вероятности риска.
- **Ни один клиент** не покупает отдельно AI токены, не создаёт DeepSeek/OpenRouter API keys, не вводит конфигурацию LLM. NoSale платит и управляет провайдерами в **owner-admin**. Клиент только подключает свой магазин WB **по официальным разрешённым методам**.
- **Большая аналитика** (долгий event store, seller finance reconciliation, внешние исследования, когорты, статистические модели, collective intelligence) переносится в этап **Smart v2+**, без перепаковки данных чужих BI-продуктов.
- **Релизная зависимость:** не рекламировать «AI-консультант уже работает», пока реально не работает модель, тарифное разграничение и Telegram привязка. Не задерживать технические исправления Standard, но для публичной продажи **двухтарифного предложения** оба обещанных тарифа должны быть проверены в end-to-end.

Цены **предварительные**, действующий checkout Django имеет **один** `NOSALE_MONTHLY_PRICE_RUB`, оплата/договор/фискализация ещё под коммерческой приёмкой. Нельзя просто разместить две цены, не перепроектировав покупку и доступ: в verified bank webhook должно быть подписанное `plan_id`/amount (через платежный ledger), server-side entitlement и неизменяемый snapshot тарифа на время платежа/возврата; пробные/админские гранты регулируются отдельно. Не допускать апгрейд Smart путём подмены скрытого поля браузера.

## 1. Где переиспользование, а где его пока нет

- Текущий SaaS NoSale (`saas/guard`, `saas/portal`) имеет цены, события, настройки скидок, уведомления и Web Push. **В проверенной SaaS-ветке отдельного Telegram runtime/тарифа Smart пока нет.** Нельзя утверждать, что Telegram-бот NoSale уже доставляет сообщения.
- В `Anderhaos/MiniBook/backend/src/modules/tg-admin-assistant` есть Telegram gateway, private-chat identity, dedup/idempotency, orchestrator, semantic protocol, дневники; `Anderhaos/platform-kits/packages/ai-runtime` — готовые DeepSeek/OpenRouter провайдеры, prompt caching/usage. **Переиспользовать паттерны и общие библиотеки, НЕ переносить CRM/YCLIENTS-зависимую семантику и пользователей MiniBook**.
- AI Platform `apps/control-plane-api/src/ai-gateway/http.ts` предоставляет проектный authenticated `/chat/completions` и usage audit, но подключение NoSale / его service token / published model должны быть **проверены по реальному deployed endpoint**. Проект по ADR может вызывать shared Node runtime напрямую; для Django MVP легальный и чистый вариант — **проверенный внутренний gateway AI Platform по HTTPS с отдельным NoSale service token**, если он действительно доступен, либо изолированный Node bridge к `@anderhaos/ai-runtime`. **Не копировать MiniBook и не создавать новые provider SDK в Python без необходимости.**
- Текущий DeepSeek API 2026 (официально): `deepseek-flash` — дешёвая флагманская для small adviser задач; `deepseek-chat` нельзя предполагать актуальной моделью. `https://api-docs.deepseek.com/quick_start/pricing/`. Конкретный model ID в AI Platform gateway может отличаться: выбирать только реально опубликованный ID.

## 2. Что действительно делает бот за 500 ₽

Единый **официальный Telegram bot NoSale**, созданный/подключённый владельцем. Один бот обслуживает оба тарифа, доступ определяется **server-side** по `TelegramUser ↔ NoSale user ↔ seller store ↔ active SubscriptionPlan/AccessGrant`.

**Привязка:** подтверждённый пользователь в личном кабинете нажимает «Подключить Telegram» → получает одноразовую короткоживущую ссылку `t.me/<bot>?start=<random-token>` → backend проверяет secret/token/TTL/telegram sender и привязывает **конкретный Telegram ID**. Запрещено связывать по username, `chatId` без start-token, коду магазина или e-mail. Запрещена доставка в групповые чаты. Не принимать входящие webhook updates без `X-Telegram-Bot-Api-Secret-Token` с constant time compare; update ID dedup и персональный rate limiting. После отзыва связи отозвать доступ и прекратить сообщения.

**Basic команды** (без LLM):
- `/start` и `/help`: подключение/привязка, команды, состояние доступа;
- `/status`: защита активна/пауза, время последней проверки, состояние подключения WB и ограничений, по зарегистрированным магазинам;
- `/alerts` / `/history`: последние реально записанные предупреждения и события (в пределах своего пользователя), без домыслов;
- пуш уведомлений о событиях по существующему `GuardEvent` через очередь с идемпотентностью: превышение лимита, изменение скидки, WB errors, подтверждение/неподтверждение upload, срок подписки.
Любая write-команда WB в Telegram **запрещена на первом релизе**. Вся фактическая защита продолжает выполняться в existing deterministic Django guard.

**AI-бот** — тот же Telegram sender и те же уведомления, но дополнительные команды/естественный язык и `/advice`; не создавать отдельно второй бот, не требовать отдельную Telegram учётку.

## 3. Smart за 800–1000 ₽: ограниченный, но небесполезный консультант

Схема одного AI отчёта:
1. По расписанию 1–2 раза в день (seller timezone; configurable), не чаще и не всегда если нет новых данных, read-only выгрузить уже собранные `GuardAccount/Products/Events/lastCheck/allowedDiscount/Upload` **данные собственного магазина** из NoSale. Не делать неконтролируемого частого WB polling для генерации текста и не включать платные WB API без нужды.
2. Детерминированный Python backend рассчитывает для SKU фактическое отклонение `current_discount vs allowed_discount`, изменения цены, количество затронутых товаров, статус синхронизации, последние события, confirmed WB actions и warning source. Если дата `last_check` просрочена — `data_stale`, не прикидываться realtime.
3. Сформировать **короткий минимальный snapshot** JSON с value, source `WB API via NoSale`, timestamp, currency и ограничениями. **Никаких WB токенов/PII/credentials в prompt**. Передавать только разрешённые сведения, учесть конфиденциальность платформы и условия provider data processing.
4. AI пишет краткое объяснение и рекомендацию **по известным числам и событиям**, а не «бизнес-аналитику акции» без API о финансировании акции. Для ответа со свободным вопросом использовать строго тот же snapshot, не произвольные внешние факты.
5. Backend проверяет output contract (разрешённые sku/source references/схема, нет новых numeric facts). Любые цифры и риск-статусы показывать из deterministic calculations, а AI использовать преимущественно для текста.
6. Сводки могут быть «Новых отклонений нет» **без вызова LLM**, если факты не изменились. Если синхронизации нет — «Пока недостаточно данных WB для персональных рекомендаций», а не шаблонный «AI совет».
7. Сохранять `recommendation_id, user_id, seller_store_id, observed_snapshot_id, model/prompt_version, created_at, delivered_at, consumer feedback`; реальную пользу измерять метриками clicked/followed-up/false-positive, но не заявлять `profit saved` без финансовых доказательств.

**Первые реальные AI кейсы:**
- 8 товаров превысили заданный предел; что проверить в приоритетном порядке;
- цены 3 SKU изменились за сутки; на что обратить внимание (не «скидка точно убыточна»);
- проверка WB не выполнялась давно; что сделать чтобы восстановить актуальную защиту;
- конкретные ответы «Что изменилось сегодня?», «По каким товарам наибольшие превышения?», «Какие есть предупреждения?» по текущим событиям;
- будущий номер акции, WB-funded financing, маржинальная прибыль и рынок — только после добавления разрешённого подтверждённого official read API, иначе честный отказ делать вывод.

## 4. Owner-only admin (Django /admin, не отдельный SaaS UI для продавца)

**Разделы:**
- Telegram: bot token хранится только в environment/secret store, webhook secret, username (без секретов в браузере), проверка Bot API `getMe` и webhook delivery, health/error count/queue. Создание бота делается через BotFather владельцем.
- AI: выбрать published provider / model (`deepseek-flash` при подтверждённом ID), резерв, prompt/template version, read-only role `nosale_seller_advisor`, enable/disable, allowed context fields, max input/output size, per request timeout, balance/budget, не выдавать секреты продавцу. Не хранить сам провайдерский API key в открытом ModelAdmin поле.
- Тарифы: Standard 500 / Smart 990 как **параметры предложения**, месяц/дни пробного доступа и серверные capabilities, лимиты AI сообщений и сводок; `AccessGrant` + immutable `SubscriptionPayment` payment source of truth, отдельный `PlanEntitlement` при успешной оплате. Все апгрейды/продления/возвраты привязаны к плану.
- AI usage: per user/per store UTC-date or seller local-date ledger `input_tokens/cached_tokens/output_tokens, call count, cost`, hard fail-closed daily/monthly budgets, dedup incoming update and scheduled task, cache. Админ видит агрегированные usage/error counts, не чужие переписки без предусмотренного доступа и оснований.
- Safety: global kill switch (не отключает Standard); disable per tenant; privacy/consent; no AI-write. Внешние источники и Machine Learning из предыдущих документов — будущие дополнительные разделы, **не нужны для MVP**.

**Первоначальные лимиты-предложения:** 2 scheduled advice batches/day/user, 2 manual AI questions/day/user, 300–500 output tokens/answer, ограниченный SKU top-N context и weekly/monthly token cap. Не обещать ежедневные «выгодные акции», если API их не доказал. Квоты должны ограничивать **расходы**, а не заставлять модель выдавать бессодержательный контент.

## 5. Security и юридическое разграничение

- Telegram update — недоверенный ввод, независимо от подписки; отдельная verified identity, per-user grants и per-store access on each command; защита от повторов/гонок;
- Доступ к AI только при `plan=smart AND active AccessGrant AND feature enabled AND quota remaining AND verified seller linkage`; **не просто скрытие кнопки**. При отзыве платежа нельзя продолжать использовать AI до конца месяца.
- Токены WB/Ozon, AI и Telegram хранятся только на сервере отдельно, не в репозитории, логах, клиенте, LLM context.
- Через LLM не проводить запись в WB. Текст Telegram не запускает изменение цены.
- Нельзя пересылать полные сырые данные продавца DeepSeek без договора/разрешения; минимизация данных, цель обработки, политика приватности, opt-in AI.
- Telegram bot не заменяет сайт и подтверждённый платёж, не доверять `username`, `chat ID` без linkage.

## 6. Реальная экономика

DeepSeek официально публикует недорогую модель `deepseek-flash` и цену за 1М входных/выходных токенов, включая кэш и пиковые часы (`https://api-docs.deepseek.com/quick_start/pricing/`). Два компактных отчёта + два вопроса/сутки значительно дешевле бесконечного agent browsing; **рассчитывать по фактическому prompt/output token usage и действующему тарифу API**, расходы Telegram/инфраструктуры/поддержки/платежей тоже учитывать. Цена 990 — гипотеза, не оферта без фискализации и approved acquiring.

Пример не является обещанием: 1 вход 2000 tokens и 1 выход 400 tokens = запрос с ограниченным контекстом; бюджет пользователя = лимиты и отсутствие бессмысленных повторов. Никаких повторных WB-парсингов ради двух сводок.

## 7. Вертикальные проверки перед релизом

**Release A** (обе тарифные возможности в staging в отдельной branch, legacy не менять):
- собственная админка: настройки бота/модели/тарифов; owner-only, masked secrets, выключено по умолчанию;
- простой бот: owner BOT API credential verified, secret webhook, Telegram binding link, basic commands, alerts, idempotent delivery, отказ при чужом chat/user, проверка отзыва доступа;
- Smart: проверенный gateway `NoSale service token` + выбранная опубликованная DeepSeek model, capped tokens, prompt/no-raw-token, 2 scheduled advisories (или skip when no new events), 2 manual queries/day, strict source scope, no WB writes;
- `plan` and bank checkout: plan price locked at checkout, verified signed webhook amount/plan, access grants+entitlements, refund downgrade, expiration, trial rule; не выдавать AI за просто оплату Standard;
- Telegram opt-in delivery, opt-out, GDPR/privacy/terms, 2 тарифные карточки, staging full UX acceptance (регистрация → WB token onboarding → trial → Telegram bot → AI premium via approved grant → expiry) и PostgreSQL/CI tests;
- E5 **separate** test environment; `SAAS_WB_NETWORK_ENABLED=0` in staging default; no real discount write test without explicit permissions; legacy port 8891 untouched.
- ни один тариф не считать готовым только по green CI; smoke with real Telegram and LLM, as well as verified acquiring where legally ready.

**Release B** (после платного MVP): real WB promotions/finance, historical event store, first-party cohorts/advanced analytics, calibrated probabilities, later Ozon and licensed sources. Refer to `FIRST_PARTY_COLLECTIVE_INTELLIGENCE_DECISION_2026-10-23.md`. Default no collective Ozon training without explicit permissible rights.

**Do not claim implemented:** по состоянию на 23.10.2026 owner AI admin, Telegram runtime in NoSale, AI gateway connectivity for NoSale, smart billing and generation have **not** been verified/implemented; legacy and Standard unchanged.
