FROM python:3.12-alpine

WORKDIR /app

RUN pip install --no-cache-dir requests

COPY bot.py /app/bot.py

RUN mkdir -p /app/data

ENV PYTHONUNBUFFERED=1
ENV TZ=Europe/Moscow

CMD ["python", "bot.py"]
