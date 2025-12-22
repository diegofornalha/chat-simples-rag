# =============================================================================
# PROMPT GUARD - Validacao contra prompt injection
# =============================================================================

import re
from dataclasses import dataclass
from typing import List, Tuple


@dataclass
class ValidationResult:
    """Resultado da validacao de prompt."""
    is_safe: bool
    risk_score: float  # 0.0 a 1.0
    violations: List[str]
    message: str = ""


class PromptGuard:
    """
    Detecta tentativas de prompt injection.

    Analisa o texto buscando padroes conhecidos de ataque
    e retorna um score de risco.
    """

    # Padroes suspeitos com seus scores de risco
    # Formato: (regex_pattern, risk_score, description)
    INJECTION_PATTERNS: List[Tuple[str, float, str]] = [
        # Tentativas de override de instrucoes
        (r"ignore\s+(previous|all|above|your)\s+instructions?", 0.95, "instruction_override"),
        (r"disregard\s+(your|the|all)\s+(instructions?|rules?)", 0.95, "instruction_override"),
        (r"forget\s+(everything|all|your\s+rules)", 0.90, "instruction_override"),
        (r"new\s+instructions?\s*:", 0.85, "instruction_override"),

        # Role-playing malicioso
        (r"you\s+are\s+now\s+", 0.80, "role_hijack"),
        (r"pretend\s+(you're|to\s+be|you\s+are)", 0.75, "role_hijack"),
        (r"act\s+as\s+(if|a|an)", 0.60, "role_hijack"),
        (r"from\s+now\s+on\s+you", 0.70, "role_hijack"),

        # Exfiltracao de sistema
        (r"(show|reveal|print|display)\s+(your|the)\s+(system|prompt|instructions)", 0.90, "exfiltration"),
        (r"what\s+(are|is)\s+your\s+(instructions?|rules?|prompt)", 0.50, "exfiltration"),
        (r"repeat\s+(your|the)\s+(system|initial)\s+(prompt|message)", 0.85, "exfiltration"),

        # Delimitadores de payload
        (r"```system", 0.95, "delimiter_injection"),
        (r"\[\[system\]\]", 0.95, "delimiter_injection"),
        (r"<\|im_start\|>", 0.98, "delimiter_injection"),
        (r"<\|endoftext\|>", 0.98, "delimiter_injection"),
        (r"###\s*system", 0.90, "delimiter_injection"),

        # Jailbreak comum
        (r"dan\s+mode", 0.90, "jailbreak"),
        (r"developer\s+mode", 0.85, "jailbreak"),
        (r"evil\s+mode", 0.90, "jailbreak"),
        (r"unrestricted\s+mode", 0.85, "jailbreak"),

        # Manipulacao de contexto
        (r"(end|close)\s+(of\s+)?(context|conversation|chat)", 0.80, "context_manipulation"),
        (r"---+\s*new\s+(prompt|conversation)", 0.85, "context_manipulation"),
    ]

    # Threshold para bloquear (0.7 = 70% de risco)
    BLOCK_THRESHOLD: float = 0.70

    # Threshold para avisar (0.4 = 40% de risco)
    WARN_THRESHOLD: float = 0.40

    def __init__(self, block_threshold: float = None, warn_threshold: float = None):
        if block_threshold is not None:
            self.BLOCK_THRESHOLD = block_threshold
        if warn_threshold is not None:
            self.WARN_THRESHOLD = warn_threshold

    def validate(self, prompt: str) -> ValidationResult:
        """
        Valida prompt contra padroes de injection.

        Args:
            prompt: Texto a validar

        Returns:
            ValidationResult com is_safe, risk_score e detalhes
        """
        if not prompt:
            return ValidationResult(is_safe=True, risk_score=0.0, violations=[])

        prompt_lower = prompt.lower()
        violations = []
        max_risk = 0.0

        for pattern, risk, category in self.INJECTION_PATTERNS:
            if re.search(pattern, prompt_lower, re.IGNORECASE):
                violations.append(f"{category}:{pattern[:30]}")
                max_risk = max(max_risk, risk)

        # Determinar resultado
        is_safe = max_risk < self.BLOCK_THRESHOLD

        if max_risk >= self.BLOCK_THRESHOLD:
            message = "Mensagem bloqueada por politica de seguranca"
        elif max_risk >= self.WARN_THRESHOLD:
            message = "Mensagem com risco moderado detectado"
        else:
            message = ""

        return ValidationResult(
            is_safe=is_safe,
            risk_score=round(max_risk, 3),
            violations=violations,
            message=message
        )

    def sanitize(self, prompt: str) -> str:
        """
        Remove ou neutraliza padroes suspeitos do prompt.

        Use com cautela - pode alterar o significado da mensagem.
        """
        sanitized = prompt

        # Remover delimitadores perigosos
        dangerous_delimiters = [
            r"<\|[^|]+\|>",  # Tokens especiais
            r"\[\[[^\]]+\]\]",  # Colchetes duplos
            r"```system[^`]*```",  # Code blocks system
        ]

        for pattern in dangerous_delimiters:
            sanitized = re.sub(pattern, "[REMOVED]", sanitized, flags=re.IGNORECASE)

        return sanitized


# Instancia global
prompt_guard = PromptGuard()


def validate_prompt(prompt: str) -> ValidationResult:
    """Funcao de conveniencia para validacao rapida."""
    return prompt_guard.validate(prompt)
