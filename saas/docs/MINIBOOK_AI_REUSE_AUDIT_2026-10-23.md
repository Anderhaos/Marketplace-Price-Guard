# NoSale Smart: аудит AI-слоя Telegram Admin MiniBook и повторное использование

**Дата:** 2026-10-23.
**Аудит исходников:** `Anderhaos/MiniBook` main `de481e5c685ff7b42223d0f6b2a052e429087d0c`; `Anderhaos/platform-kits` main `cfb2fb5a2a43ae77c4b5c60bcf723b88ed8912a7`.
**Цель:** сохранить Standard NoSale без изменений; Smart — отдельный режим в Django. Документ — результат аудита, не реализация, не проверка живых моделей на проде.

## 1. Важный архитектурный вывод

Изначальная гипотеза «создать с нуля отдельный DeepSeek/OpenRouter провайдер для NoSale» **отвергнута**. Уже есть общий `@anderhaos/ai-runtime` в `platform-kits`, Telegram orchestration на `@anderhaos/assistant-runtime` и подключение продуктов к AI Platform. Не копировать backend MiniBook целиком и не создавать независимые дубли провайдеров, кэширования, управления моделью/ключами.

- **AI Platform** = контроль политики, конфигураций, контрактов, model/provider selection и связей продуктов (control plane; verify exact current enrollment API before integration).
- **`@anderhaos/ai-runtime`** = повторно используемая Node реализация inference: OpenAI, DeepSeek, Anthropic, OpenRouter, Yandex; устойчивый порядок промпта и provider-specific prompt caching. По README пакета вызов модели прямой из продукта, **AI Platform не обязан быть в inference path**. В MiniBook есть также опциональный platform-managed gateway; не считать его единственным путём.
- **Telegram assistant** = зрелый образец orchestration, deterministic tool boundary, safety and semantic caching; CRM/YCLIENTS domain tools не переносить в NoSale.
- **NoSale** = Django/Python. Нельзя напрямую импортировать TS `@anderhaos/ai-runtime` в Python. Выбрать по отдельному ADR: тонкий доверенный Node microservice/kit gateway со строго типизированным API либо существующий совместимый AI Platform inference gateway/SDK, если проверен production ready. Не создавать второй «NoSale DeepSeek client» только ради удобства.
- **NoSale Smart policy** = исключительно Django домен: WB facts, seller economics, classification, policy decisions and guarded WB write. LLM analysis/advisory не является источником истинности.

## 2. Проверенные исходники MiniBook

| Путь | Подтверждённая функция | Вывод для NoSale |
|---|---|---|
| `backend/src/modules/ai-runtime/ai-provider-registry.ts` | Реестр Yandex/DeepSeek/OpenAI/OpenRouter/Anthropic | Не клонировать каталог/SDK |
| `backend/src/modules/ai-runtime/ai-role-config-resolver.ts` | Роль разрешается из **published** конфигурации, иначе `ai_role_unconfigured` | NoSale создать отдельную published роль `nosale_promotion_analyst`, не наследовать настройки Telegram |
| `backend/src/modules/ai-runtime/ai-native-tool-gateway.ts` | Native-tool протокол, строгие schemas; telemetry `cachedReadTokens/cacheWriteTokens`, latency и fallback | Переиспользовать общую обработку/наблюдаемость, не поставлять credentials клиенту |
| `backend/src/modules/ai-runtime/platform-managed-text-gateway.ts` | Опциональная платформа с model policy, таймаутами, retries и fallback | Выбрать одну поддерживаемую инфраструктуру, не создавать скрытую вторую |
| `backend/src/modules/tg-admin-assistant/model/production-semantic-protocol.ts` | Semantic calls canonicalized **структурно**, строгая проверка бизнес-интерпретации выполняется далее на сервере | JSON schema — необходима, но не достаточна для решения о прибыли |
| `backend/src/modules/tg-admin-assistant/runtime/semantic-fast-path.ts` | В памяти LRU-ish cache, max 256; promotion после одинаковой **read-only semantic interpretation** в двух разных контекстах | Не кэшировать финансовый verdict аналогично текстовой интерпретации |
| `backend/src/modules/tg-admin-assistant/runtime/semantic-pattern-memory.ts` | Нормализация фразы/shape, contract version, SHA-256 ключи; Reject cachedReply/businessFacts/mutableAnswer, только безопасная read-only интерпретация | Кэшировать план проверки (какие WB поля посмотреть), но не «эту акцию можно пропустить» |
| `backend/src/modules/tg-admin-assistant/storage/semantic-pattern-store.ts` | Persistent Prisma store: principalId, contractVersion, candidate→active после согласованных повторов; exact/shape reuse только если signatures совпадают | Tenant isolation, evidence promotion, conflict invalidation. Реализация в NoSale — Postgres/Django или отдельный общий контракт |
| `backend/src/modules/tg-admin-assistant/runtime/resolve-semantic-fast-path.ts` | In-process hit→persistent exact→persistent shape→LLM; кэш failure падает обратно к модели; logs `modelCalls=0` | Раздельный учёт hit/miss и причин miss |
| `backend/src/modules/tg-admin-assistant/runtime/write-operation-guard.ts` | Durable idempotent operation claim + confirm path, не повторять сомнительные внешние записи | Критично для WB 429/async upload и смены правил |
| `backend/src/modules/tg-admin-assistant/runtime/model-route-dispatch.ts` | One-shot planner / bounded agent loop / procedural skills; ограничение итераций | Для NoSale преимущественно one-shot analyses и без автономного исполнения |
| `backend/src/modules/tg-admin-assistant/runtime/semantic-fast-path.test.ts` | Тесты: две одинаковые интерпретации прежде reuse; смена версии инвалидирует; **кэшированный intent запускает свежий CRM read** | Аналогично всегда заново читать/проверять WB facts перед любым write |
| `backend/src/modules/tg-admin-assistant/runtime/semantic-pattern.integration.test.ts` | Тесты persistent paraphrase hits без доп. LLM вызова; факты загружаются заново | Рекомендации проверять на новых фактах |
| `DOC/agent-docs/decisions/ADR-0020-tg-admin-ai-role-provisioning.md` | SSOT — опубликованная роль, не UI default/draft; отсутствие роли явная ошибка | Не использовать молчаливую модель по умолчанию |
| `DOC/agent-docs/decisions/ADR-0022-cdlit-remote-ai-runner-provider.md` | HTTPS Remote Runner через managed credential store; при ошибке parse/schema fail closed, fallback для транспортных ошибок | NoSale transport fallback ≠ permission to write |

Все ссылки в исходных репозиториях (canonical):
- https://github.com/Anderhaos/MiniBook/tree/main/backend/src/modules/tg-admin-assistant/
- https://github.com/Anderhaos/MiniBook/tree/main/backend/src/modules/ai-runtime/
- https://github.com/Anderhaos/platform-kits/tree/main/packages/ai-runtime/
- https://github.com/Anderhaos/platform-kits/blob/main/packages/ai-runtime/README.md

## 3. В NoSale два РАЗНЫХ вида кэширования

**A. Prompt/prefix cache (провайдер):** передавать общие инструкции, schema и условия сначала, параметры кампании и последние динамические данные в конце. Из `@anderhaos/ai-runtime` уже есть провайдерные optimizations: OpenAI `prompt_cache_key`, Anthropic cache breakpoints, OpenRouter session ID и DeepSeek automatic prefix reuse. Эти механизмы ускоряют обработку и снижают стоимость токенов, но не гарантируют правильность ответа.

**B. Application semantic cache (MiniBook):** хранит не «погоду 20 °C», а «пользователь запросил прогноз на завтра»; выполняет новый вызов живого источника. В NoSale аналог — «проверить источник платформенной скидки и поле seller payout», а не «скидка безопасна». Такой план/алгоритм пригоден к повторному использованию при иных товарах и датах после проверки контракта.

**C. NoSale evidence / campaign snapshot cache:** отдельный, финансово-критичный кэш, которого готовым из MiniBook нет. Ключ включает `tenantId+storeId+nmID+promotionId+promotionRevision+sellerPriceRevision+costRevision+protectionRulesVersion+WBApiObservationTime+schemaVersion`. Использовать для read/display и дешёвой precheck аналитики, но перед автоматической записью — проверить свежесть фактов и версию настроек, WB способ воздействия, idempotency. Любой `unknown/stale/ambiguous` не переводить в safe из-за AI-кэша. Кэш WB финансовой детализации не подменяет бухгалтерский источник.

## 4. Зависимость от AI, режимы и границы

- `Standard` = существующая `saas/guard/monitor.py` политика, полностью без AI; регрессионные тесты перед любым общим refactor.
- `Smart` = новая политика с `monitor_only`, `preserve_confirmed`, `risk_confirmed`, `uncertain`. Финансовое решение принимает deterministic rules engine, AI объясняет, разъясняет условия, помогает классифицировать *недоказанные* механики и формирует запрос на недостающие факты.
- Никакого `WBClient.submit_discounts` из LLM tool/agent. Только отдельная доменная policy с подтверждёнными правами, активной конфигурацией и verification upload status.
- Ошибки AI / quota / missing role / fallback: Smart продолжает детерминированный мониторинг и ранее разрешённую процентную защиту; не объявлять неизвестные акции безопасными.
- AI role `nosale_promotion_analyst` независима от MiniBook/CDLIT; roles, credentials, prompts — управляются через проверенный control plane/credential store. Не копировать секреты чужих продуктов.

## 5. Реальный статус и следующий технический шаг

- Аудит исходников MiniBook и platform-kits **выполнен** на указанных Git SHA; соответствующий plan must replace new custom provider idea в NoSale spec.
- **Не проверено** live-подключение конкретного нового NoSale проекта к AI Platform (enrollment/credentials) и совместимость Django↔Node (bridge). Требует отдельного spike/proof. Не обещать, что прямое импортирование TypeScript в Django работает.
- Тесты MiniBook/AI runtime по исходникам есть, но **не запускались** в этом аудите; статистику процента кэш-хитов или качество на фактических акциях NoSale нельзя заявлять без данных.
- Сначала утверждённый способ вызова shared runtime из Django, затем Smart read-only + snapshots, затем строгие eval и shadow mode, только после этого explicit writes.

**Не смешивать** кэширование намерений Telegram с кэшированием денежного решения и не переносить CRM-политику/роли из MiniBook.
