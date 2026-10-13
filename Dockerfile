# Headless container for self-hosting the dashboard. The browser runs inside the container, so you cannot watch a
# window; the live log, screenshots and history still work. Build:  docker build -t self-healing-agent .
# Run:  docker run --rm -p 8501:8501 -v shdata:/app/data self-healing-agent
FROM python:3.11-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 HEADLESS=true

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
 && playwright install --with-deps firefox

COPY . .
EXPOSE 8501
CMD ["streamlit", "run", "app.py", "--server.address=0.0.0.0", "--server.port=8501", "--server.headless=true"]
