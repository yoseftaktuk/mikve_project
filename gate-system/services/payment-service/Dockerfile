FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DEFAULT_TIMEOUT=120 \
    PIP_RETRIES=10

RUN pip install --no-cache-dir -U pip

COPY shared/py /app/shared/py
COPY deploy/scripts/build-dns-check.sh /tmp/build-dns-check.sh
RUN chmod +x /tmp/build-dns-check.sh && /tmp/build-dns-check.sh
COPY services/payment-service/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt || \
    (sleep 5 && pip install --no-cache-dir -r /app/requirements.txt) || \
    (sleep 10 && pip install --no-cache-dir -r /app/requirements.txt)
RUN pip install --no-cache-dir -e /app/shared/py || \
    (sleep 5 && pip install --no-cache-dir -e /app/shared/py)

COPY services/payment-service /app

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
