# =============================================================================
# SECURITY - Configuracoes de seguranca (CORS, headers, etc)
# =============================================================================

import os
from typing import List

def get_allowed_origins() -> List[str]:
    """
    Retorna origens permitidas baseado no ambiente.

    Em producao: apenas URLs configuradas via env
    Em desenvolvimento: localhost padrao
    """
    env = os.getenv("ENVIRONMENT", "development")

    if env == "production":
        # Em producao, usar apenas origens configuradas
        frontend_url = os.getenv("FRONTEND_URL")
        if frontend_url:
            return [frontend_url]
        return []  # Nenhuma origem permitida se nao configurado

    # Desenvolvimento - origens locais permitidas
    return [
        "http://localhost:3000",
        "http://localhost:8080",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:8080",
    ]


def get_allowed_methods() -> List[str]:
    """Metodos HTTP permitidos."""
    return ["GET", "POST", "DELETE", "OPTIONS"]


def get_allowed_headers() -> List[str]:
    """Headers permitidos nas requisicoes."""
    return [
        "Authorization",
        "Content-Type",
        "X-API-Key",
        "X-Request-ID",
    ]


# Configuracoes de seguranca
SECURITY_CONFIG = {
    "cors": {
        "allow_origins": get_allowed_origins,  # Funcao para avaliacao lazy
        "allow_credentials": True,
        "allow_methods": get_allowed_methods(),
        "allow_headers": get_allowed_headers(),
    },
    "headers": {
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "X-XSS-Protection": "1; mode=block",
    }
}
