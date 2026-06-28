FROM python:3.10-alpine

# Force stdin, stdout and stderr to be totally unbuffered
ENV PYTHONUNBUFFERED=1

# Install system dependencies
# readline-dev  → enables Tab-completion and arrow-key history in --chat mode
# git           → required by DevOpsWorker for git operations
# build-base    → C compiler for native Python extensions
RUN apk add --no-cache git build-base readline-dev


# Set up work directory
WORKDIR /app

# Install Python requirements
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code, CLI wrapper, and default configurations
COPY src/ /app/src/
COPY config.yaml /app/
COPY jobs.yaml /app/
COPY VERSION /app/
COPY agent-x1 /app/agent-x1
RUN chmod +x /app/agent-x1

# Expose FastAPI REST API gateway port
EXPOSE 8000

# Default command starts the API gateway
CMD ["uvicorn", "src.api.server:app", "--host", "0.0.0.0", "--port", "8000"]
