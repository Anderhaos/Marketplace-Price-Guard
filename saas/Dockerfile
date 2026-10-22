FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY manage.py .
COPY guard ./guard
COPY portal ./portal
COPY billing ./billing
RUN useradd --system --create-home app && mkdir -p /app/staticfiles && chown -R app:app /app/staticfiles
USER app
EXPOSE 8000
