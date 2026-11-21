from arq import run_worker
from arq.connections import RedisSettings
from app.core.config import settings

class WorkerSettings:
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    functions = []
