# NoSale: authenticated transactional mail from nosale.pro

Target visible From and envelope sender: NoSale <noreply@nosale.pro>,
with aligned SPF, DKIM and DMARC. Do not run a mail server on the NoSale VPS.

## Preparation on REG.RU

1. ISPmanager on existing REG.RU hosting: Почта → Почтовые домены.
   Add mail domain nosale.pro (without changing website A/NS).
   Enable DKIM on the new mail domain.
2. Create an authenticated mailbox noreply@nosale.pro with a new password.
   Keep the secret out of Git and chat.
3. On authoritative ns1.reg.ru and ns2.reg.ru, add MX, one SPF TXT
   authorizing REG.RU outgoing servers, and DKIM TXT from ISPmanager.
   Never invent the actual DKIM selector/key; obtain it from the provider.
4. Add DMARC TXT at _dmarc.nosale.pro with p=none initially.
   Move to quarantine/reject after verifying authorization.
5. Send to multiple mailboxes; inspect the raw message headers for
   spf=pass, dkim=pass d=nosale.pro and dmarc=pass.
   SMTP 250 alone does not confirm delivery.

## Russian VPS settings (private saas/.env, never Git)

    DJANGO_EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
    SMTP_HOST=mail.hosting.reg.ru
    SMTP_PORT=465
    SMTP_USE_TLS=0
    SMTP_USE_SSL=1
    SMTP_TIMEOUT=12
    SMTP_USER=noreply@nosale.pro
    SMTP_PASSWORD=<mailbox-secret>
    SMTP_FROM=NoSale <noreply@nosale.pro>

Only switch production after DNS, verified sender and real email
delivery are confirmed. The existing trusted HTTPS relay remains active
until then. Do not simply spoof the displayed From while retaining an
unrelated SMTP envelope sender or unauthorized SPF.
