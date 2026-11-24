FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY requirements.txt ./
RUN python -m pip install --no-cache-dir -r requirements.txt

RUN useradd --create-home --uid 10001 appuser
COPY --chown=appuser:appuser *.py ./
COPY --chown=appuser:appuser .streamlit/config.toml .streamlit/config.toml
COPY --chown=appuser:appuser styles/*.css ./styles/
COPY --chown=appuser:appuser scripts/*.py ./scripts/
# ca.pe[m] permits a mounted CA in a clean checkout where ca.pem is gitignored.
COPY --chown=appuser:appuser requirements.txt ca.pe[m] ./
USER appuser

EXPOSE 8501
CMD ["sh", "-c", "python -m scripts.check_release_schema && exec python -m streamlit run app.py --server.address=0.0.0.0 --server.port=8501 --server.headless=true"]

