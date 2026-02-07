FROM python:3.12-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/cache/*

# Copy requirements and install
COPY api/requirements.txt ./api_requirements.txt
RUN pip install --no-cache-dir -r api_requirements.txt
RUN pip install --no-cache-dir fastapi uvicorn psycopg2-binary python-dotenv

# Copy the application code
COPY api/ ./api/
COPY web/ ./web/
COPY chain-feeder/routing/ ./chain-feeder/routing/

# Set working directory to app root
WORKDIR /app

# Expose the portal port
EXPOSE 8000

# Run the server
CMD ["python", "api/main.py"]
