FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN pip install --no-cache-dir -U pip

COPY shared/py /app/shared/py
COPY services/hardware-service/requirements.txt /app/requirements.txt
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    python3-dev \
    liblgpio1 \
    liblgpio-dev \
    && pip install --no-cache-dir -r /app/requirements.txt \
    && apt-get purge -y gcc python3-dev liblgpio-dev \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir -e /app/shared/py

COPY services/hardware-service /app

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

