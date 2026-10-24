FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# Official Russian Trusted CA (MinTsifry), required by Tochka TLS.
# Source and validity: https://developers.tochka.com/docs/tochka-api/certificate
COPY certs/russian_trusted_root_ca_pem.crt /usr/local/share/ca-certificates/russian-trusted-root.crt
COPY certs/russian_trusted_sub_ca_pem.crt /usr/local/share/ca-certificates/russian-trusted-sub.crt
RUN update-ca-certificates
ENV SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt
COPY manage.py .
COPY guard ./guard
COPY portal ./portal
COPY billing ./billing
COPY push ./push
RUN useradd --system --create-home app && mkdir -p /app/staticfiles && chown -R app:app /app/staticfiles
USER app
EXPOSE 8000
