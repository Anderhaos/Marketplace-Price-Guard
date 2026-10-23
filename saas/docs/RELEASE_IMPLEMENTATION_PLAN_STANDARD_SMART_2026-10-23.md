# NoSale — план реализации и приёмки первого коммерческого релиза

**Дата:** 2026-10-23. **Статус:** исполнимый план, не акт готовности продукта.
**Приоритет:** два тарифа, реально работающая защита WB, один Telegram-бот, ограниченный AI-консультант по собственному магазину, управляемый владельцем. Коллективное обучение и глубокая аналитика — **после запуска**.
**Связанные постановки:** [COMMERCIAL_MVP_STANDARD_SMART_TELEGRAM_2026-10-23.md](./COMMERCIAL_MVP_STANDARD_SMART_TELEGRAM_2026-10-23.md), [FIRST_PARTY_COLLECTIVE_INTELLIGENCE_DECISION_2026-10-23.md](./FIRST_PARTY_COLLECTIVE_INTELLIGENCE_DECISION_2026-10-23.md). При расхождении scope MVP — этот план.
**Базовые инструкции:** `saas/AGENTS.md`: изменения исходного кода **только через GitHub Connector**, feature branches и Draft PR, GitHub Actions, immutable deploy. `mpnosale.svdev.pro` (legacy, реальный продавец, 8891) **не изменять**.

## A. Что реально готово на момент аудита

- **NoSale SaaS:** Django/PostgreSQL, multi-store, WB guard, notifications/Web Push, PIN/email, 5-дневный триал, Tochka acquiring adapter с проверкой webhook, AccessGrant и reconciliation. SaaS staging `nosale.svdev.pro` на E5 port 8892, `SAAS_WB_NETWORK_ENABLED=0`; real commercial payment / real WB tests not approved.
- **PR #3** `feature/nosale-welcome-email-first`: UI/регистрация; **PR #4** `feature/nosale-dual-protection-modes`: продуктовая спецификация; **PR #5** `feature/nosale-store-advisor-mvp`: read-only adviser, `SubscriptionPayment.plan`, `AccessGrant.plan`, effective-plan and BeClients-style capability matrix; **PR #5 head `46bdcc51f58f1a0360e23e8bdd55a60f6fb42fd3`: CI `django-tests` ×2 GREEN** (Django migrations/tests/Docker smoke). PR #5 остаётся Draft.
- **Известный блокер:** `saas/billing/service.py` по-прежнему создаёт платежи с **одной ценой** и при подтверждении не передаёт `payment.plan` в выдаваемый грант; текущий Smart нельзя купить. Нынешний `/advisor/` — только детерминированные советы и не Telegram/LLM.
- **BeClients источник правды:** `Anderhaos/MiniBook` main `de481e5c` (совпадает с E5 checkout). `backend/src/modules/billing/billing.service.ts`, `billing-capabilities.ts`, `beclients-ai/beclients-ai-tariff.ts`. Использовать **pattern**: Tariff price/plan, effective AccessGrant, paid checkout snapshot, capability guard и idempotent bank confirmation; **не копировать TypeScript, CRM, второй payment engine**. В BeClients продажа PRO сейчас явно disabled (не заимствовать это ограничение).

## B. Неподвижный договор MVP

**Standard** (ориентир 500 ₽/мес): действующая WB price/discount guard + один Telegram бот: `/status`, `/alerts`, `/history`, персональные push/Telegram notifications.
**Smart** (ориентир 990 ₽/мес): весь Standard + 1–2 короткие meaningful рекомендации/сутки, до 2 ручных вопросов/сутки через управляемый владельцем DeepSeek/AI Platform. Если нет новых фактов — без пустого LLM вызова. Это **гипотезы цен**, не опубликованная оферта.
**Границы:** все WB операции записи исключительно существующим deterministic guard при явно разрешённой конфигурации; AI/read-only Telegram не изменяет товары/скидки, не обещает фактический финансовый эффект, не обучается на данных Ozon/WB без допустимых прав. Нельзя показывать необоснованные проценты вероятности или финансовую прибыль.
**Учётный объём:** AccessGrant относится к пользователю и его зарегистрированным магазинам как сейчас; лимиты AI считаются **на пользователя** (не отдельно на каждый магазин), чтобы исключить мультипликацию стоимости. Единица тарификации/лимиты магазинов перед публикацией оферты должны быть согласованы с фактической экономикой.

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

### R2. Один Telegram-бот для обоих тарифов (параллелен R1)

**Переиспользовать из MiniBook:** Telegram ingress identity, dedup patterns, protection of private chat; **не импортировать CRM code/команды**. Реализация компактным Django модулем в SaaS плюс существующие notification workers.

1. Owner создаёт/подключает **одного** бота через BotFather; bot token + webhook secret только server secret storage; `getMe` health в админке; webhook `X-Telegram-Bot-Api-Secret-Token` и POST body validation.
2. Верифицируемый одноразовый `/start <TTL link-token>` после входа в кабинет, приватный Telegram user ID ↔ NoSale user, unlink/relink, отзыв, запрет group chat, cross-tenant, дублированных updates. Для нескольких магазинов можно выбрать свой store при условии владельца; никакой авторизации по username.
3. Команды `/help`, `/status`, `/alerts`, `/history` только из существующих WB snapshots / GuardEvent, без нового постоянного WB polling; `/alerts` не меняет скидки. Подписка неактивна → понятное сообщение и путь в кабинет.
4. Уведомления из уже существующего события/очереди: идемпотентная отправка, ретраи с backoff, уважение Telegram 429, opt-out, no token leakage, метрики доставок; если бот недоступен, guard continue independently.

**Done R2:** real Telegram bot smoke на тестовом аккаунте и unit tests spoofed sender/invalid secret/replay/cross-store/disconnect/429; Standard работает без AI.

### R3. Минимальная owner-admin для подключения и расходов (параллелен R2)

**Не делать новую самостоятельную панель управления.** Встроить только необходимые экраны в Django `/admin/` (superuser/owner-only, secret masked):
- `Telegram integration`: enabled, verified bot username/webhook health, failure counters, broker status, не показывать token.
- `AI`: project role `nosale_seller_advisor`, published gateway model ID, enabled/off, readonly prompt version, max input/output, timeout, daily/monthly token and RUB budget, 1–2 scheduled briefs + 2 manual questions/day; **provider key живёт в общей AI Platform**, NoSale хранит только свой отдельный scoped service-token как серверный secret.
- `Tariff offers`: видимые цены и доступность, месячный период, server-side capability matrix; не редактировать инвойсы задним числом.
- `Operational status`: payment/Telegram/AI errors, quotas, counters, emergency disable AI **без выключения Standard**. Secrets and seller PII not logged.

**Done R3:** non-owner не может открыть/настроить AI или раскрыть secrets; owner может выключить AI из админки; повторное включение не сбрасывает израсходованные квоты.

### R4. Smart AI-консультант, но только по доказуемым данным (зависит R1 + R2 + R3)

1. Завершить `guard/advisor.py`: stale fraction по каждому SKU (а не только max `checked_at`), топ проблемных товаров, последние настоящие price/discount events, pending vs confirmed WB uploads. Короткий доказательный per-user/store snapshot (`sku_id`, metric, source, observed_at, data_age) c исключением raw seller tokens, credentials и лишней коммерческой информации.
2. Подключить доступный published DeepSeek model через существующий **AI Platform Gateway** (`/chat/completions`), отдельный project service token, лимиты затрат и таймаут; если шлюз/роль недоступны — fail closed и **показать детерминированные рекомендации**, не молча вызывать публичный сторонний API. Не копировать full Telegram AI агент/semantic training engine MiniBook.
3. AI отвечает кратко на `/advice` и 2 ручных вопроса/день: «Что изменилось?», «Что проверить первым?», «Какие товары превышают предел?». Программа вычисляет числа, источники и даты; LLM составляет объяснение. Валидировать схему output: только известные metrics/SKUs, no hallucinated payout/competition/seller-funded claims.
4. **Сводки** max 2/day/user, по часовому поясу пользователя, только при новых значимых фактах; не делать 2 бессмысленных генерации каждый день. Idempotency `(user_id, snapshot_digest, advice_kind, reporting_window)`, лимиты атомарно, ledgers of model/usage/cost, no WB calls triggered by AI.
5. Персональные store prompts передавать внешнему AI только при подходящем правовом основании/согласии, без личных WB/Ozon токенов, с указанными purpose и retention. No train/collective learning by default.

**Done R4:** реальные 1–2 успешных отчёта с grounded refs, запуск 2 разрешённых вопросов, 3-й отклонён до вызова модели, webhook replay не расходует токены дважды; prompt injection не даёт получить чужой store и не вызывает WB writes; устаревшие WB данные отмечены устаревшими.

### R5. Единый UX + интеграционная приёмка (зависит R1–R4)

1. Главный кабинет: Telegram connect/unlink, тариф/expiry, понятный gated Smart upgrade; не показывать ссылку Smart всем с последующим plain 403. Не превращать read-only advice в «подтверждённый финансовый прогноз». UI mobile responsiveness.
2. Сквозная user journey: email→code→PIN→WB onboarding→5-day Standard trial→Standard/Smart payment→verified entitlement→link Telegram→bot basic→Smart advice→quota→expiry/refund→re-enable после оплаты.
3. Автоматические CI tests SQLite + **обязательные PostgreSQL migration/integration** на изолированном staging, Docker, backend isolation, actual Telegram Bot API test chat + owner-approved AI Gateway smoke + signed bank sandbox webhook, failure/rollback tests, WB 429/read/write gates.
4. Проверить коммерческие предпосылки: регистрация/договорные права WB для SaaS, секреты/роли Точки, настоящий merchant/54-ФЗ чеки/оферта/политика данных, лимиты себестоимости AI/Telegram и фактическая цена предложения. Никакой реальной скидочной записи без отдельной разрешённой проверки.
5. Деплой в отдельный `nosale.svdev.pro` **по SHA** с backup+rollback. Health, workflow, monitoring, owner counters, no regressions legacy. Сначала внутренний тест и закрытая beta; только после приёмки — публичные платежи и объявление Smart.

**Done R5 / Release GO:** пользователь без помощи разработчика может пройти путь, Standard защищает товары, Telegram уведомляет, Smart действительно отвечает по своему магазину, банк покупает правильный тариф, квоты/рефанды работают, бюджет не превышается, legacy не изменён. Любой сломанный пункт — NO GO для соответствующего тарифа.

## D. Структура работ и параллелизм

- **Трек Billing**: R1 — можно выполнять одновременно с R2; только код `saas/billing/*` и billing UI/migrations, отдельный PR.
- **Трек Telegram**: R2 — новый isolated adapter с owner settings/wiring, отдельный PR; не менять existing WB monitor без явного event hook и regression.
- **Трек Advisor+AI**: качественные factual snapshots R4-start параллельно, но реальный AI dispatch только после readiness R3 + capability checks R1.
- **Интеграционный трек**: R3 + R5, единый commit-integrated staging release только после независимых green CI и конкретных code-review мержей. PR #1/#3/#4/#5 — **stacked**; запрещено незаметно мержить их прямо в `master`.

### Блокирующие зависимости

```
R0 baseline+CI
  ├── R1 billing+effective entitlements ──┐
  ├── R2 Telegram basic+verified binding ─┤
  └── R3 owner settings+AI gateway probe ─┤
                ↓                         │
        R4 evidence+AI capped adviser <──┘
                ↓
          R5 E2E + isolated E5 staging
                ↓
       closed beta → accepted paid launch
```

## E. Что сознательно НЕ входит в MVP

- MPSTATS/Moneyplace/другие vendor BI; никакой reseller analytics.
- Global seller data pool, machine learning/fine-tuning/LLM training, Ozon and other marketplaces; отдельный legal/workflow в Smart v2+.
- Автоматическое изменение цены по тексту AI/Telegram, произвольный агент с открытыми tool calls; новый parser/scraper WB.
- Отдельная панель «AI Platform» внутри NoSale, второй SDK для провайдеров/копия MiniBook AI runtime, второй Telegram-бот.
- Автоматические recurring charges, годовые/квартальные тарифы, рекламная кампания AI «гарантированно предотвращает убытки».

## F. Как организовано управление задачами

- Этот файл — **единственная исполнимая очередь задач первого релиза**. Концептуальные документы — справочные, не параллельные планы разработки.
- Каждая R1–R5 задача оформляется как PR с доказательством `code + tests + CI + acceptance notes`. Никаких «готово» только за факт коммита или health=200.
- До завершения R5 никакого deploy/merge, затрагивающего живой legacy; SaaS staging изменения отдельно через одобренный E5 workflow.
- **Следующая задача:** R1, закончить тарифный checkout/AccessGrant webhook and tests; параллельно начать R2 Telegram identity/webhook. Никаких новых обсуждений большой аналитики до закрытия этих задач.
