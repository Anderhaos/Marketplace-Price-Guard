# NoSale — эквайринг по официальной документации Точка.API

Источник истины: документация Точки, версия API v1.103.1-stable (проверено 09.11.2026).
Olvee и BeClients — только примеры; не переносить их JWT, merchantId,
customerCode, webhook, цены или налоговую конфигурацию.

Production: https://nosale.pro, сервер 226082.msk.web.highserver.ru,
репозиторий Anderhaos/Marketplace-Price-Guard, ветка main.
Старые инструкции для E5 и nosale.svdev.pro НЕ применимы.

## Сценарий

- NoSale продаёт доступ к Standard или Smart на один месяц без автосписания.
- Использовать Create Payment Operation With Receipt:
  POST https://enter.tochka.com/uapi/acquiring/v1.0/payments_with_receipt
  (а не Create Subscription With Receipt: это отдельные рекуррентные списания).
- Сервер считает цену по включённым OwnerTariffOffer; браузер не задаёт сумму.
- Client.email покупателя обязателен для кассового чека.
- Одна строка Items: услуга NoSale, paymentObject=service, quantity=1,
  measure=шт., amount в рублях, подтверждённые taxSystemCode/vatType/paymentMethod.
- paymentMode: card и sbp, preAuthorization=false, saveCard=false.
- Ответ содержит operationId и paymentLink. Сам браузерный redirect не выдаёт
  платный AccessGrant. Доступ выдаётся только после проверенного APPROVED
  с совпадающими merchant/customer, operationId, paymentLinkId и суммой.
- Подписанный webhook: acquiringInternetPayment, JWT RS256 в text/plain.
  Публичный URL: https://nosale.pro/billing/tochka/webhook/
- Пропущенные webhook восстанавливаются серверным запросом Get Payment Operation Info
  через reconciliation-контейнер. Возвраты и чеки возвратов проверять отдельно.

## Факты 09.11.2026 (проверены в production)

- JWT, client_id, Business customerCode, merchantId именно для nosale.pro
  установлены в защищённой среде VPS и проверены через официальное API.
- Точка сообщила REG/isActive=true, карта+СБП, сервис «Чеки»; webhook
  acquiringInternetPayment зарегистрирован и совпадает с публичным JWK банка.
- Фискальные параметры подтверждены владельцем: УСН «Доходы», без НДС,
  full_payment, paymentObject=service. JWT никогда не хранить в Git.
- Реальный платёж 1 ₽ через СБП: APPROVED, кассовый чек пришёл по email,
  AccessGrant создан один раз. Ручной возврат через банк: REFUNDED,
  фоновая сверка отозвала этот AccessGrant.
- Недавние оплаченные операции сверяются с банком через 10 минут.
- Приём платежей по API включён, но ОБА публичных тарифа в PostgreSQL
  остаются выключенными. Нельзя открывать продажи до отдельной приёмки.
- Встроена owner-only команда полного возврата в админке, с двумя шагами,
  журналом, защитой от повторного POST и проверкой результата через банк.
  Новый код не означает, что ещё один реальный возврат уже проверен.

## Последовательность подключения

1. В Точке подтвердить фискализацию: предмет «Услуги», способ расчёта,
   актуальный режим УСН/НДС, чек продажи и возврата.
2. Создать отдельный JWT NoSale в Интеграции и API, сохранить client_id.
   Разрешения: ReadCustomerData, ReadAcquiringData, MakeAcquiringOperation,
   ManageWebhookData. JWT не отправлять в чат или GitHub.
3. Получить customerCode через Get Customers List, где customerType=Business.
   Получить merchantId через Get Retailers. Для точки NoSale необходимо
   status=REG, isActive=true и url=nosale.pro.
4. Секреты только в приватном /opt/nosale/source/saas/.env на новом VPS:
   NOSALE_TOCHKA_JWT_TOKEN — отдельный JWT;
   NOSALE_TOCHKA_CLIENT_ID — отдельный client_id;
   NOSALE_TOCHKA_CUSTOMER_CODE — проверенный код клиента;
   NOSALE_TOCHKA_MERCHANT_ID — проверенный 15-значный ID торговой точки;
   NOSALE_TOCHKA_WEBHOOK_JWK_JSON — публичный RSA JWK с официального
     https://enter.tochka.com/doc/openapi/static/keys/public (TLS-проверка);
   NOSALE_TOCHKA_TAX_SYSTEM_CODE — подтверждённый налоговый режим;
   NOSALE_TOCHKA_VAT_TYPE — подтверждённая ставка НДС;
   NOSALE_TOCHKA_RECEIPT_PAYMENT_METHOD — full_payment или full_prepayment;
   NOSALE_PAYMENT_RETURN_BASE=https://nosale.pro.
   Оставить NOSALE_TOCHKA_ENABLED=0 до приёмки.
5. Проверить без операций списания:
   docker compose exec web python manage.py tochka_preflight --online
   Команда сверяет merchantId, регистрацию REG/active, nosale.pro,
   карту/СБП, официальный публичный JWK и webhook. Секреты не печатаются.
6. Если webhook ещё отсутствует, зарегистрировать:
   docker compose exec web python manage.py tochka_preflight --online --register-webhook
   Используется PUT /webhook/v1.0/{client_id} с webhooksList =
   acquiringInternetPayment и url = https://nosale.pro/billing/tochka/webhook/.
   Команда НЕ перезаписывает чужой URL или набор событий.
7. Только после подтверждения кассы включить флаги NOSALE_FISCALIZATION_CONFIRMED
   и NOSALE_TOCHKA_ENABLED. Включить тестируемый тариф отдельно.
   Провести реальную контролируемую оплату с выдачей чека, однократным
   AccessGrant, повторным webhook, возвратом и сверкой. Повторить для СБП.
   Не открывать продажи всем до успешной приёмки.

В Точке на один client_id допускается ОДИН URL webhook; при перевыпуске JWT
меняется client_id. Не использовать для NoSale ключ другого проекта.
Регистрация webhook может проводиться при выключенном эквайринге:
проверочный POST получит 200 и не создаст подписку.

## Полный возврат через админку NoSale

- В панели владельца: «Платежи и возвраты Точки» →
  платёж в статусе paid → «Вернуть N ₽» → отдельный экран подтверждения.
- Доступ только у active superuser после обычного подтверждения доступа
  в панель владельца. GET не вызывает банк, POST защищён CSRF.
- Сервер сам берёт точную исходную сумму в копейках из SubscriptionPayment,
  конвертирует в рубли, независимо сверяет operationId/merchantId/customerCode,
  paymentLinkId/amount/status через GET банка. Подменить сумму нельзя.
- Одна неизменяемая запись PaymentRefundAttempt на платёж создаётся
  ДО POST /acquiring/v1.0/payments/{operationId}/refund.
  Нет idempotency key у провайдера — при ошибке/таймауте повторные POST
  запрещены. Статус «Требуется проверка банка» не повод нажимать повторно.
- Успешный POST Data.isRefund=true + совпадение amount/operationId/orderId
  подтверждает ПРИНЯТИЕ запроса. Возврат и отзыв доступа подтверждаются
  только банковским GET со статусом REFUNDED. Переходы ON-REFUND и
  временный APPROVED с Order[type=refund] не означают завершение.
- Все попытки видны в «Журнале запросов возврата Точки».
  Никаких массовых/автоматически инициируемых возвратов нет.
  Возврат принимается решением владельца, без фонового повторного POST.
- Частичные возвраты через интерфейс запрещены; увиденный банковский
  REFUNDED_PARTIALLY помечается для ручного разбора, без фиктивного
  подтверждения полной суммы или отзыва всего доступа.

## Официальные ссылки

- https://developers.tochka.com/docs/tochka-api/internet-acquiring-integration
- https://developers.tochka.com/docs/tochka-api/algoritm-raboty-s-jwt-tokenom
- https://developers.tochka.com/docs/tochka-api/opisanie-metodov/platyozhnye-ssylki
- https://developers.tochka.com/docs/tochka-api/api/create-payment-operation-with-receipt-acquiring-v-1-0-payments-with-receipt-post
- https://developers.tochka.com/docs/tochka-api/api/get-retailers-acquiring-v-1-0-retailers-get
- https://developers.tochka.com/docs/tochka-api/opisanie-metodov/vebhuki
- https://developers.tochka.com/docs/tochka-api/api/create-webhook-webhook-v-1-0-client-id-put
- https://developers.tochka.com/docs/tochka-api/faq
- https://developers.tochka.com/docs/tochka-api/certificate
