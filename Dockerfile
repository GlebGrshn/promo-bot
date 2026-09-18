FROM python:3.12-slim

# Логи сразу в вывод, зона по умолчанию (реальная берётся из TIMEZONE в .env)
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Europe/Moscow

WORKDIR /app

# Сначала зависимости (кэшируется отдельным слоем)
COPY requirements-max.txt .
RUN pip install --no-cache-dir -r requirements-max.txt

# Затем код
COPY app ./app
COPY run_max.py .

# Каталог данных (том монтируется снаружи для сохранности БД и фото)
RUN mkdir -p /app/data/media
VOLUME ["/app/data"]

CMD ["python", "run_max.py"]
