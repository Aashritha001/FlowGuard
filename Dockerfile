FROM python:3.12-slim
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY backend ./backend
COPY frontend ./frontend
WORKDIR /srv/backend
RUN useradd -r flowguard && mkdir -p /srv/data && chown -R flowguard /srv
USER flowguard
EXPOSE 8000
CMD ["sh", "-c", "python -m app.setup && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
