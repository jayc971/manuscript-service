from arq.connections import RedisSettings
from app.core.config import settings
from app.models.user import User
from app.models.manuscript import Manuscript, ManuscriptStage, NotificationKey
from app.workers.tasks import process_manuscript, notify_editor_decision

class WorkerSettings:
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    functions = [process_manuscript, notify_editor_decision]
    max_jobs = 10
