FROM python:3.12-alpine

WORKDIR /app

# Установка tzdata для корректной работы таймзоны Europe/Moscow в Alpine
RUN apk add --no-cache tzdata

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY bot.py /app/bot.py
COPY career_hub /app/career_hub
COPY miniapp_index.html /app/miniapp_index.html

# Создание каталога данных и непривилегированного пользователя appuser
RUN mkdir -p /app/data && \
    addgroup -S appgroup && adduser -S appuser -G appgroup && \
    chown -R appuser:appgroup /app

USER appuser

ENV PYTHONUNBUFFERED=1
ENV TZ=Europe/Moscow

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD wget -qO- http://127.0.0.1:8088/health || exit 1

CMD ["python", "bot.py"]
