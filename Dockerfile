# Dashboard image for Azure Container Apps (or any container host).
# Build and run locally:   docker build -t momentum-engine .   then   docker run -p 8501:8501 --env-file .env momentum-engine
FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src ./src
EXPOSE 8501
# One replica only: a running match lives in the memory of one process.
CMD ["streamlit", "run", "src/app.py", "--server.port=8501", "--server.address=0.0.0.0", "--server.headless=true", "--browser.gatherUsageStats=false"]
