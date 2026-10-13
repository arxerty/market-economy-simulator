FROM python:3.12-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    MARKET_SIM_RUNS_DIR=/app/data/runs

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY market_sim ./market_sim
COPY experiments ./experiments
COPY tests ./tests
COPY pytest.ini .

EXPOSE 8000
CMD ["uvicorn", "market_sim.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
