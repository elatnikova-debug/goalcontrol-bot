FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Создаём директорию для данных
RUN mkdir -p /app/data

ENV DB_PATH=/app/data/coach_bot.db

CMD ["python", "main.py"]
