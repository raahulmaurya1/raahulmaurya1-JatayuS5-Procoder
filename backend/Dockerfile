FROM python:3.12-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV DEBIAN_FRONTEND=noninteractive

# Install system dependencies (for OCR, MediaPipe, cv2)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \
    libglib2.0-0 \
    libgles2 \
    libegl1 \
    gcc \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install -r requirements.txt

# --no-cache-dir

# Copy project files
COPY . .

# Copy start script
COPY start.sh .
RUN chmod +x start.sh

# Expose FastAPI port
EXPOSE 8000

# Start command to run both Celery and FastAPI
CMD ["./start.sh"]
