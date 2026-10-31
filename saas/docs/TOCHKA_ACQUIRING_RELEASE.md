# NoSale / Tochka — подключение эквайринга с фискализацией

Интеграция основана на способе работы BeClients (Anderhaos/MiniBook):
серверный запрос к Tochka API, подтверждение по подписанному RS256 JWT,
периодические сверки платежей. Реквизиты и токены MiniBook НЕ переносятся.

## Что реализовано

- Разовая помесячная покупка Standard или Smart, без автосписаний.
- Цены из включённых OwnerTariffOffer в PostgreSQL; проверка на сервере.
- Платёжная ссылка только с чеком: POST /uapi/acquiring/v1.0/payments_with_receipt.
- В чеке: email покупателя, услуга NoSale и её цена, режим налогообложения,
  НДС и способ расчёта; никаких предположений о налогах.
- Событие acquiringInternetPayment — подписанный JWT RS256, text/plain.
- Проверка merchantId, customerCode, суммы, операции и номера заказа;
  идемпотентное начисление AccessGrant и отзыв при полном возврате.
- Браузерный возврат не подтверждает оплату. При пропущенном webhook
  статус запрашивается сервером через Get Payment Operation Info.
- Частичные возвраты остаются ручной проверкой без автоматического изменения срока.

## Действия владельца в интернет-банке

1. Сервисы → Интернет-эквайринг: проверить, что договор подключён для
   NoSale и торговая точка активна. Если merchant общий с BeClients,
   уточнить у Точки, можно ли использовать его для второго сайта.
2. Подключить фискального партнёра: «Чеки» либо онлайн-кассу. Проверить
   формирование чеков продажи И возврата. С бухгалтером подтвердить:
   систему налогообложения, ставку НДС, способ расчёта по подписке.
3. Сервисы → Интеграции и API → Создать JWT-ключ. Имя «NoSale».
   Требуются разрешения MakeAcquiringOperation, ReadAcquiringData,
   ReadCustomerData и ManageWebhookData для создания/изменения webhook.
   Не пересылать JWT по чату и не добавлять его в GitHub.
4. Получить customerCode (Get Customers List, тип Business), merchantId
   (Get Retailers), client_id и публичный ключ проверки подписи Точки.
5. Настроить получение acquiringInternetPayment по HTTPS:
   https://nosale.svdev.pro/billing/tochka/webhook/
   Точка отправляет тестовый POST при регистрации и ждёт HTTP 200;
   даже при выключенном эквайринге NoSale отвечает 200 без изменения
   платежей и прав. После включения активной обработки события
   проверяются подписанным JWT RS256.
6. В защищённом окружении E5 задать, НЕ публикуя секреты:
   NOSALE_TOCHKA_JWT_TOKEN, NOSALE_TOCHKA_CUSTOMER_CODE,
   NOSALE_TOCHKA_MERCHANT_ID, NOSALE_TOCHKA_WEBHOOK_JWK_JSON,
   NOSALE_TOCHKA_TAX_SYSTEM_CODE (osn/usn_income/usn_income_outcome/esn/patent),
   NOSALE_TOCHKA_VAT_TYPE (none/vat0/vat5/vat7/vat10/vat22 и др.),
   NOSALE_TOCHKA_RECEIPT_PAYMENT_METHOD (full_payment/full_prepayment).
   После подтверждения кассы: NOSALE_FISCALIZATION_CONFIRMED=1.
   До окончания проверки: NOSALE_TOCHKA_ENABLED=0.
7. В тестовой/контролируемой среде проверить ОДНУ фактическую оплату,
   email с чеком, однократное начисление доступа, повтор webhook,
   возврат (с чеком возврата) и отзыв доступа. Только после этого
   отдельным решением открыть продажи и включить тарифы.

## Важные ограничения

- Не переносить токены, merchantId или customerCode BeClients.
- Подтверждённое подключение фискального сервиса — обязательная предпосылка,
  одна булева настройка без подключения кассы не подтверждает выдачу чека.
- Изменение токена JWT в Точке требует обновить client_id вебхука.
- Не включать реальные платежи на основании только unit/E2E тестов.
- Вебхук доступен публично, но не доверяет неподписанным событиям.

Официальные документы:
https://developers.tochka.com/docs/tochka-api/internet-acquiring-integration
https://developers.tochka.com/docs/tochka-api/api/create-payment-operation-with-receipt-acquiring-v-1-0-payments-with-receipt-post
https://developers.tochka.com/docs/tochka-api/api/get-payment-operation-info-acquiring-v-1-0-payments-operation-id-get
https://developers.tochka.com/docs/tochka-api/opisanie-metodov/vebhuki
https://developers.tochka.com/docs/tochka-api/algoritm-raboty-s-jwt-tokenom
