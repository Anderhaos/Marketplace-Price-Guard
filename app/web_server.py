import sys
import threading
import time
from datetime import datetime
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlencode, urlparse

from config import APP_MODE, AUTO_CHECK_ENABLED, CHECK_INTERVAL_SECONDS, LIVE_MAX_FIXES
from database import (
    add_history,
    delete_empty_checks,
    get_app_setting,
    get_marketplace_token,
    get_marketplace_token_hint,
    init_db,
    list_history_by_date,
    list_history_dates,
    save_app_setting,
    save_marketplace_token,
)
from mailer import send_email
from marketplaces import MARKETPLACES, create_marketplace_client, get_marketplace_meta
from rules import check_discount_rule


APP_NAME = "Marketplace Price Guard"

MONITOR_STATE = {
    "enabled": AUTO_CHECK_ENABLED,
    "last_run": "не запускался",
    "last_message": "Автомонитор ожидает первого запуска.",
    "running": False,
}
SORT_COLUMNS = {
    "name": {"label": "Артикул продавца", "type": "text"},
    "nm_id": {"label": "nmID", "type": "number"},
    "price": {"label": "Цена", "type": "number"},
    "discounted_price": {"label": "Цена со скидкой", "type": "number"},
    "current_discount": {"label": "Скидка продавца", "type": "number"},
    "allowed_discount": {"label": "Разрешено", "type": "number"},
    "anti_discount": {"label": "Антискидка", "type": "status"},
    "auto_check": {"label": "Автопроверка", "type": "status"},
    "action": {"label": "Действие", "type": "status"},
}


def product_status_value(product):
    current_discount = product.get("current_discount", 0) or 0
    allowed_discount = product.get("allowed_discount", 0) or 0
    return 1 if current_discount > allowed_discount else 0


def product_sort_value(product, sort_key):
    if sort_key in {"anti_discount", "auto_check", "action"}:
        return product_status_value(product)
    value = product.get(sort_key, "")
    if SORT_COLUMNS.get(sort_key, {}).get("type") == "number":
        try:
            return float(value or 0)
        except (TypeError, ValueError):
            return 0
    return str(value or "").lower()


def sort_products(products, sort_key, sort_dir):
    if sort_key not in SORT_COLUMNS:
        sort_key = "price"
    return sorted(products, key=lambda item: product_sort_value(item, sort_key), reverse=(sort_dir == "desc"))


def sort_link(sort_key, current_sort, current_dir):
    next_dir = "desc" if sort_key == current_sort and current_dir == "asc" else "asc"
    arrow = ""
    if sort_key == current_sort:
        arrow = " ↓" if current_dir == "desc" else " ↑"
    query = urlencode({"sort": sort_key, "dir": next_dir})
    label = SORT_COLUMNS[sort_key]["label"]
    return f'<a class="sort-link" href="/?{query}">{escape(label)}{arrow}</a>'


def get_selected_marketplace_id():
    marketplace_id = str(get_app_setting("marketplace_id", "wildberries") or "wildberries")
    return marketplace_id if marketplace_id in MARKETPLACES else "wildberries"


def get_selected_marketplace_meta():
    return get_marketplace_meta(get_selected_marketplace_id())


def get_api():
    marketplace_id = get_selected_marketplace_id()
    meta = get_marketplace_meta(marketplace_id)
    token = get_marketplace_token(marketplace_id)

    if APP_MODE != "DEMO" and not token:
        raise ValueError(
            f"API токен для {meta.name} не добавлен. "
            "Открой раздел 'Маркетплейс и автоцикл' и вставь токен."
        )

    return create_marketplace_client(marketplace_id, token)


def get_live_max_fixes():
    try:
        value = int(get_app_setting("live_max_fixes", LIVE_MAX_FIXES))
    except (TypeError, ValueError):
        value = LIVE_MAX_FIXES
    return max(1, min(value, 5000))


def get_check_interval_seconds():
    try:
        value = int(get_app_setting("check_interval_seconds", CHECK_INTERVAL_SECONDS))
    except (TypeError, ValueError):
        value = CHECK_INTERVAL_SECONDS
    return max(60, min(value, 86400))


def is_auto_check_enabled():
    value = str(get_app_setting("auto_check_enabled", "1" if AUTO_CHECK_ENABLED else "0"))
    return value == "1"


def is_email_notifications_enabled():
    return str(get_app_setting("email_notifications_enabled", "0")) == "1"


def get_notification_email():
    return str(get_app_setting("notification_email", "") or "").strip()


def send_notification_if_enabled(subject, body):
    if not is_email_notifications_enabled():
        return
    try:
        ok, message = send_email(get_notification_email(), subject, body)
        if not ok:
            print(f"Email notification skipped: {message}")
    except Exception as error:
        print(f"Email notification error: {error}")


def save_monitor_settings(auto_enabled, interval_minutes, live_max_fixes, email_enabled=False, notification_email=""):
    try:
        interval_minutes = int(interval_minutes)
        live_max_fixes = int(live_max_fixes)
    except (TypeError, ValueError) as error:
        raise ValueError("Интервал и лимит должны быть числами.") from error

    interval_seconds = max(1, min(interval_minutes, 1440)) * 60
    live_max_fixes = max(1, min(live_max_fixes, 5000))
    save_app_setting("auto_check_enabled", "1" if auto_enabled else "0")
    save_app_setting("check_interval_seconds", interval_seconds)
    save_app_setting("live_max_fixes", live_max_fixes)
    save_app_setting("email_notifications_enabled", "1" if email_enabled else "0")
    save_app_setting("notification_email", notification_email.strip())


def get_products_safe():
    try:
        return get_api().get_products(), None
    except Exception as error:
        return [], str(error)


def ping_token(token):
    marketplace_id = get_selected_marketplace_id()
    meta = get_marketplace_meta(marketplace_id)
    try:
        create_marketplace_client(marketplace_id, token).ping()
        return True, f"{meta.name} API подключен. Токен работает."
    except Exception as error:
        return False, f"Ошибка проверки токена: {error}"


def run_discount_check():
    api = get_api()
    actions = []
    fixed_count = 0
    live_max_fixes = get_live_max_fixes()
    products = api.get_products()
    stats = build_stats(products)

    for product in products:
        result = check_discount_rule(product)
        if not result["needs_fix"]:
            continue

        if APP_MODE != "LIVE":
            actions.append(f"DRY-RUN: {result['message']} — реальные изменения не выполнялись")
            continue

        if fixed_count >= live_max_fixes:
            actions.append(
                f"Лимит исправлений {live_max_fixes} за цикл достигнут. "
                f"{product['name']} ({product['nm_id']}) пока не исправлен."
            )
            continue

        response = api.update_discount(product["nm_id"], result["new_discount"], product.get("price"))
        fixed_count += 1
        actions.append(f"LIVE: {result['message']}. Ответ API: {response}")

    if not actions:
        message = "Все скидки в норме. Исправлять нечего."
    elif APP_MODE == "LIVE":
        message = f"LIVE-исправлений отправлено: {fixed_count}.\n" + "\n".join(actions)
    else:
        message = "\n".join(actions)

    if actions:
        add_history(stats, APP_MODE, message)
        send_notification_if_enabled(f"{APP_NAME}: действия со скидками", message)
    return message


def fix_discount(nm_id, price, target_discount=0):
    api = get_api()
    products = api.get_products()
    stats = build_stats(products)
    product = next((item for item in products if str(item.get("nm_id")) == str(nm_id)), None)

    if not product:
        message = f"Товар nmID={nm_id} не найден в текущем списке маркетплейса."
        add_history(stats, APP_MODE, message)
        send_notification_if_enabled(f"{APP_NAME}: товар не найден", message)
        return message

    current_discount = product.get("current_discount", 0) or 0
    allowed_discount = product.get("allowed_discount", 0) or 0
    target_discount = int(target_discount)

    if current_discount <= allowed_discount:
        message = f"{product['name']} ({product['nm_id']}): скидка уже в норме, исправление не требуется."
        return message

    if APP_MODE != "LIVE":
        message = (
            f"DRY-RUN: {product['name']} ({product['nm_id']}): "
            f"скидка сейчас {current_discount}%, в LIVE режиме поставил бы {target_discount}%. "
            "Реальные изменения не выполнялись."
        )
        add_history(stats, APP_MODE, message)
        send_notification_if_enabled(f"{APP_NAME}: dry-run", message)
        return message

    response = api.update_discount(product["nm_id"], target_discount, price or product.get("price"))
    message = (
        f"LIVE: {product['name']} ({product['nm_id']}): "
        f"отправлена задача API на изменение скидки {current_discount}% -> {target_discount}%. "
        f"Ответ API: {response}"
    )
    add_history(stats, APP_MODE, message)
    send_notification_if_enabled(f"{APP_NAME}: скидка исправлена", message)
    return message


def build_stats(products):
    total = len(products)
    problem = sum(1 for item in products if (item.get("current_discount", 0) or 0) > (item.get("allowed_discount", 0) or 0))
    ok = total - problem
    max_discount = max([(item.get("current_discount", 0) or 0) for item in products], default=0)
    return {"total": total, "problem": problem, "ok": ok, "max_discount": max_discount}


def render_products_rows(products):
    if not products:
        return '<tr><td colspan="9" class="empty">Товары пока не отображаются. Открой раздел "Маркетплейс и автоцикл" и вставь API токен.</td></tr>'
    rows = []
    auto_check_label = "Включена" if is_auto_check_enabled() else "Выключена"
    auto_check_class = "ok" if is_auto_check_enabled() else "muted"
    for product in products:
        current_discount = product.get("current_discount", 0) or 0
        allowed_discount = product.get("allowed_discount", 0) or 0
        is_bad = current_discount > allowed_discount
        anti_discount = f"Сбросить до {allowed_discount}%" if is_bad else "Не требуется"
        anti_class = "bad" if is_bad else "ok"
        price = product.get("price", 0) or 0
        discounted_price = product.get("discounted_price", 0) or 0
        currency = product.get("currency") or "RUB"
        action_html = "Не требуется"
        if is_bad:
            button_text = f"Сбросить на {allowed_discount}%" if APP_MODE == "LIVE" else f"Dry-run: {allowed_discount}%"
            action_html = f"""
              <form method="post" action="/fix-discount" style="margin:0;">
                <input type="hidden" name="nm_id" value="{escape(str(product.get('nm_id', '')))}">
                <input type="hidden" name="price" value="{escape(str(price))}">
                <button class="small-button" type="submit">{button_text}</button>
              </form>
            """
        rows.append(
            f"""
            <tr>
              <td>{escape(str(product.get('name', '')))}</td>
              <td>{escape(str(product.get('nm_id', '')))}</td>
              <td>{escape(str(price))} {escape(str(currency))}</td>
              <td>{escape(str(discounted_price))} {escape(str(currency))}</td>
              <td>{escape(str(current_discount))}%</td>
              <td>{escape(str(allowed_discount))}%</td>
              <td class="{anti_class}">{anti_discount}</td>
              <td class="{auto_check_class}">{auto_check_label}</td>
              <td>{action_html}</td>
            </tr>
            """
        )
    return "\n".join(rows)


def render_layout(title, body, message=None):
    message_html = f'<div class="result">{escape(message)}</div>' if message else ""
    nav = """
    <nav>
      <a href="/">Дашборд</a>
      <a href="/settings">Маркетплейс и автоцикл</a>
      <a href="/history">История</a>
    </nav>
    """
    return f"""
<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(title)} — {APP_NAME}</title>
  <style>
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; font-family: Arial, sans-serif; background: #f4f6fb; color: #1f2937; }}
    header {{ background: #2457d6; color: white; padding: 22px 32px; }}
    header h1 {{ margin: 0 0 6px; font-size: 28px; }}
    header p {{ margin: 0; opacity: .92; }}
    main {{ max-width: 1180px; margin: 24px auto; padding: 0 20px 40px; }}
    nav {{ display: flex; gap: 10px; margin-bottom: 18px; flex-wrap: wrap; }}
    nav a, button, .button {{ border: 0; border-radius: 8px; padding: 11px 14px; font-weight: 700; text-decoration: none; cursor: pointer; display: inline-block; }}
    .small-button {{ padding: 8px 10px; font-size: 12px; background: #dc2626; color: white; }}
    nav a, .button.secondary {{ background: white; color: #1f2937; border: 1px solid #d9dce3; }}
    button.primary, .button.primary {{ background: #2457d6; color: white; }}
    .status-line {{ display: flex; gap: 14px; align-items: center; flex-wrap: wrap; margin-bottom: 18px; color: #4b5563; font-size: 14px; }}
    .grid {{ display: grid; grid-template-columns: repeat(6, minmax(0, 1fr)); gap: 12px; margin-bottom: 18px; }}
    .card {{ background: white; border: 1px solid #e5e7eb; border-radius: 8px; padding: 16px; }}
    .card-title {{ color: #6b7280; font-size: 13px; margin-bottom: 8px; }}
    .card-value {{ font-size: 24px; font-weight: 800; }}
    table {{ width: 100%; border-collapse: collapse; background: white; border-radius: 8px; overflow: hidden; }}
    th, td {{ padding: 12px; border-bottom: 1px solid #eceef3; text-align: left; }}
    th {{ background: #fafafa; color: #626774; font-size: 13px; }}
    th a.sort-link {{ color: inherit; text-decoration: none; display: inline-block; }}
    th a.sort-link:hover {{ color: #2457d6; text-decoration: underline; }}
    .bad {{ color: #dc2626; font-weight: 700; }}
    .ok {{ color: #15803d; font-weight: 700; }}
    .empty, .muted {{ color: #6b7280; }}
    .mode {{ display: inline-block; padding: 4px 8px; border-radius: 999px; background: white; color: #2457d6; font-weight: 700; margin-left: 8px; font-size: 14px; }}
    .result {{ margin-top: 18px; background: white; border-radius: 8px; padding: 16px; white-space: pre-wrap; border: 1px solid #e5e7eb; }}
    label {{ display: block; font-weight: 700; margin-bottom: 8px; }}
    input, select {{ width: 100%; padding: 11px 12px; border: 1px solid #d1d5db; border-radius: 8px; margin-bottom: 14px; background: white; }}
    input[type="checkbox"] {{ width: auto; margin-right: 8px; }}
    input[type="date"] {{ max-width: 220px; }}
    .form-card {{ max-width: 720px; }}
    .date-picker {{ display: flex; gap: 10px; align-items: end; flex-wrap: wrap; margin-bottom: 16px; }}
    .date-list {{ display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 16px; }}
    .date-pill {{ background: white; color: #1f2937; border: 1px solid #d9dce3; border-radius: 999px; padding: 8px 11px; text-decoration: none; font-weight: 700; }}
    .date-pill.active {{ background: #2457d6; color: white; border-color: #2457d6; }}
    pre {{ white-space: pre-wrap; }}
    @media (max-width: 800px) {{ .grid {{ grid-template-columns: 1fr 1fr; }} table {{ font-size: 13px; }} }}
  </style>
</head>
<body>
  <header>
    <h1>{APP_NAME} <span class="mode">{escape(APP_MODE)}</span></h1>
    <p>Мониторинг и автоисправление скидок продавца на маркетплейсах</p>
  </header>
  <main>
    {nav}
    {body}
    {message_html}
  </main>
</body>
</html>
"""


def render_dashboard(message=None, sort_key="price", sort_dir="desc"):
    live_max_fixes = get_live_max_fixes()
    check_interval_seconds = get_check_interval_seconds()
    auto_check_enabled = is_auto_check_enabled()
    monitor_status = "включен" if auto_check_enabled else "выключен"
    monitor_text = f"Автомонитор {monitor_status}, последний запуск: {MONITOR_STATE['last_run']}"
    mode_warning = f"LIVE: автоисправление включено, лимит {live_max_fixes} за цикл" if APP_MODE == "LIVE" else "READ_ONLY: изменения отключены"
    meta = get_selected_marketplace_meta()
    products, error = get_products_safe()
    products = sort_products(products, sort_key, sort_dir)
    stats = build_stats(products)
    if error and not message:
        message = error
    body = f"""
    <div class="status-line">
      <span><strong>{APP_MODE}</strong></span>
      <span>Маркетплейс: <strong>{escape(meta.name)}</strong></span>
      <span>{mode_warning}</span>
      <span>{monitor_text}</span>
    </div>
    <div class="grid">
      <div class="card"><div class="card-title">Всего товаров</div><div class="card-value">{stats['total']}</div></div>
      <div class="card"><div class="card-title">OK</div><div class="card-value ok">{stats['ok']}</div></div>
      <div class="card"><div class="card-title">К исправлению</div><div class="card-value bad">{stats['problem']}</div></div>
      <div class="card"><div class="card-title">Макс. скидка</div><div class="card-value">{stats['max_discount']}%</div></div>
      <div class="card"><div class="card-title">Лимит цикла</div><div class="card-value">{live_max_fixes}</div></div>
      <div class="card"><div class="card-title">Автоцикл</div><div class="card-value">{check_interval_seconds // 60} мин</div></div>
    </div>
    <form method="post" action="/check" style="margin-bottom: 18px; display: inline-block;"><button class="primary" type="submit">Проверить и исправить</button></form>
    <a class="button secondary" href="/">Обновить</a>
    <table>
      <thead><tr>
        <th>{sort_link('name', sort_key, sort_dir)}</th>
        <th>{sort_link('nm_id', sort_key, sort_dir)}</th>
        <th>{sort_link('price', sort_key, sort_dir)}</th>
        <th>{sort_link('discounted_price', sort_key, sort_dir)}</th>
        <th>{sort_link('current_discount', sort_key, sort_dir)}</th>
        <th>{sort_link('allowed_discount', sort_key, sort_dir)}</th>
        <th>{sort_link('anti_discount', sort_key, sort_dir)}</th>
        <th>{sort_link('auto_check', sort_key, sort_dir)}</th>
        <th>{sort_link('action', sort_key, sort_dir)}</th>
      </tr></thead>
      <tbody>{render_products_rows(products)}</tbody>
    </table>
    """
    return render_layout("Дашборд", body, message=message)


def render_settings(message=None):
    selected_marketplace_id = get_selected_marketplace_id()
    meta = get_selected_marketplace_meta()
    token_info = get_marketplace_token_hint(selected_marketplace_id)
    token_status = f"заполнен ({escape(token_info['token_hint'])})" if token_info else "не заполнен"
    live_max_fixes = get_live_max_fixes()
    interval_minutes = get_check_interval_seconds() // 60
    checked = "checked" if is_auto_check_enabled() else ""
    email_checked = "checked" if is_email_notifications_enabled() else ""
    notification_email = escape(get_notification_email())
    marketplace_options = []
    for marketplace_id, marketplace in MARKETPLACES.items():
        selected = "selected" if marketplace_id == selected_marketplace_id else ""
        suffix = "" if marketplace.implemented else " — скоро"
        marketplace_options.append(
            f'<option value="{escape(marketplace_id)}" {selected}>{escape(marketplace.name + suffix)}</option>'
        )
    body = f"""
    <div class="card form-card">
      <h2>Маркетплейс и API токен</h2>
      <p class="muted">Текущий маркетплейс: {escape(meta.name)}. Токен: {token_status}.</p>
      <form method="post" action="/settings">
        <label>Маркетплейс</label>
        <select name="marketplace_id">
          {''.join(marketplace_options)}
        </select>
        <label>{escape(meta.token_label)}</label>
        <input name="token" type="password" placeholder="Вставить токен" required>
        <p class="muted">{escape(meta.token_help)}</p>
        <button class="primary" type="submit">Сохранить подключение</button>
      </form>
    </div>
    <div class="card form-card" style="margin-top: 18px;">
      <h2>Автоцикл и лимит</h2>
      <form method="post" action="/monitor-settings">
        <label><input name="auto_check_enabled" type="checkbox" value="1" {checked}>Автоматический режим</label>
        <label>Интервал проверки, минут</label>
        <input name="interval_minutes" type="number" min="1" max="1440" value="{interval_minutes}" required>
        <label>Лимит исправлений за один цикл</label>
        <input name="live_max_fixes" type="number" min="1" max="5000" value="{live_max_fixes}" required>
        <label><input name="email_notifications_enabled" type="checkbox" value="1" {email_checked}>Уведомлять на почту при исправлениях и ошибках</label>
        <label>Email для уведомлений</label>
        <input name="notification_email" type="email" placeholder="seller@example.com" value="{notification_email}">
        <button class="primary" type="submit">Сохранить автоцикл</button>
      </form>
    </div>
    """
    return render_layout("Маркетплейс и автоцикл", body, message=message)


def render_history(message=None, selected_date=None):
    dates = list_history_dates()
    if selected_date is None:
        selected_date = dates[0]["date"] if dates else datetime.utcnow().date().isoformat()
    rows = list_history_by_date(selected_date)
    date_buttons = []
    for item in dates[:31]:
        active = " active" if item["date"] == selected_date else ""
        date_buttons.append(
            f'<a class="date-pill{active}" href="/history?date={escape(item["date"])}">{escape(item["date"])} ({item["count"]})</a>'
        )
    dates_html = "".join(date_buttons) if date_buttons else '<span class="muted">Дат с событиями пока нет.</span>'
    if not rows:
        history_html = f'<p class="muted">За {escape(selected_date)} событий нет.</p>'
    else:
        table_rows = []
        for row in rows:
            table_rows.append(
                f"<tr><td>{escape(row['checked_at'])}</td><td>{escape(row['mode'])}</td><td>{row['total']}</td><td class='ok'>{row['ok_count']}</td><td class='bad'>{row['problem_count']}</td><td>{row['max_discount']}%</td><td><pre>{escape(row['message'])}</pre></td></tr>"
            )
        history_html = f"""
        <table><thead><tr><th>Время UTC</th><th>Режим</th><th>Всего</th><th>OK</th><th>Антискидка</th><th>Макс.</th><th>Сообщение</th></tr></thead><tbody>{''.join(table_rows)}</tbody></table>
        """
    body = f"""
    <div class='card'>
      <h2>История действий</h2>
      <form class="date-picker" method="get" action="/history">
        <div>
          <label>Выбрать дату</label>
          <input name="date" type="date" value="{escape(selected_date)}">
        </div>
        <button class="primary" type="submit">Показать день</button>
      </form>
      <div class="date-list">{dates_html}</div>
      <form method="post" action="/clear-empty-history" style="margin-bottom: 14px;">
        <button class="button secondary" type="submit">Очистить пустые проверки</button>
      </form>
      {history_html}
    </div>
    """
    return render_layout("История", body, message=message)


def run_auto_monitor_once():
    if not is_auto_check_enabled():
        MONITOR_STATE["last_message"] = "Автомонитор выключен в настройках."
        return
    if MONITOR_STATE["running"]:
        return
    MONITOR_STATE["running"] = True
    try:
        message = run_discount_check()
        MONITOR_STATE["last_message"] = message
    except Exception as error:
        MONITOR_STATE["last_message"] = f"Ошибка автомонитора: {error}"
        send_notification_if_enabled(f"{APP_NAME}: ошибка автомонитора", MONITOR_STATE["last_message"])
    finally:
        MONITOR_STATE["last_run"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        MONITOR_STATE["running"] = False


def auto_monitor_loop():
    run_auto_monitor_once()
    while True:
        time.sleep(get_check_interval_seconds())
        run_auto_monitor_once()


def start_auto_monitor():
    thread = threading.Thread(target=auto_monitor_loop, daemon=True)
    thread.start()

class WebHandler(BaseHTTPRequestHandler):
    def _send_html(self, html, status=200):
        body = html.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_form(self):
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length).decode("utf-8")
        return parse_qs(raw)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)
        sort_key = (query.get("sort") or ["price"])[0]
        sort_dir = (query.get("dir") or ["desc"])[0]
        if sort_dir not in {"asc", "desc"}:
            sort_dir = "desc"

        if path == "/":
            self._send_html(render_dashboard(sort_key=sort_key, sort_dir=sort_dir))
            return
        if path == "/settings":
            self._send_html(render_settings())
            return
        if path == "/history":
            selected_date = (query.get("date") or [None])[0]
            self._send_html(render_history(selected_date=selected_date))
            return
        self.send_error(404)

    def do_POST(self):
        path = urlparse(self.path).path
        form = self._read_form()

        if path == "/settings":
            marketplace_id = (form.get("marketplace_id") or ["wildberries"])[0].strip()
            if marketplace_id not in MARKETPLACES:
                marketplace_id = "wildberries"
            save_app_setting("marketplace_id", marketplace_id)
            token = (form.get("token") or [""])[0].strip()
            if not token:
                self._send_html(render_settings("Токен не заполнен."))
                return
            ok, message = ping_token(token)
            if ok:
                save_marketplace_token(marketplace_id, token)
                message = "Подключение проверено, токен сохранен локально в базе."
            self._send_html(render_settings(message))
            return

        if path == "/monitor-settings":
            auto_enabled = (form.get("auto_check_enabled") or ["0"])[0] == "1"
            email_enabled = (form.get("email_notifications_enabled") or ["0"])[0] == "1"
            interval_minutes = (form.get("interval_minutes") or [""])[0].strip()
            live_max_fixes = (form.get("live_max_fixes") or [""])[0].strip()
            notification_email = (form.get("notification_email") or [""])[0].strip()
            try:
                save_monitor_settings(auto_enabled, interval_minutes, live_max_fixes, email_enabled, notification_email)
                message = "Режим, лимит и уведомления сохранены. Новые значения применяются без перезапуска."
            except Exception as error:
                message = f"Ошибка сохранения настроек: {error}"
            self._send_html(render_settings(message))
            return

        if path == "/check":
            try:
                message = run_discount_check()
            except Exception as error:
                message = f"Ошибка проверки правил: {error}"
            self._send_html(render_dashboard(message=message))
            return

        if path == "/clear-empty-history":
            try:
                deleted_count = delete_empty_checks()
                message = f"Удалено пустых проверок: {deleted_count}."
            except Exception as error:
                message = f"Ошибка очистки истории: {error}"
            self._send_html(render_history(message=message))
            return

        if path == "/fix-discount":
            nm_id = (form.get("nm_id") or [""])[0].strip()
            price = (form.get("price") or [""])[0].strip()
            try:
                message = fix_discount(nm_id, price, 0)
            except Exception as error:
                message = f"Ошибка исправления скидки: {error}"
            self._send_html(render_dashboard(message=message))
            return

        self.send_error(404)


def main():
    init_db()
    start_auto_monitor()
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    server = ThreadingHTTPServer(("127.0.0.1", port), WebHandler)
    print(f"{APP_NAME} запущен: http://127.0.0.1:{port}")
    server.serve_forever()


if __name__ == "__main__":
    main()











