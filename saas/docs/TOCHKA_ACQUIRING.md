# NoSale — API Точки (основная документация)

**Основной источник истины — официальная документация Точка.API.**

Этот файл сохранён только для совместимости ссылок из старых задач.
Актуальный, проверенный на 09.11.2026, сценарий подключения, правильный
production-домен и последовательность действий приведены в
[TOCHKA_ACQUIRING_RELEASE.md](TOCHKA_ACQUIRING_RELEASE.md).

Важно:
- NoSale использует `POST /acquiring/v1.0/payments_with_receipt`, то есть
  **платёжную ссылку с фискализацией**, не метод без чека.
- Адрес webhook: `https://nosale.pro/billing/tochka/webhook/`.
- Сервер NoSale: `226082.msk.web.highserver.ru`; старый E5 не использовать.
- Нужен **собственный** JWT NoSale и его client_id; никогда не заменять
  webhook другого проекта.
- Работающий код Olvee/BeClients — референс для сравнения, не спецификация
  банка и не источник настроек NoSale.
- Пока отсутствуют утверждённые банковские идентификаторы и проверенная
  фискализация, checkout должен оставаться выключенным.

Официальные источники:
https://developers.tochka.com/docs/tochka-api/
https://developers.tochka.com/docs/tochka-api/internet-acquiring-integration
https://developers.tochka.com/docs/tochka-api/opisanie-metodov/vebhuki
