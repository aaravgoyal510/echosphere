import json
import logging
from typing import Optional
import redis
from dialogue_manager.models import SessionState

logger = logging.getLogger(__name__)

class SessionStateManager:
    def __init__(self, host: str = "localhost", port: int = 6379, db: int = 0, ttl: int = 1800):
        self.ttl = ttl
        self.fallback_store = {}
        self.use_fallback = False
        
        try:
            self.client = redis.Redis(
                host=host, 
                port=port, 
                db=db, 
                decode_responses=True,
                socket_connect_timeout=0.5,
                socket_timeout=0.5,
                retry=None
            )
            # Test connection
            self.client.ping()
            logger.info("Connected to Redis successfully for session storage.")
        except (redis.exceptions.ConnectionError, redis.exceptions.TimeoutError) as e:
            self.use_fallback = True
            logger.info(
                "Using local in-memory session cache (Redis not configured)."
            )
            self.client = None

    def get_session(self, call_id: str) -> Optional[SessionState]:
        if self.use_fallback:
            data = self.fallback_store.get(call_id)
            if data:
                return SessionState.model_validate_json(data)
            return None
        
        try:
            data = self.client.get(f"session:{call_id}")
            if data:
                return SessionState.model_validate_json(data)
            return None
        except Exception as e:
            logger.error(f"Error reading session from Redis: {e}. Falling back to in-memory.")
            data = self.fallback_store.get(call_id)
            if data:
                return SessionState.model_validate_json(data)
            return None

    def save_session(self, session: SessionState) -> None:
        call_id = session.call_id
        session_json = session.model_dump_json()
        
        # Always write to fallback store as a backup
        self.fallback_store[call_id] = session_json
        
        if not self.use_fallback:
            try:
                self.client.setex(
                    name=f"session:{call_id}",
                    time=self.ttl,
                    value=session_json
                )
            except Exception as e:
                logger.error(f"Error saving session to Redis: {e}. Saving to fallback store only.")
                # We do not switch to fallback mode permanently unless ping fails, 
                # but we ensure the fallback store acts as local backup.
