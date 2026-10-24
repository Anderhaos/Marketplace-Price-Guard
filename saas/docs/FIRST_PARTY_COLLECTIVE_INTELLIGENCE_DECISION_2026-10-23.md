# NoSale — собственная накопительная аналитика и обучение на подтверждённых исходах

> **РЕЛИЗНАЯ ОЧЕРЁДНОСТЬ 2026-10-23:** собственная глобальная база, когорты и ML — **после** [коммерческого MVP с двумя тарифами, обычным Telegram-ботом и небольшим Smart-ассистентом](./COMMERCIAL_MVP_STANDARD_SMART_TELEGRAM_2026-10-23.md). Правовые ограничения использования WB/Ozon данных и недопустимость вымышленных фактов обязательны уже в первом MVP.
**Дата:** 2026-10-23. **Статус:** уточнённая продуктовая постановка / дизайн-решение, **не реализовано**. **Продуктовый приоритет:** этот документ уточняет и заменяет ошибочный акцент на покупке/перепродаже внешней статистики из `ADMIN_EXTERNAL_MARKET_DATA_SOURCE_AUDIT_2026-10-23.md` и задаёт направление Smart в `SMART_MARKETPLACE_ADVISOR_PRODUCT_DECISION_2026-10-23.md`. Сохранить Standard NoSale 1:1. Работать по `saas/AGENTS.md`.

## 0. Что пользователь имеет в виду

**Не:** арендовать MPSTATS/Moneyplace, перепаковать их отчёты и назвать «AI NoSale».

**Да:** строить **собственную историю и доказательный аналитический движок**: собственные наблюдения законно подключённых магазинов, история акций/цены/выплат, сегменты сопоставимых товаров/магазинов, результат рекомендаций и сделанных пользователем действий, обучение на расхождении прогноза и факта. Внешние аналитические источники — только опциональный валидатор/контекст при разрешённом использовании, но не ядро.

AI — инструмент интерпретации и консультирования **над собственной аналитической инфраструктурой**, а не замена расчётов/оценка скидок «из воздуха». Первый режим `Standard` не меняется, второй `Smart` включает рекомендации и обновляемое знание.

## 1. Юридический стоп-фактор по текущему Ozon Developer Agreement

**Проверено 23.10.2026:** Ozon, `https://dev.ozon.ru/docs/legal/appstore/`, раздел 6.1: данные продавцов через API конфиденциальны; разработчику **прямо запрещено использовать их для обучения искусственного интеллекта и моделей машинного обучения**, а также вне приложения, и для иных целей/способов вне согласия продавца. Раздел 5.4 ограничивает использование базы данных, в том числе построение альтернативной программы/БД из этих материалов. Это существенно строже обычной пользовательской галочки.

**Engineering default = DENY**: Ozon API data разрешены только для документированной персональной обработки в рамках приложения и соглаcия; не допускать общую cross-tenant статистическую обучающую выборку/обучение/дообучение ML/LLM/embedding model на Ozon Seller API data. Даже `anonymized`, `federated` и `differentially private` **не являются обходом запрета на training**. Нужна отдельная письменная юридическая оценка и явные права от маркетплейса, а также от продавца, прежде чем допускать эти данные к коллективному обучению. Никакое прошлое согласие продавца не отменяет договор маркетплейса.

**WB:** в общедоступных правилах проверены официальная API интеграция и конфиденциальность `https://seller.wildberries.ru/instructions/ru/ru/material/site-using-terms`, но конкретное разрешение на cross-seller secondary use/ML training **не подтверждено**. Поэтому **DENY by default** до анализа актуального соглашения WB API, партнёрского договора, условий обработки данных и соответствующего отдельного согласия. То же по любому будущему marketplace.

**GDPR/PII:** даже агрегирование/псевдонимизация — не автоматическое исключение из правового режима; small-cell и linkage могут восстановить магазин/человека. Источники:
- Ozon official agreement: https://dev.ozon.ru/docs/legal/appstore/
- WB public portal rules: https://seller.wildberries.ru/instructions/ru/ru/material/site-using-terms
- European Data Protection Board: https://www.edpb.europa.eu/topics/ai-and-technology/anonymisation-pseudonymisation_en
- UK ICO: https://ico.org.uk/for-organisations/uk-gdpr-guidance-and-resources/data-sharing/anonymisation/introduction-to-anonymisation/

**Не путать** персональную аналитику истории своего магазина внутри NoSale (если разрешена его подключением и назначением приложения) с `global_learning`/cross-tenant статистикой и тренировкой ML. Разные scopes, цели, retention, аудит. Нельзя использовать чужие данные для оценки другого продавца при невыданных правах.

## 2. Разрешённая и целевая архитектура

```text
WB/Ozon authorised seller API — seller-specific official facts
          | (provenance + access policy)
          v
TENANT EVENT STORE: immutable SKU x store x timestamp observations
   price, discount, promo, orders, sales, returns, reports, payouts, fees
   manual COGS/ad cost, consent provenance, source/fields/revisions
          |
          +--> Personal baseline / SKU history / seller advice
          |           deterministic calculations, evidence, 2–3 AI updates/day
          |
          +--[secondary-use permission gate]-->
                    LICENSED RESEARCH / AGGREGATION POOL
                    independent cohort snapshots
                    normalisation -> confounding checks
                    statistical tests and validation
                    calibration/effect models
                              |
                       APPROVED MODEL REGISTRY
                              |
                  personal Smart recommendation+
                    fresh seller facts + audit
```

При `not-authorized` стрелка `secondary-use permission gate` **полностью закрыта**, даже если пользователь оплатил Smart. Вместо общих инсайтов — персональная история/формулы и разрешённые лицензированные публичные агрегаты. Никаких скрытых фонов/тренировок во внешней DeepSeek.

## 3. Что собирать в Event Store

Запись должна отражать реальные изменения, а не только текущий snapshot:
- `seller_store_id` (тенант), `marketplace`, `sku/nmID`, категория/бренд/ценовой сегмент, fulfilment (FBW/FBS/FBO...), размеры/варианты, валюта, дата/часовой пояс, метод/source, задержка источника и ревизия;
- seller base and discounted price; `WB/customer discount` отдельным полем **только когда получено API**; financing `platform|seller|mixed|unknown` только по доказательству; promo type, ID, planned price, starts/ends; «нет поля» `null/unknown`, не 0;
- orders, sales, cancellations, buyouts, returns, attribution/ads, stocks, commissions, acquiring, payout, storage/logistics, platform subsidies; actual `finance reconciled` после задержки;
- user-provided `COGS, taxes, packaging, target margin` с пометкой происхождения, явным consent;
- `Recommendation`: snapshot source IDs + calculated metric, analyst/model/prompt/version, proposed action, confidence type, expiry;
- `Action`: recommendation displayed/read, seller chose keep/leave/correct, confirmed WB API write (если Standard) + ack. Отдельно действия продавца в портале **только если официально наблюдаемы**; не додумывать;
- `Outcome`: спустя 1/3/7/14/28 дней наблюдаемые показатели фактических оплат, возвратов и seller income; отмечать censored, data missing, stockout, simultaneous changes, data lag.

**Сохранять неизменяемую историю** с дедупликацией, reconciliation и data lineage, но соблюдать запреты хранения, отзыва доступа, минимизации и удаления. Размер retention определяется контрактами по источнику, а не максимальной пользой для AI.

## 4. Подбор сопоставимых групп (если разрешено)

Не складывать все товары/магазины в одну кучу. Cohort key/version как минимум:
- один marketplace + механика промо (`auto|regular|unknown`) + финансирование;
- категория/товарная группа, цена/маржа, схема поставки, регион, наличие остатков;
- размер, дата сезона, календарная неделя, прежний тренд продаж, рекламное вмешательство;
- предыдущие скидки, длительность участия, источник изменения, трафик и уровень данных.

Сначала сравнивать **один товар с его же историей** в сопоставимые дни и периоды. Затем — другие похожие SKU **в пределах одного магазина**. Затем, **только при законном разрешении**, когорты из разных магазинов. Если выборка маленькая или сопоставимость низкая — честно `insufficient evidence`.

**Когорты не равны причинным выводам.** Продавцы чаще подключают акции в сезон, при повышенном спросе или рекламной активности; простое «продажи выросли после акции» часто ложно приписывает рост скидке. Использовать pre/post with seasonal baselines, matched controls, holdouts where feasible, difference-in-differences с проверкой pretrends, чувствительность к остаткам/ценам/рекламе, доверительные интервалы и метод/ограничения вывода. Не обещать causal proof без подходящего дизайна.

## 5. Как именно система «учится» (4 разных механизма)

1. **Накопление фактов:** пополнение исторического журнала и проверка результата любой рекомендации через фактическую выплату/прибыль. Само по себе это НЕ ML-тренировка.
2. **Проверяемые правила:** матрица официальных механизмов `marketplace/campaign/discount payer/effect`, обновляемая при смене документации и прямого API evidence; правила версионировать и тестировать.
3. **Статистическая модель полезности:** на **только легально разрешённых** исторических метках/исходах, статистическая вероятность или диапазон ожидаемого uplift; `model version, training dataset source rights, eval methodology, holdout window, bias, calibrated confidence`. Если права или данные отсутствуют — отключить коллективное ML, не подменять AI гаданием.
4. **LLM на inference:** DeepSeek/OpenRouter через общий `platform-kits/ai-runtime` получает только разрешённый актуальный summary/evidence, выбирает ясное объяснение и сообщает ограничения. Prompt-cache и semantic-cache **не являются обучением модели**. Не выполнять автоматическое fine-tuning/remote provider training на seller private data.

## 6. Методология качества и рекомендуемые выходы

Два разных направления:
- `Seller loss protection`: false-safe rate (доля финансово опасных скидок, которые система сочла безопасными), RUB-loss at risk, latency, unknown/coverage, actual autoaction ack. Жёсткие запреты/лимиты задаются детерминированным policy, а не LLM confidence.
- `Promotion upside`: projected contribution margin / unit, max affordable seller discount, necessary unit sales uplift (baseline unit contribution / after unit contribution) при положительной после-акционной марже, benchmark uplift / credible interval, confidence calibration, actual/forecast deltas. С отрицательной unit margin рост продаж обычно увеличивает убытки — не рекомендовать `просто продавать больше`.

`risk_score` от неподтверждённого LLM — **НЕ** probability. Если через год проверено, что из 100 сопоставимых эпизодов с прогнозом около 70% риск наступил в ~70, можно обозначить калиброванную вероятность с sample size/time window. До этого «оценка не рассчитана».

Пример **только для будущей UI формы, не реальные данные**:
```
Площадка: WB | Товар: SKU 123
Режим: Smart — рекомендация, не исполнение
Источники: WB API (последний snapshot), финансовые отчёты (с лагом),
себестоимость продавца (дата), разрешённые cohort stats (если есть)
Текущая цена продавца: подтверждено
Часть скидки за счёт WB: неизвестно — отсутствует подтверждающий метод
Прогноз изменения прибыли: недостаточно данных
Исторически сопоставимых случаев: N (только если права и фактический N подтверждены)
Действие: проверить участие, не считать безопасной автоматически
```

## 7. Owner Admin — собственная система обучения NoSale

Не «переключатели провайдера рыночных данных» в центре, а:
- **Источники и права**: какие official APIs подключены, срок разрешений, allowed processing purposes; permission to use for `personal-analysis` vs `global-aggregation` vs `model-training`; правовая версия документа; **no default global opt-in**.
- **Историческая база**: число авторизованных магазинов, SKU-days, кампаний, подтверждённых financial-outcomes и покрытие по platform/category; технические состояния.
- **Когорты/сегменты**: правила similarity matching, минимальное число независимых магазинов/наблюдений, suppression small cells, exclude rare/unique SKUs; разграничение показателей по сегментам, time windows, bias alerts.
- **Гипотезы/эксперименты**: предложено и подтверждено, baseline, matched controls, confounders, outcome accuracy, data quality; challenger модели, временное тестирование, rollback.
- **Model Registry**: model version, legal dataset manifest, consent/contract provenance, evaluation metrics, training disabled by default, deployment/shadow status; клиенты не управляют этим.
- **Decision Ledger**: какие рекомендации NoSale давал и на основании каких API facts, какой подтверждённый итог, сколько ошибок, рублёвая сумма потенциального риска, оценка экономического эффекта и задержка отчёта.
- **AI провайдеры**: DeepSeek и т. д. по опубликованной отдельной роли, стоимость, токены, cache hits, 2–3 аналитические сводки в день per tenant; текущая защита Standard не зависит от AI.

Невозможно показывать `коллективная статистика 200 похожих продавцов`, если их данные не лицензированы для коллективного использования или сегмент раскрывает информацию о конкретном продавце. Качество/количество реальных подключений не выдавать за «точность модели».

## 8. MVP по шагам без правовых и технических фикций

**P0 — сейчас:** правовой `source-permission matrix` для WB/Ozon, `deny training` for Ozon, `deny unverified secondary` for WB, не применять к чужим данным. Документировать retention, data minimization and user consent; согласовать с юристом и площадками.

**P1:** собственный **tenant-private** Event Store/read-only наблюдения и история акций, цены, поступления, результаты текущей Standard-защиты; без вмешательства в current guard.

**P2:** персональная аналитика времени и товара: до/после, сравнение похожих SKU в одном магазине, сезонная база; понятные рекомендации/AI 2–3 раза в день; проверяемый финансовый итог и статус missing-data.

**P3:** first-party feedback и decision ledger, автоматическая сверка рекомендаций с последующими фактическими результатами; quality metrics, ручное разбор ошибочных случаев в owner-admin.

**P4 (условно разрешённый):** после полученных договорных прав, seller consent и legal signoff — кросс-магазинная агрегированная аналитика и статистическая ML model (по разрешённым данным и purpose), backtest/calibration; не перенимать Ozon seller API training data вопреки запрету.

**P5:** внешние лицензированные публичные исследования/рынок лишь как дополнительные ковариаты/валидация, если добавляют точность. MPSTATS и т. п. — **не центральная ценность NoSale**.

**Acceptance:** приложение не запускает коллективное обучение без flags `per_source.allow_model_training=true` и `per_seller.secondary_use_granted=true` + verified marketplace-rights; поздний rollback/revocation удаляет доступ из будущих наборов и выполняет условия удаления; сквозные тесты не позволяют случайно включить кросс-тенантную загрузку. Все рекомендации имеют `facts vs estimates` и source lineage; AI не обладает WB/Ozon write tool.
