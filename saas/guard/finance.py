"""Strict WB Finance v1 read-only transport and minimal report-list normalizer.

Official method POST /api/finance/v1/sales-reports/list is a READ operation.
Never use the discount-token credential here, never request buyer-level details,
and never infer marketplace-funded campaigns, margin or net profit from sums.
"""
import json
import re
from datetime import date
from decimal import Decimal, InvalidOperation
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from django.conf import settings
from .wb import get_token_permissions


BASE_URL = "https://finance-api.wildberries.ru"
REPORTS_PATH = "/api/finance/v1/sales-reports/list"
MAX_REPORTS = 50
MAX_RESPONSE_BYTES = 262144


class FinanceUnavailable(Exception):
    pass


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


def require_finance_service_token(value):
    """JWT claims are a local prefilter; WB must validate the actual token."""
    value = (value or "").strip()
    if len(value) > 4096 or len(value) < 32 or any(ch.isspace() for ch in value):
        raise FinanceUnavailable("Нужен отдельный сервисный токен WB категории «Финансы».")
    rights = get_token_permissions(value)
    if (not rights or rights["token_type"] != "service"
            or "Финансы" not in rights["categories"]):
        raise FinanceUnavailable("Требуется сервисный токен WB с категорией «Финансы».")
    return value


def _date_bound(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise FinanceUnavailable("Некорректная дата отчёта.")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise FinanceUnavailable("Некорректная дата отчёта.") from exc


def _money(value):
    # Values arrive as decimal strings. Invalid numbers are unknown, NEVER 0.
    if isinstance(value, bool) or value is None or not isinstance(value, (str, int)):
        return None
    try:
        amount = Decimal(str(value))
        if not amount.is_finite() or amount.copy_abs() > Decimal("100000000000"):
            return None
        return format(amount.quantize(Decimal("0.01")), "f")
    except (InvalidOperation, ValueError):
        return None


def normalize_reports(raw):
    if not isinstance(raw, list) or len(raw) > MAX_REPORTS:
        raise FinanceUnavailable("Неверный формат списка финансовых отчётов.")
    summaries = []
    seen = set()
    for item in raw:
        if not isinstance(item, dict):
            raise FinanceUnavailable("Некорректная финансовая запись WB.")
        report_id = item.get("reportId")
        if type(report_id) is not int or not 0 < report_id < 2**63 or report_id in seen:
            raise FinanceUnavailable("Некорректный или повторный ID финансового отчёта.")
        seen.add(report_id)
        start = _date_bound(item.get("dateFrom"))
        end = _date_bound(item.get("dateTo"))
        if end < start:
            raise FinanceUnavailable("Некорректный период финансового отчёта.")
        currency = item.get("currency")
        if not isinstance(currency, str) or not re.fullmatch(r"[A-Z]{3}", currency):
            raise FinanceUnavailable("Неизвестная валюта финансового отчёта.")
        summaries.append({
            "report_id": report_id,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "currency": currency,
            "for_pay": _money(item.get("forPaySum")),
            "retail_amount": _money(item.get("retailAmountSum")),
            "source": "WB Finance realization reports (summary)",
            "funding": "unknown",
            "profit_verified": False,
        })
    return summaries


def fetch_reports_list(service_token, date_from, date_to):
    if not settings.SAAS_WB_NETWORK_ENABLED or not settings.NOSALE_WB_FINANCE_ENABLED:
        raise FinanceUnavailable("WB Finance API отключён оператором.")
    service_token = require_finance_service_token(service_token)
    start, end = _date_bound(date_from), _date_bound(date_to)
    if end < start or (end - start).days > 31:
        raise FinanceUnavailable("Допустим период не более 31 дня.")
    body = json.dumps({
        "dateFrom": start.isoformat(), "dateTo": end.isoformat(),
        "limit": MAX_REPORTS, "offset": 0, "period": "weekly",
    }, separators=(",", ":")).encode("utf-8")
    request = Request(BASE_URL + REPORTS_PATH, method="POST", data=body, headers={
        "Authorization": service_token,
        "Content-Type": "application/json",
        "Accept": "application/json",
    })
    try:
        with build_opener(ProxyHandler({}), _NoRedirect()).open(request, timeout=20) as response:
            if response.status == 204:
                return [], False
            if response.status != 200:
                raise FinanceUnavailable("WB Finance API вернул неожиданный ответ.")
            payload = response.read(MAX_RESPONSE_BYTES + 1)
    except HTTPError as error:
        if error.code == 429:
            raise FinanceUnavailable("WB ограничил частоту финансовых запросов.") from error
        raise FinanceUnavailable("WB не подтвердил финансовый доступ.") from error
    except (URLError, TimeoutError, OSError) as error:
        raise FinanceUnavailable("WB Finance API временно недоступен.") from error
    if len(payload) > MAX_RESPONSE_BYTES:
        raise FinanceUnavailable("Финансовый ответ WB превышает безопасный размер.")
    try:
        raw = json.loads(payload)
    except (UnicodeDecodeError, ValueError) as exc:
        raise FinanceUnavailable("Некорректный JSON ответа WB.") from exc
    rows = normalize_reports(raw)
    # One page only. A full page must never imply a complete report history.
    return rows, len(rows) == MAX_REPORTS

# R5.2: a deliberately bounded FIRST page of a named report, not a full ledger.
# Fields are WB's published finance field names. Do not request personal,
# bank, order, seller legal-name or label/serial identifiers.
DETAIL_PATH_PREFIX = "/api/finance/v1/sales-reports/detailed/"
MAX_DETAIL_ROWS = 30
DETAIL_FIELDS = (
    "reportId", "rrdId", "nmId", "currency", "saleDt",
    "retailAmount", "forPay", "sellerPromoId", "sellerPromoDiscount",
)


def _positive_id(value):
    if type(value) is not int or not 0 < value < 2**63:
        raise FinanceUnavailable("Некорректный ID финансового отчёта или строки.")
    return value


def _bounded_percent(value):
    # WB reports percentage fields as numbers. Never infer a 0 from missing data.
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return None
    try:
        percent = Decimal(str(value))
        if not percent.is_finite() or not 0 <= percent <= 100:
            return None
        return format(percent.quantize(Decimal("0.01")), "f")
    except (InvalidOperation, ValueError):
        return None


def normalize_report_detail_page(raw, expected_report_id):
    """Whitelist-only normalization; zero conclusions about funding or profit."""
    _positive_id(expected_report_id)
    if not isinstance(raw, list) or len(raw) > MAX_DETAIL_ROWS:
        raise FinanceUnavailable("Недопустимый размер детализации WB.")
    result, seen = [], set()
    for item in raw:
        if not isinstance(item, dict):
            raise FinanceUnavailable("Некорректная строка детализации WB.")
        report_id = _positive_id(item.get("reportId"))
        row_id = _positive_id(item.get("rrdId"))
        if report_id != expected_report_id or row_id in seen:
            raise FinanceUnavailable("Несоответствие отчёта или повтор строки WB.")
        seen.add(row_id)
        nm_id = item.get("nmId")
        if type(nm_id) is not int or not 0 < nm_id < 2**63:
            nm_id = None  # Some accounting adjustments have no product.
        currency = item.get("currency")
        if not isinstance(currency, str) or not re.fullmatch(r"[A-Z]{3}", currency):
            raise FinanceUnavailable("Неизвестная валюта детализации WB.")
        sale_date = item.get("saleDt")
        if isinstance(sale_date, str) and len(sale_date) <= 40:
            try:
                sale_date = _date_bound(sale_date[:10]).isoformat()
            except FinanceUnavailable:
                sale_date = None
        else:
            sale_date = None
        promo_id = item.get("sellerPromoId")
        if type(promo_id) is not int or not 0 < promo_id < 2**63:
            promo_id = None
        result.append({
            "report_id": report_id, "rrd_id": row_id, "nm_id": nm_id,
            "currency": currency, "sale_date": sale_date,
            "retail_amount": _money(item.get("retailAmount")),
            "for_pay": _money(item.get("forPay")),
            "seller_promo_id": promo_id,
            "seller_promo_discount_pct": _bounded_percent(item.get("sellerPromoDiscount")),
            "funding": "unknown", "profit_verified": False,
        })
    return result


def fetch_report_detail_page(service_token, report_id):
    """One manual page only, from a server-verified report ID. NEVER retry or paginate automatically."""
    if not settings.SAAS_WB_NETWORK_ENABLED or not settings.NOSALE_WB_FINANCE_ENABLED:
        raise FinanceUnavailable("WB Finance API отключён оператором.")
    service_token = require_finance_service_token(service_token)
    report_id = _positive_id(report_id)
    body = json.dumps({
        "limit": MAX_DETAIL_ROWS, "rrdId": 0, "fields": list(DETAIL_FIELDS),
    }, separators=(",", ":")).encode("utf-8")
    request = Request(
        BASE_URL + DETAIL_PATH_PREFIX + str(report_id),
        method="POST", data=body,
        headers={"Authorization": service_token, "Content-Type": "application/json",
                 "Accept": "application/json"},
    )
    try:
        with build_opener(ProxyHandler({}), _NoRedirect()).open(request, timeout=20) as response:
            if response.status == 204:
                return [], False  # Only a 204 explicitly proves no further rows.
            if response.status != 200:
                raise FinanceUnavailable("Неожиданный статус детализации WB.")
            payload = response.read(MAX_RESPONSE_BYTES + 1)
    except HTTPError as error:
        if error.code == 429:
            raise FinanceUnavailable("WB ограничил частоту финансовых запросов.") from error
        raise FinanceUnavailable("WB не подтвердил доступ к детализации.") from error
    except (URLError, TimeoutError, OSError) as error:
        raise FinanceUnavailable("Детализация WB временно недоступна.") from error
    if len(payload) > MAX_RESPONSE_BYTES:
        raise FinanceUnavailable("Слишком большой ответ детализации WB.")
    try:
        raw = json.loads(payload)
    except (UnicodeDecodeError, ValueError) as exc:
        raise FinanceUnavailable("Некорректный JSON детализации WB.") from exc
    return normalize_report_detail_page(raw, report_id), True
