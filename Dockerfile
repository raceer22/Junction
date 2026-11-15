FROM python:3.12-slim

ARG SPREAD_URL=https://spread.bahnexpress.com

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN pip install --no-cache-dir "${SPREAD_URL}/kit/spread-0.1.0-py3-none-any.whl"

COPY dispatch.py custom_args.py ./
COPY website/ ./website/

EXPOSE 8000

CMD ["python", "website/server.py", "--host", "0.0.0.0", "--port", "8000"]
