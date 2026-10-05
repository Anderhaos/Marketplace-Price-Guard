FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt
COPY manage.py /app/
COPY portal /app/portal
COPY guard /app/guard
RUN useradd --system --create-home app && mkdir -p /data /backups /app/staticfiles && chown -R app:app /data /backups /app
USER app
EXPOSE 8000
