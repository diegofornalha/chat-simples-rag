# =============================================================================
# CORE - Modulos de seguranca e infraestrutura
# =============================================================================

from .security import get_allowed_origins, get_allowed_methods, get_allowed_headers, SECURITY_CONFIG
from .rate_limiter import get_limiter, RATE_LIMITS, get_client_ip
from .prompt_guard import PromptGuard, validate_prompt, ValidationResult
from .auth import verify_api_key, is_auth_enabled, generate_api_key

__all__ = [
    # Security
    "get_allowed_origins",
    "get_allowed_methods",
    "get_allowed_headers",
    "SECURITY_CONFIG",
    # Rate Limiter
    "get_limiter",
    "RATE_LIMITS",
    "get_client_ip",
    # Prompt Guard
    "PromptGuard",
    "validate_prompt",
    "ValidationResult",
    # Auth
    "verify_api_key",
    "is_auth_enabled",
    "generate_api_key",
]
