FROM python:3.12-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/cache/*

# Copy requirements and install
COPY routing-web/requirements.txt ./routing_web_requirements.txt
RUN pip install --no-cache-dir -r routing_web_requirements.txt
RUN pip install --no-cache-dir fastapi uvicorn psycopg2-binary python-dotenv

# Copy the application code
COPY routing-web/ ./routing-web/
COPY lp-backtester/ ./lp-backtester/

# Set working directory to routing-web as it contains the server
WORKDIR /app/routing-web

# Expose the portal port
EXPOSE 8000

# Run the server
CMD ["python", "server.py"]
