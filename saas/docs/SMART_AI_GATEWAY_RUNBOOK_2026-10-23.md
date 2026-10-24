# NoSale Smart — AI Platform Gateway integration runbook

**Дата:** 2026-10-23. **Branch / Draft PR:** `feature/nosale-r4-store-evidence-gateway` / #16. **Статус:** программный AI-шлюз и тесты реализованы; **живой AI-моделью и реальными продавцами НЕ подтверждено**, фискализация и коммерческий выпуск не готовы.

## Границы

- Веб-помощник **внутри NoSale**. Не Telegram bot.
- Standard: базовые детерминированные ответы по данным конкретного WB-магазина без LLM вызовов.
- Smart: пользователь выбирает «Спросить Smart AI» и явно соглашается на обработку текста вопроса + ограниченного snapshot своего магазина через общую AI Platform и подключённого провайдера.
- AI **не получает WB токены, платёжные реквизиты, исходные персональные данные из профиля, товары других продавцов, raw WB API response**. Snapshot содержит лишь агрегаты, ID отдельных проблемных SKU, источники и время наблюдений.
- WB API write и read ни при каких обстоятельствах не вызываются из AI/веб-помощника. Existing guard alone owns writes.

## Подключение в изолированном staging (без действующего продавца)

1. **В AI Platform** создать/проверить проект NoSale (отдельный от MiniBook), выдать scoped project service token только для chat completion + разрешённой модели. Модель должна быть реально опубликована и доступна в `GET /v1/models` для этого проекта; ID скопировать без преобразования. Настроить **реальный monthly USD project cost cap/rate limits в самой AI Platform**, сверить провайдера и фактическую биллинговую тарифную сетку.
2. На E5 в отдельной **секретной конфигурации SaaS** задать:
   - `NOSALE_AI_GATEWAY_URL=https://<trusted-subdomain>.svdev.pro/v1/chat/completions`, только trusted HTTPS host под `.svdev.pro`; **не** произвольные хосты, localhost, http, query или редиректы.
   - `NOSALE_AI_SERVICE_TOKEN=<NoSale-only-scoped-token>` — не в Git, Django Admin, JS или prompt.
   - `NOSALE_AI_RESERVE_KOPEKS_PER_CALL=100` — **консервативный резерв 1 ₽ на разрешённый вызов**, пример настройки, не provider-invoice и не гарантия реальной стоимости.
3. Открыть текущую Django `/admin/` в отдельном SaaS NoSale как superuser → «Настройки AI владельца»: одна запись (singleton). Настроить `gateway_model_id`, лимиты `smart_questions_per_day` (по умолчанию 2), `max_output_tokens` (до 500 для Smart), `timeout_seconds`, `monthly_budget_kopeks` (>0), `ai_enabled=true` после проверок. Владелец может выключить AI без остановки WB Guard.
4. Выдать тестовому пользователю активный **Smart AccessGrant**; убедиться, что существует синхронизированный тестовый магазин WB **без реального seller data**. В NoSale открыть `/assistant/`, поставить согласие и отправить вопрос через «Спросить Smart AI».
5. Проверить, что backend записал `AssistantAIRequest` со статусом `success`, model ID и usage tokens, но без prompt/API-ключей. Убедиться, что третий вопрос/24ч **не идёт в AI**; Standard не может послать `mode=smart`; неверный URL/отсутствие токена/модель отозвана приводит к **безопасному базовому ответу**, не к вызову WB.
6. Проверить actual provider billing usage в AI Platform и согласовать локальный `NOSALE_AI_RESERVE_KOPEKS_PER_CALL` с консервативным верхним пределом себестоимости. Локальные `monthly_budget_kopeks` — **ограничение на резервируемые обращения**, фактическая стоимость из USD записей AI Platform пока **не синхронизируется в RUB**; поэтому независимый gateway monthly USD cap обязателен.
7. До публичного Smart релиза: текст согласия, политика передачи/удаления данных, сроки хранения истории веб-чата и реальные model response/false-fact испытания; работа на PostgreSQL и отдельный merchant/54-ФЗ.

## Проверки в PR

- JWT-подобный WB токен пользователя в чате не сохраняется и не отправляется провайдеру; ключи NoSale не появляются в ответах модели.
- Единый серверный endpoint и Bearer service token; никаких browser-supplied model/provider/token/URL.
- Gateway client: не следует редиректам, отключает proxies, принимает только HTTPS `*.svdev.pro/v1/chat/completions`; ограничивает размер запроса, ответ и таймаут; умеет читать `usage` OpenAI-совместимого API.
- `OwnerAISettings` superuser-only; модель и бюджет по умолчанию выключены.
- Квоты резервируются транзакцией с блокировкой пользователя и singleton конфигурации; не умножаются от количества магазинов. Ошибочные обращения учитываются в quota, чтобы повторные запросы не спамили провайдера.
- Ответы модели без подтверждённых чисел отклоняются. При недоступности AI сайт выдаёт только детерминированный baseline, не 500 и не ложные финансовые рекомендации.

## Что ещё не реализовано

- Регистрация проекта NoSale в реальной AI Platform и issued service token; работа с реальным DeepSeek модельным ID, действующим провайдером и его ценой; никаких ключей и денег у агента нет.
- Плановые **1–2 сводки в день**, dedup и доставка: требуется отдельный bounded background job после ручного Smart smoke. Нельзя подменять их нынешним generic Push daily digest.
- Конвертация фактических расходов AI Platform USD → RUB для бюджета NoSale; сейчас контролируем запросы/токены и локальный reserve ceiling, actual cost отдельно на стороне AI Platform.
- Полная обработка data privacy/consent и соответствие официальным условиям WB/провайдера.
- Полный onboarding и реальный Tochka merchant + 54-ФЗ; нет деплоя на legacy.

См. [канонический план](./RELEASE_IMPLEMENTATION_PLAN_STANDARD_SMART_2026-10-23.md), тикеты #6, #9, #10, #11 и Draft PR #13–#16.
