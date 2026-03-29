FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV DATA_DIR=/app/data
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

HEALTHCHECK --interval=60s --timeout=10s --retries=3 \
    CMD python -c "from pathlib import Path; assert Path('/app/data/deals.db').exists()" || exit 1

STOPSIGNAL SIGTERM

CMD ["python", "main.py"]
