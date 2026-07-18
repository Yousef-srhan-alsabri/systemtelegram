from app import create_app
from app.extensions import celery

app = create_app()
app.app_context().push()

import worker_tasks  # noqa: E402,F401
