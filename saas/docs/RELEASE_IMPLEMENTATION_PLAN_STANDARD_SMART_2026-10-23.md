# NoSale — план реализации и приёмки первого коммерческого релиза

**Дата:** 2026-10-23. **Статус:** исполнимый план, не акт готовности продукта.
**Приоритет:** два тарифа, работающая защита WB, **встроенный в кабинет NoSale помощник**, расширенный Smart AI-консультант с рекомендациями по своему магазину. Берём переносимые элементы существующего ассистента из Telegram, **не создаём для клиентов Telegram-бота**. Коллективное обучение и глубокая аналитика — **после запуска**.
**Связанные постановки:** [COMMERCIAL_MVP_STANDARD_SMART_TELEGRAM_2026-10-23.md](./COMMERCIAL_MVP_STANDARD_SMART_TELEGRAM_2026-10-23.md), [FIRST_PARTY_COLLECTIVE_INTELLIGENCE_DECISION_2026-10-23.md](./FIRST_PARTY_COLLECTIVE_INTELLIGENCE_DECISION_2026-10-23.md). При расхождении scope MVP — этот план.
**Базовые инструкции:** `saas/AGENTS.md`: изменения исходного кода **только через GitHub Connector**, feature branches и Draft PR, GitHub Actions, immutable deploy. `mpnosale.svdev.pro` (legacy, реальный продавец, 8891) **не изменять**.

## A. Что реально готово на момент аудита

- **NoSale SaaS:** Django/PostgreSQL, multi-store, WB guard, notifications/Web Push, PIN/email, 5-дневный триал, Tochka acquiring adapter с проверкой webhook, AccessGrant и reconciliation. SaaS staging `nosale.svdev.pro` на E5 port 8892, `SAAS_WB_NETWORK_ENABLED=0`; real commercial payment / real WB tests not approved.
- **PR #3** `feature/nosale-welcome-email-first`: UI/регистрация; **PR #4** `feature/nosale-dual-protection-modes`: продуктовая спецификация; **PR #5** `feature/nosale-store-advisor-mvp`: read-only adviser, `SubscriptionPayment.plan`, `AccessGrant.plan`, effective-plan and BeClients-style capability matrix; **PR #5 head `46bdcc51f58f1a0360e23e8bdd55a60f6fb42fd3`: CI `django-tests` ×2 GREEN** (Django migrations/tests/Docker smoke). PR #5 остаётся Draft.
- **Известный блокер:** `saas/billing/service.py` по-прежнему создаёт платежи с **одной ценой** и при подтверждении не передаёт `payment.plan` в выдаваемый грант; текущий Smart нельзя купить. Нынешний `/advisor/` — только детерминированные советы и не Telegram/LLM.
- **BeClients источник правды:** `Anderhaos/MiniBook` main `de481e5c` (совпадает с E5 checkout). `backend/src/modules/billing/billing.service.ts`, `billing-capabilities.ts`, `beclients-ai/beclients-ai-tariff.ts`. Использовать **pattern**: Tariff price/plan, effective AccessGrant, paid checkout snapshot, capability guard и idempotent bank confirmation; **не копировать TypeScript, CRM, второй payment engine**. В BeClients продажа PRO сейчас явно disabled (не заимствовать это ограничение).

## B. Неподвижный договор MVP

**Уточнение владельца от 23.10.2026:** «взять ассистента из Telegram и воткнуть его в базовую версию» означает **переиспользовать подходящую логику/ядро существующего ассистента MiniBook/BeClients, но встроить его в веб-интерфейс NoSale**. Это **не** задача создания Telegram-бота или интеграции пользователей с Telegram. Предыдущие предложения с BotFather, webhook, `chat_id` и Telegram notifications отменены в scope MVP. Телеграм может рассматриваться как отдельный необязательный канал в будущем, не как release dependency.

**Standard** (ориентир **500 ₽/мес.**): действующая защита скидок WB, события и предупреждения, встроенный **базовый помощник в личном кабинете** (помощь по функциям NoSale, статус защиты/проверок и навигация по данным своего магазина; ограниченные ответы через проверенную общую AI инфраструктуру там, где нужны). Не выдавать базовому помощнику функции аналитики Smart. Цену считать предварительной до оферты.

**Smart** (ориентир **990 ₽/мес.**): Standard + **расширенные возможности того же помощника** — персональные советы по фактам WB конкретного магазина, 1–2 содержательных сводки/сутки и небольшое число ручных вопросов (первоначальная гипотеза — 2/день); DeepSeek через централизованный AI Platform Gateway, квоты и бюджет. Без бесконечного опроса WB и вымышленных доказательств прибыльности акций.

**Границы:** существующий deterministic guard и его WB write gates не трогать; AI-помощник read-only, работает только по данным авторизованного аккаунта, не знает коммерческие факты, отсутствующие в WB API; no collective training без прав. Базовый веб-чат и Smart работают в одном UI, а разграничение действует на backend через `AccessGrant.plan` / `PLAN_CAPABILITIES`, не через скрытие кнопок.

**Учётный объём:** `AccessGrant` user-scoped, разрешённые магазины пользователя. Квоты AI считать на пользователя, а не на магазин. Стоимость provider calls Standard и Smart измерять отдельно; Standard может отвечать на типовые вопросы без LLM.


## C. Очерёдность реализации (вертикальные рабочие пакеты)

### R0. Свести базу и исключить конфликт веток — блокер интеграции

1. Проверить последовательно `PR #1 → PR #3 → PR #4 → PR #5`: на каком SHA основывается следующая ветка и какие миграции уже в ней. Не создавать дублей SaaS, не менять legacy, не передвигать `master` без отдельной release acceptance.
2. Зафиксировать существующий E5 deployed tree и GitHub tree; отдельная SaaS staging deployment только проверенного immutable SHA. Зафиксировать rollback и backup PostgreSQL.
3. Добавить регрессионные тесты ранее работающего Standard (worker, guard write gates, WB 429, pending uploads, PIN/mail, email, tenant isolation), чтобы следующие изменения их не повредили.

**Done R0:** проверенная чистая база + успешный CI; задокументированный commit SHA для staging, без изменений legacy.

### R1. Два тарифа: end-to-end payment → plan → capabilities (critical path)

**Код:** `saas/billing/models.py`, `access.py`, `service.py`, `views.py`, `tochka.py`, `saas/portal/templates/billing/subscription.html`; новые миграции только если действительно нужны.

1. Использовать текущие `AccessGrant.plan`, `SubscriptionPayment.plan`, `effective_plan()` и `PLAN_CAPABILITIES`. **Не создавать `PlanEntitlement` или отдельную таблицу `Subscription`, если имеющихся ledger-объектов достаточно.** Достаточна одна небольшая админская конфигурация тарифных предложений `slug/price RUB/month/enabled` (либо строгая конфигурация deployment на MVP); capability matrix задаётся в коде и проверяется сервером, не в UI.
2. `GET /billing/` показывает доступные `Standard/Smart`, текущий эффективный тариф, старт/окончание доступа и характеристики; `POST checkout` принимает только `plan_slug`, **цену вычисляет сервер**. В `SubscriptionPayment` зафиксировать immutable `plan + amount + period`; pending link reuse только при совпадающем плане, периоде и сумме. Любое изменение approved цены не меняет уже созданный payment.
3. Verified Tochka webhook сверяет merchant/customer, operation/link, сумму и подпись JWT, создаёт **`AccessGrant.plan=payment.plan`**, не стандартный default; REFUNDED revokes ровно связанный grant, pending/duplicate/refund-before-approval проходят тесты.
4. **Явная политика смены тарифа для MVP без сложного proration:** выбранный новый оплаченный тариф начинает действовать **по окончании уже оплаченного/пробного периода**. Это намеренно отложенный upgrade; UI ДО оплаты честно показывает дату активации, после оплаты — «Smart начнёт действовать DD.MM». До этой даты Smart не разрешён. Если мгновенный upgrade обязателен коммерчески — реализовать его **отдельной проверенной задачей** с расчётом доплаты, а не считать `previous.endsAt` мгновенным переключением. Отмена автооплаты: оплата разовая, no recurring charge.
5. Существующий 5-дневный trial остаётся **Standard**, никаких неявных бесплатных AI calls; staff/admin grants — явный отдельный source. Установить активный plan/capability по серверному `AccessGrant`; при overlap Smart приоритетнее Standard только если оба периода реально активны.

**Done R1:** тесты Standard purchase, Smart purchase, insufficient payment, tampered `plan/amount`, pending reuse mismatch, duplicated webhook, refund, active future grant, expired grant, trial → paid, overlapping plans; банк не включён в production до отдельной коммерческой приёмки / 54-ФЗ.

### R2. Встроенный базовый веб-помощник Standard (параллелен R1)

**Код:** компактный Django web assistant / conversations, текущие SaaS auth и шаблоны; существующий `platform-kits/ai-runtime` + `ai-platform` для inference, где нужно. **Референс:** `Anderhaos/MiniBook/backend/src/modules/tg-admin-assistant` — переносить только независимые подсистемы и методы, **не CRM, не Telegram transport**.

1. Провести reuse-аудит реального ассистента MiniBook: handler, conversation/context, prompt/inference gateway, safety/rate limiting; определить, что можно адаптировать к Django SaaS без Node микросервиса.
2. Встроить помощь/чат прямо в интерфейс NoSale с лёгкой историей текущего диалога. После авторизации получает scoped context выбранного магазина.
3. Базовые запросы: как настроить защиту, что означает ошибка, когда магазин проверялся последний раз, какие последние события и превышения. Фактологические данные — только из БД пользователя и источников, а не сгенерированные.
4. Содержательные статусы и FAQ реализовать без LLM, где возможно; если используем модель для обычного чата — короткий ограниченный контекст, базовая квота и отдельная стоимость на пользователя. Никогда не обещать бесплатно неограниченную модель.
5. Обязательные тесты: session/CSRF, чужой магазин, закрытие подписки, output escaping, rate-limits, модель недоступна, мобильная верстка; никакой записи в WB из ответа AI.

**Done R2:** пользователь Standard общается с базовым помощником **веб-кабинета** без регистрации в Telegram; его данные не попадают другим продавцам. Smart использует тот же веб-интерфейс.

### R3. Минимальная owner-admin конфигурация AI/цен/квот (параллелен R2)

**Не создавать новую AI-платформу или Telegram-админку.** Достаточна текущая Django `/admin/` c owner-only проверкой.

- AI Platform: NoSale project role, реально опубликованная и доступная модель, read-only system prompt, optional fallback, таймаут, max input/output, enable/disable. Сервисный ключ только в secret store.
- Тарифы: approved `Standard` и `Smart`, цена за месяц, включение/выключение, server-side capabilities; snapshot уже купленной цены immutable. Не создавать второй ledger.
- Usage: счётчик дневных и месячных AI вызовов и фактических расходов, отдельные бюджеты Standard и Smart; кнопка kill-switch AI не выключает WB защиту.
- Логи: только события, статусы, превышения квоты и без секретов, WB токенов, чужого контекста и платежных деталей.
- Tests: non-owner denied, settings don't leak keys, quota/disable fail closed.

**Done R3:** администратор управляет ценами, моделью и расходами; клиенты подключают только свой WB магазин.

### R4. Smart — персональные рекомендации и AI-чат в вебе (после R1+R2+R3)

1. Развивать `guard/advisor.py`: свежесть **каждого** SKU (а не только последний `checked_at`), топ превышений, изменения, pending/confirmed WB операций. Рассчитывать реальные метрики backend, не LLM.
2. Строить короткий контекст `user_id/store_id/sku/observed_at/source` исключительно из своих данных, без токенов, personal secrets и чужих продавцов. Финансирование акций и прибыль = `unknown` без официального WB факта.
3. Подключить AI Platform authenticated Gateway/DeepSeek, использовать действующий published model; output shape проверять и отделять derived summaries от verified facts. Fail-closed при недоступности AI и показывать deterministic fallback.
4. Встроить Smart-команды в **тот же веб-помощник**: «Что изменилось за день?», «Что проверить сначала?», «Почему риск по товару?», «Получить рекомендации». Ни одного Telegram webhook/start/чат ID.
5. Планировщик 1–2 сводок/день только при изменениях; хранение идемпотентности `(user, digest, window)`, атомарные квоты 2 manual questions/day как первоначальный конфиг. Не вызывать WB чтение при каждом LLM вопросе и не включать AI write.
6. Tests: tenant isolation, stale snapshot, negative/no-data, quota/replay, model timeout/invalid output, no WB write; owner/admin cap.

**Done R4:** Smart-пользователь получает контекстный ответ и проверяемый совет внутри NoSale, а не стандартные уведомления под вывеской AI.

### R5. Общий UX, интеграционная приёмка и релиз (после R1–R4)

1. Понятная единая страница подписок и текущих возможностей; Standard не видит недоступные Smart действия как тупиковую ошибку HTTP 403. Кнопка обновления тарифа, временные периоды и обещанные функции показаны корректно.
2. Сквозная пользовательская цепочка: email→код→PIN→WB onboarding→5-day Standard trial→выбор Standard/Smart→verified bank grant→встроенный базовый чат→Smart рекомендации→quota→expiry/refund→re-enable. **Telegram не требуется.**
3. CI: unit/integration SQLite + PostgreSQL, Django migrations, browser web smoke/кроссбраузер, AI Platform real gateway smoke, bank sandbox signed webhook, WB access control/read-only and legacy regression.
4. Правовые и коммерческие блокеры: WB SaaS API право, merchant Точки и 54-ФЗ, оферта, privacy/consent для внешнего AI, утверждённые цены, cost caps, rollback.
5. Deploy отдельно на `nosale.svdev.pro` immutable SHA, staging backup+rollback; legacy `mpnosale.svdev.pro` не трогать. Проверить реальные страницы и сценарии, закрытая бета → публичный платный релиз только после evidence.

**Done R5 / Release GO:** пользователь без Telegram может зарегистрироваться, подключить WB, оплатить конкретный тариф, использовать Standard guard/базовый веб-чат и Smart AI по своему магазину. `/health 200` и зелёный unit CI сами по себе не означают готовность.


## D. Структура работ и параллелизм

- **R1 Billing**: существующий `saas/billing/*`, отдельная ветка `feature/nosale-r1-two-plan-billing` и PR. Покупка/возвраты/сроки должны быть независимы от UI ассистента.
- **R2 Web assistant**: reuse-аудит существующего TG-ассистента, перенос **логики** в Django web кабинет; Telegram transport не переносить. Параллельно R1.
- **R3 Owner-admin**: AI model + provider routing в общей AI Platform, budget/quotas + approved 2 offers; параллельно R2, без нового слоя администрирования.
- **R4 Contextual Smart**: расширение существующего adviser, подключение к тому же чату; использовать effective capabilities из R1.
- **R5 Integration**: после R1–R4 единый staging SHA, E2E и rollback; stacked PR #1/#3/#4/#5 reviewed, мерж только по приёмке.

### Блокирующие зависимости

```text
R0 sync/test baseline
  ├─ R1 two-plan billing + grants ────┐
  ├─ R2 embedded web helper ──────────┤
  └─ R3 owner admin + AI gateway ─────┤
                                   ↓
                       R4 store-aware Smart
                                   ↓
                    R5 web E2E + isolated E5
                                   ↓
                     closed beta → paid launch
```

## E. Что НЕ входит в MVP

- Telegram-бот как отдельный продукт, BotFather, Telegram linking/идентификация, Telegram-webhook/доставка. Существующий Telegram-ассистент является источником reuse, **не каналом общения клиента**.
- Сложная аналитика, cross-seller memory, pooled ML/fine-tuning, Ozon, внешние BI поставщики, необоснованные оценки прибыльности/расходов.
- Автоматическая WB запись из ассистента, произвольные tools/агенты, отдельный Node AI bridge, если существующий AI Platform HTTP Gateway доступен.
- Ещё одна схема/ledger подписок, второй provider registry, много периодов/методов оплаты и автосписание, если это не нужно MVP.


## F. Как организовано управление задачами

- Этот файл — **единственная исполнимая очередь задач первого релиза**. Концептуальные документы — справочные, не параллельные планы разработки.
- Каждая R1–R5 задача оформляется как PR с доказательством `code + tests + CI + acceptance notes`. Никаких «готово» только за факт коммита или health=200.
- До завершения R5 никакого deploy/merge, затрагивающего живой legacy; SaaS staging изменения отдельно через одобренный E5 workflow.
- **Следующая задача:** R1, закончить тарифный checkout/AccessGrant webhook and tests; параллельно начать R2 Telegram identity/webhook. Никаких новых обсуждений большой аналитики до закрытия этих задач.
