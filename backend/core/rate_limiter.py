# =============================================================================
# RATE LIMITER - Limitacao de requisicoes por IP/usuario
# =============================================================================

import os
from typing import Callable
from fastapi import Request

# Usar slowapi se disponivel, senao fallback simples
try:
    from slowapi import Limiter
    from slowapi.util import get_remote_address
    from slowapi.errors import RateLimitExceeded
    SLOWAPI_AVAILABLE = True
except ImportError:
    SLOWAPI_AVAILABLE = False


def get_client_ip(request: Request) -> str:
    """Extrai IP do cliente da requisicao."""
    # Verificar headers de proxy primeiro
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()

    real_ip = request.headers.get("X-Real-IP")
    if real_ip:
        return real_ip

    # Fallback para IP direto
    if request.client:
        return request.client.host

    return "unknown"


# Configuracao de limites por endpoint
RATE_LIMITS = {
    "chat": os.getenv("RATE_LIMIT_CHAT", "10/minute"),
    "chat_stream": os.getenv("RATE_LIMIT_STREAM", "10/minute"),
    "sessions": os.getenv("RATE_LIMIT_SESSIONS", "30/minute"),
    "default": os.getenv("RATE_LIMIT_DEFAULT", "60/minute"),
}


if SLOWAPI_AVAILABLE:
    # Limiter com slowapi (recomendado)
    limiter = Limiter(
        key_func=get_client_ip,
        default_limits=[RATE_LIMITS["default"]],
        storage_uri=os.getenv("REDIS_URL"),  # None = in-memory
    )

    def get_limiter() -> Limiter:
        return limiter

else:
    # Fallback: implementacao simples in-memory
    from collections import defaultdict
    from datetime import datetime, timedelta
    from threading import Lock

    class SimpleLimiter:
        """Limitador simples sem dependencias externas."""

        def __init__(self):
            self.requests = defaultdict(list)  # {ip: [timestamps]}
            self._lock = Lock()

        def is_allowed(self, key: str, limit: int = 10, window: int = 60) -> bool:
            """Verifica se requisicao e permitida."""
            now = datetime.now()
            cutoff = now - timedelta(seconds=window)

            with self._lock:
                # Limpar requests antigas
                self.requests[key] = [
                    ts for ts in self.requests[key]
                    if ts > cutoff
                ]

                # Verificar limite
                if len(self.requests[key]) >= limit:
                    return False

                # Registrar nova request
                self.requests[key].append(now)
                return True

        def limit(self, limit_string: str) -> Callable:
            """Decorator compativel com slowapi."""
            # Parse "10/minute" -> (10, 60)
            count, period = limit_string.split("/")
            count = int(count)
            window = {"second": 1, "minute": 60, "hour": 3600}.get(period, 60)

            def decorator(func):
                async def wrapper(request: Request, *args, **kwargs):
                    key = get_client_ip(request)
                    if not self.is_allowed(key, count, window):
                        from fastapi import HTTPException
                        raise HTTPException(
                            status_code=429,
                            detail="Rate limit exceeded"
                        )
                    return await func(request, *args, **kwargs)
                return wrapper
            return decorator

    limiter = SimpleLimiter()

    def get_limiter():
        return limiter


# Excecao para rate limit (compativel com FastAPI)
class RateLimitError(Exception):
    """Erro de rate limit excedido."""
    def __init__(self, detail: str = "Rate limit exceeded"):
        self.detail = detail
