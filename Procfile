web: sh -c 'gunicorn wsgi:app --bind 0.0.0.0:${PORT:-8000} --workers ${WEB_CONCURRENCY:-2} --threads ${GUNICORN_THREADS:-4} --timeout ${GUNICORN_TIMEOUT:-180}'
scheduler: python scheduler_worker.py
