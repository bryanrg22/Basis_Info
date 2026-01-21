FROM python:3.12-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    poppler-utils \
    && rm -rf /var/lib/apt/lists/*

# Copy everything
COPY . .

# Install dependencies
RUN pip install --no-cache-dir -e ./evidence_layer || true
RUN pip install --no-cache-dir -e ./agentic

# Set Python path
ENV PYTHONPATH=/app/agentic

# Expose port
EXPOSE 8000

# Run the server
CMD ["uvicorn", "agentic.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
