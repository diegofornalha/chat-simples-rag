# =============================================================================
# AUTH - Autenticacao via API Key
# =============================================================================

import os
import secrets
from typing import Optional, Set
from fastapi import HTTPException, Security, Request, Depends
from fastapi.security import APIKeyHeader

# Header para API Key
API_KEY_HEADER = APIKeyHeader(name="X-API-Key", auto_error=False)


def load_api_keys() -> Set[str]:
    """
    Carrega chaves de API validas.

    Em producao: do ambiente (API_KEYS separadas por virgula)
    Em desenvolvimento: gera uma chave temporaria
    """
    keys_str = os.getenv("API_KEYS", "")

    if keys_str:
        # Chaves do ambiente
        return set(k.strip() for k in keys_str.split(",") if k.strip())

    # Desenvolvimento: gerar chave temporaria
    env = os.getenv("ENVIRONMENT", "development")
    if env != "production":
        dev_key = os.getenv("DEV_API_KEY") or secrets.token_urlsafe(32)
        print(f"[DEV] API Key temporaria: {dev_key}")
        return {dev_key}

    # Producao sem chaves = erro
    return set()


# Cache de chaves validas
VALID_API_KEYS: Set[str] = load_api_keys()


def is_auth_enabled() -> bool:
    """Verifica se autenticacao esta habilitada."""
    env = os.getenv("ENVIRONMENT", "development")
    auth_enabled = os.getenv("AUTH_ENABLED", "true").lower()

    # Em producao, sempre habilitado
    if env == "production":
        return True

    # Em dev, pode ser desabilitado
    return auth_enabled == "true"


# Endpoints que nao precisam de autenticacao
PUBLIC_ENDPOINTS = {
    "/",
    "/health",
    "/docs",
    "/redoc",
    "/openapi.json",
}


async def verify_api_key(
    request: Request,
    api_key: Optional[str] = Security(API_KEY_HEADER)
) -> Optional[str]:
    """
    Middleware de autenticacao via API Key.

    Args:
        request: Request do FastAPI
        api_key: Chave do header X-API-Key

    Returns:
        API Key validada ou None para endpoints publicos

    Raises:
        HTTPException 401: Se chave nao fornecida
        HTTPException 403: Se chave invalida
    """
    # Verificar se auth esta habilitada
    if not is_auth_enabled():
        return "auth_disabled"

    # Endpoints publicos
    if request.url.path in PUBLIC_ENDPOINTS:
        return None

    # Verificar se chave foi fornecida
    if not api_key:
        raise HTTPException(
            status_code=401,
            detail="API Key obrigatoria. Use header X-API-Key.",
            headers={"WWW-Authenticate": "ApiKey"}
        )

    # Verificar se chave e valida
    if api_key not in VALID_API_KEYS:
        raise HTTPException(
            status_code=403,
            detail="API Key invalida"
        )

    return api_key


def generate_api_key() -> str:
    """Gera uma nova API Key segura."""
    return secrets.token_urlsafe(32)


def add_api_key(key: str) -> bool:
    """Adiciona uma nova API Key (runtime only)."""
    if key and len(key) >= 16:
        VALID_API_KEYS.add(key)
        return True
    return False


def revoke_api_key(key: str) -> bool:
    """Remove uma API Key."""
    if key in VALID_API_KEYS:
        VALID_API_KEYS.discard(key)
        return True
    return False


# Dependencia para uso nos endpoints
def require_auth():
    """Dependencia que requer autenticacao."""
    return Depends(verify_api_key)
