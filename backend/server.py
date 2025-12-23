from fastapi import FastAPI, Request, Depends, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from contextlib import asynccontextmanager
from pathlib import Path
import json
import os
import sys
import importlib.util

from claude_agent_sdk import ClaudeSDKClient, AssistantMessage, TextBlock, ProcessError

# Importa config do RAG Agent
rag_config_path = Path(__file__).parent / "rag-agent" / "config.py"
spec = importlib.util.spec_from_file_location("rag_config", rag_config_path)
rag_config = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rag_config)
RAG_AGENT_OPTIONS = rag_config.RAG_AGENT_OPTIONS

# Importa modulos de seguranca
sys.path.insert(0, str(Path(__file__).parent))
from core.security import get_allowed_origins, get_allowed_methods, get_allowed_headers, SECURITY_CONFIG
from core.rate_limiter import get_limiter, RATE_LIMITS, get_client_ip, SLOWAPI_AVAILABLE
from core.prompt_guard import validate_prompt
from core.auth import verify_api_key, is_auth_enabled

SESSIONS_DIR = Path.home() / ".claude" / "projects" / "-Users-2a--claude-hello-agent-chat-simples-backend"

client: ClaudeSDKClient | None = None

async def get_client() -> ClaudeSDKClient:
    """Retorna o cliente, criando se necessário."""
    global client
    if client is None:
        client = ClaudeSDKClient(options=RAG_AGENT_OPTIONS)
        await client.__aenter__()
        print("🔗 Nova sessão criada!")
    return client

async def reset_client():
    """Reseta o cliente (nova sessão)."""
    global client
    if client is not None:
        await client.__aexit__(None, None, None)
        client = None
    return await get_client()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Gerencia ciclo de vida do app."""
    print("🚀 Iniciando Chat Simples...")
    yield
    # Cleanup ao desligar
    global client
    if client is not None:
        await client.__aexit__(None, None, None)
        print("👋 Sessão encerrada!")

app = FastAPI(
    title="Chat Simples",
    description="Backend com sessão persistente - Claude Agent SDK",
    version="2.0.0",
    lifespan=lifespan
)

# CORS restritivo (Debito #1)
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_allowed_origins(),
    allow_credentials=True,
    allow_methods=get_allowed_methods(),
    allow_headers=get_allowed_headers(),
)

# Rate limiter (Debito #2)
limiter = get_limiter()
if SLOWAPI_AVAILABLE:
    from slowapi import _rate_limit_exceeded_handler
    from slowapi.errors import RateLimitExceeded
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


class ChatRequest(BaseModel):
    message: str

class ChatResponse(BaseModel):
    response: str

@app.get("/")
async def root():
    """Health check."""
    global client
    env = os.getenv("ENVIRONMENT", "development")
    response = {
        "status": "ok",
        "session_active": client is not None,
        "message": "Chat Simples v2 - Sessão Persistente",
        "auth_enabled": is_auth_enabled()
    }
    # Em dev, expor a API key
    if env != "production" and is_auth_enabled():
        from core.auth import VALID_API_KEYS
        if VALID_API_KEYS:
            response["dev_key"] = list(VALID_API_KEYS)[0]
    return response

@app.get("/health")
async def health_check():
    """Health check detalhado com status de segurança."""
    global client
    env = os.getenv("ENVIRONMENT", "development")
    return {
        "status": "healthy",
        "environment": env,
        "session_active": client is not None,
        "security": {
            "auth_enabled": is_auth_enabled(),
            "cors_origins": len(get_allowed_origins()),
            "rate_limiter": "slowapi" if SLOWAPI_AVAILABLE else "simple",
            "prompt_guard": "active"
        }
    }

@app.post("/chat", response_model=ChatResponse)
@limiter.limit(RATE_LIMITS["chat"])
async def chat(
    request: Request,
    chat_request: ChatRequest,
    api_key: str = Depends(verify_api_key)
):
    """Chat com sessão persistente."""
    # Validacao anti-injection (Debito #3)
    validation = validate_prompt(chat_request.message)
    if not validation.is_safe:
        raise HTTPException(
            status_code=400,
            detail=f"Mensagem bloqueada: {validation.message}"
        )

    try:
        c = await get_client()

        # Envia mensagem
        await c.query(chat_request.message)

        # Coleta resposta
        response_text = ""
        async for message in c.receive_response():
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        response_text += block.text

        return ChatResponse(response=response_text)

    except ProcessError as e:
        raise HTTPException(status_code=503, detail=f"Erro ao processar com Claude: {str(e)}")
    except Exception as e:
        print(f"[ERROR] Chat error: {e}")
        raise HTTPException(status_code=500, detail="Erro interno do servidor")

@app.post("/chat/stream")
@limiter.limit(RATE_LIMITS["chat_stream"])
async def chat_stream(
    request: Request,
    chat_request: ChatRequest,
    api_key: str = Depends(verify_api_key)
):
    """Chat com streaming e sessão persistente."""
    # Validacao anti-injection (Debito #3)
    validation = validate_prompt(chat_request.message)
    if not validation.is_safe:
        raise HTTPException(
            status_code=400,
            detail=f"Mensagem bloqueada: {validation.message}"
        )

    try:
        c = await get_client()

        async def generate():
            await c.query(chat_request.message)
            async for message in c.receive_response():
                if isinstance(message, AssistantMessage):
                    for block in message.content:
                        if isinstance(block, TextBlock):
                            yield f"data: {block.text}\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(generate(), media_type="text/event-stream")

    except ProcessError as e:
        raise HTTPException(status_code=503, detail=f"Erro ao processar com Claude: {str(e)}")
    except Exception as e:
        print(f"[ERROR] Stream error: {e}")
        raise HTTPException(status_code=500, detail="Erro interno do servidor")

@app.post("/reset")
@limiter.limit("5/minute")
async def reset_session(request: Request, api_key: str = Depends(verify_api_key)):
    """Inicia nova sessão (novo JSONL)."""
    await reset_client()
    return {"status": "ok", "message": "Nova sessão iniciada!"}


@app.get("/sessions")
async def list_sessions():
    """Lista todas as sessões disponíveis."""
    sessions = []

    if not SESSIONS_DIR.exists():
        return {"count": 0, "sessions": []}

    for file in sorted(SESSIONS_DIR.glob("*.jsonl"), key=lambda f: f.stat().st_mtime, reverse=True):
        try:
            lines = file.read_text().strip().split('\n')
            message_count = len(lines)

            # Tentar extrair modelo da primeira mensagem
            model = "unknown"
            for line in lines[:5]:
                try:
                    data = json.loads(line)
                    if "message" in data and "model" in data.get("message", {}):
                        model = data["message"]["model"]
                        break
                except:
                    pass

            sessions.append({
                "session_id": file.stem,
                "file_name": file.name,
                "file": str(file),
                "message_count": message_count,
                "model": model,
                "updated_at": file.stat().st_mtime * 1000
            })
        except Exception as e:
            print(f"Erro ao ler {file}: {e}")

    return {"count": len(sessions), "sessions": sessions}

@app.get("/sessions/{session_id}")
async def get_session(session_id: str):
    """Retorna mensagens de uma sessão."""
    file_path = SESSIONS_DIR / f"{session_id}.jsonl"

    if not file_path.exists():
        return {"error": "Sessão não encontrada"}

    messages = []
    for line in file_path.read_text().strip().split('\n'):
        try:
            messages.append(json.loads(line))
        except:
            pass

    return {"count": len(messages), "messages": messages}

@app.delete("/sessions/{session_id}")
async def delete_session(session_id: str, api_key: str = Depends(verify_api_key)):
    """Deleta uma sessão."""
    file_path = SESSIONS_DIR / f"{session_id}.jsonl"

    if not file_path.exists():
        return {"success": False, "error": "Sessão não encontrada"}

    try:
        file_path.unlink()
        return {"success": True}
    except Exception as e:
        return {"success": False, "error": str(e)}

OUTPUTS_DIR = Path(__file__).parent / "outputs"

@app.get("/outputs")
async def list_outputs():
    """Lista arquivos da pasta outputs."""
    if not OUTPUTS_DIR.exists():
        return {"files": []}

    files = []
    for file in OUTPUTS_DIR.iterdir():
        if file.is_file() and file.name != "index.html":
            stat = file.stat()
            files.append({
                "name": file.name,
                "size": stat.st_size,
                "modified": stat.st_mtime * 1000
            })

    files.sort(key=lambda f: f["modified"], reverse=True)
    return {"files": files}

@app.delete("/outputs/{filename}")
async def delete_output(filename: str, api_key: str = Depends(verify_api_key)):
    """Deleta um arquivo da pasta outputs."""
    file_path = OUTPUTS_DIR / filename

    if not file_path.exists():
        return {"success": False, "error": "Arquivo nao encontrado"}

    if file_path.name == "index.html":
        return {"success": False, "error": "Nao pode deletar index.html"}

    try:
        file_path.unlink()
        return {"success": True}
    except Exception as e:
        return {"success": False, "error": str(e)}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
