#!/bin/bash

# Start Celery worker in the background
celery -A app.workers.celery_app worker --loglevel=info -P solo &

# Start FastAPI server using Gunicorn
exec gunicorn app.main:app -w 4 -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:8000 --access-logfile - --error-logfile -
