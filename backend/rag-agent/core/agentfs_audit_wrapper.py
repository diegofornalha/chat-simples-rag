"""
=============================================================================
AGENTFS AUDIT WRAPPER - Decorator para Auditoria Automática de Tool Calls
=============================================================================
Wrapper que registra automaticamente tool calls no AgentFS
=============================================================================
"""

import time
import json
import functools
import asyncio
from typing import Any, Callable
from .agentfs_manager import get_agentfs, is_initialized
from .logger import logger


def audit_tool_call(tool_name: str):
    """
    Decorator que registra tool call no AgentFS automaticamente.

    Args:
        tool_name: Nome da ferramenta a ser registrada

    Usage:
        @audit_tool_call("search_documents")
        async def search_documents(query: str):
            # ... código da tool ...
            return results
    """

    def decorator(func: Callable):
        # Para funções assíncronas
        if asyncio.iscoroutinefunction(func):

            @functools.wraps(func)
            async def async_wrapper(*args, **kwargs):
                # Se AgentFS não está inicializado, apenas executa a função
                if not is_initialized():
                    logger.debug(f"AgentFS não inicializado, pulando auditoria de {tool_name}")
                    return await func(*args, **kwargs)

                agentfs = get_agentfs()
                start_time = int(time.time())  # Unix timestamp em segundos

                # Captura parâmetros (simplificado - não serializa objetos complexos)
                parameters = {
                    "args": [str(arg) for arg in args] if args else [],
                    "kwargs": {k: str(v) for k, v in kwargs.items()} if kwargs else {}
                }

                try:
                    # Executa tool
                    result = await func(*args, **kwargs)

                    # Registra sucesso
                    end_time = int(time.time())

                    # Serializa result de forma segura
                    result_serialized = None
                    try:
                        if isinstance(result, (dict, list, str, int, float, bool)):
                            result_serialized = result
                        else:
                            result_serialized = str(result)
                    except:
                        result_serialized = "[not serializable]"

                    await agentfs.tools.record(
                        name=tool_name,
                        started_at=start_time,
                        completed_at=end_time,
                        parameters=parameters,
                        result=result_serialized
                    )

                    # Log estruturado (backward compatibility)
                    duration_ms = (end_time - start_time) * 1000
                    logger.info(
                        f"Tool call: {tool_name}",
                        tool_name=tool_name,
                        duration_ms=duration_ms,
                        status="success"
                    )

                    return result

                except Exception as e:
                    # Registra erro
                    end_time = int(time.time())

                    await agentfs.tools.record(
                        name=tool_name,
                        started_at=start_time,
                        completed_at=end_time,
                        parameters=parameters,
                        error=str(e)
                    )

                    duration_ms = (end_time - start_time) * 1000
                    logger.error(
                        f"Tool call failed: {tool_name}",
                        tool_name=tool_name,
                        error=str(e),
                        duration_ms=duration_ms
                    )

                    raise

            return async_wrapper

        # Para funções síncronas (não usamos no MCP, mas por completude)
        else:

            @functools.wraps(func)
            def sync_wrapper(*args, **kwargs):
                # Similar ao async mas sem await
                if not is_initialized():
                    logger.debug(f"AgentFS não inicializado, pulando auditoria de {tool_name}")
                    return func(*args, **kwargs)

                # Implementação síncrona seria similar
                # Por enquanto, apenas executa sem auditoria
                logger.warning(f"Tool síncrono {tool_name} não pode ser auditado (AgentFS é async)")
                return func(*args, **kwargs)

            return sync_wrapper

    return decorator
