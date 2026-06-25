FROM python:3.10-alpine

# Force stdin, stdout and stderr to be totally unbuffered
ENV PYTHONUNBUFFERED=1

# Install system dependencies (like git for git actions run by DevOpsWorker)
RUN apk add --no-cache git build-base


# Set up work directory
WORKDIR /app

# Install Python requirements
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code and default configurations
COPY src/ /app/src/
COPY config.yaml /app/
COPY jobs.yaml /app/

# Expose FastAPI REST API gateway port
EXPOSE 8000

# Default command starts the API gateway
CMD ["uvicorn", "src.api.server:app", "--host", "0.0.0.0", "--port", "8000"]
