FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY neurofinance ./neurofinance
COPY media ./media
VOLUME ["/data"]
ENV DB_PATH=/data/neurofinance.db
ENV PYTHONUNBUFFERED=1
CMD ["python", "-m", "neurofinance"]
