"""Self-contained branded OTP mail with HTML and plain-text alternatives."""
from html import escape

from django.conf import settings
from django.core.mail import EmailMultiAlternatives


def send_pin_verification_email(*, email: str, code: str, purpose: str) -> None:
    if len(code) != 4 or not code.isascii() or not code.isdecimal():
        raise ValueError("Invalid four-digit OTP")
    subject = ("Код подтверждения MP NoSale"
               if purpose == "register" else "Восстановление PIN MP NoSale")
    text = (
        f"Ваш код MP NoSale: {code}\n"
        "Код действует 10 минут.\n"
        "Если вы не запрашивали код, проигнорируйте это письмо."
    )
    safe_code = escape(code)
    html = f"""<!doctype html>
<html lang="ru">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:24px;background:#0a0a0d;color:#f5f4f1;font-family:Arial,sans-serif">
  <table role="presentation" cellspacing="0" cellpadding="0" border="0"
         style="max-width:480px;width:100%;margin:0 auto;border:1px solid #303034;border-radius:15px;overflow:hidden;background:#161618">
    <tr><td style="background:#ff5a1f;color:#0b0b0c;text-align:center;padding:18px 12px;font-size:22px;font-weight:800">MP NoSale</td></tr>
    <tr><td style="padding:32px 24px;text-align:center">
      <h1 style="font-size:22px;margin:0 0 12px;color:#f5f4f1">Код подтверждения</h1>
      <p style="color:#b6b4b8;margin:0 0 24px;font-size:15px">Введите этот 4-значный код на сайте NoSale.</p>
      <div style="padding:19px 12px;border:1px solid #ff5a1f;border-radius:12px;background:#332016;color:#ffffff;letter-spacing:10px;font-size:32px;font-weight:750">{safe_code}</div>
      <p style="color:#99989b;font-size:13px;margin:20px 0 0">Действует 10 минут</p>
    </td></tr>
    <tr><td style="padding:17px;color:#99989b;text-align:center;border-top:1px solid #303034;font-size:12px">
      Если вы не запрашивали код, проигнорируйте письмо.
    </td></tr>
  </table>
</body>
</html>"""
    message = EmailMultiAlternatives(
        subject=subject,
        body=text,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[email],
    )
    message.attach_alternative(html, "text/html")
    message.send(fail_silently=False)
