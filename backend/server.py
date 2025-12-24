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
import asyncio
import shutil

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

# Importa funções de logger do RAG agent para session tracking
rag_logger_path = Path(__file__).parent / "rag-agent" / "core" / "logger.py"
spec_logger = importlib.util.spec_from_file_location("rag_logger", rag_logger_path)
rag_logger = importlib.util.module_from_spec(spec_logger)
spec_logger.loader.exec_module(rag_logger)
set_session_id = rag_logger.set_session_id
get_session_id = rag_logger.get_session_id

SESSIONS_DIR = Path.home() / ".claude" / "projects" / "-Users-2a--claude-hello-agent-chat-simples-backend-rag-agent"
RAG_OUTPUTS_DIR = Path(__file__).parent / "rag-agent" / "outputs"

client: ClaudeSDKClient | None = None


def extract_session_id_from_jsonl() -> str:
    """Extrai session_id do arquivo JSONL mais recente."""
    if not SESSIONS_DIR.exists():
        return "default"

    # Pegar JSONL mais recente (por mtime)
    jsonl_files = sorted(
        SESSIONS_DIR.glob("*.jsonl"),
        key=lambda f: f.stat().st_mtime,
        reverse=True
    )

    if not jsonl_files:
        return "default"

    latest_jsonl = jsonl_files[0]

    # Ler primeira linha para extrair sessionId
    try:
        with open(latest_jsonl, 'r') as f:
            first_line = f.readline().strip()
            if first_line:
                data = json.loads(first_line)
                session_id = data.get("sessionId", latest_jsonl.stem)
                return session_id
    except Exception as e:
        print(f"[WARN] Não foi possível extrair sessionId: {e}")
        return latest_jsonl.stem  # Fallback: usar nome do arquivo

    return "default"


async def get_client() -> ClaudeSDKClient:
    """Retorna o cliente, criando se necessário."""
    global client
    if client is None:
        # Criar cliente único (evita mismatch de session_id)
        client = ClaudeSDKClient(options=RAG_AGENT_OPTIONS)
        try:
            await client.__aenter__()
            print("🔗 Nova sessão criada!")

            # Aguardar SDK escrever primeira linha do JSONL
            await asyncio.sleep(0.2)

            # Extrair session_id do cliente ativo
            session_id = extract_session_id_from_jsonl()
            set_session_id(session_id)
            print(f"📁 Session ID: {session_id}")

            # Criar pasta da sessão para outputs
            session_output_dir = RAG_OUTPUTS_DIR / session_id
            session_output_dir.mkdir(parents=True, exist_ok=True)
            print(f"📂 Pasta da sessão criada: {session_output_dir}")

            # Inicializar AgentFS para a sessão
            from core.agentfs_manager import init_agentfs
            await init_agentfs(session_id)
            print(f"🗄️  AgentFS inicializado: ~/.claude/.agentfs/{session_id}.db")

        except Exception as e:
            # Cleanup em caso de erro durante inicialização
            try:
                await client.__aexit__(None, None, None)
            except Exception:
                pass
            client = None
            raise e

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

    # Fechar AgentFS
    from core.agentfs_manager import close_agentfs
    await close_agentfs()
    print("🗄️  AgentFS fechado")

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


@app.get("/session/current")
async def get_current_session():
    """Retorna informações da sessão atual."""
    global client

    if client is None:
        return {
            "active": False,
            "session_id": None,
            "message": "Nenhuma sessão ativa"
        }

    session_id = get_session_id()
    session_file = SESSIONS_DIR / f"{session_id}.jsonl"

    # Contar mensagens
    message_count = 0
    if session_file.exists():
        message_count = len(session_file.read_text().strip().split('\n'))

    # Verificar outputs no rag-agent
    session_output_dir = RAG_OUTPUTS_DIR / session_id
    has_outputs = session_output_dir.exists()
    output_count = len(list(session_output_dir.iterdir())) if has_outputs else 0

    return {
        "active": True,
        "session_id": session_id,
        "message_count": message_count,
        "has_outputs": has_outputs,
        "output_count": output_count,
        "output_dir": str(session_output_dir) if has_outputs else None
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
    old_session_id = get_session_id()
    await reset_client()

    # Aguardar nova sessão ser criada
    await asyncio.sleep(0.1)
    new_session_id = extract_session_id_from_jsonl()
    set_session_id(new_session_id)

    return {
        "status": "ok",
        "message": "Nova sessão iniciada!",
        "old_session_id": old_session_id,
        "new_session_id": new_session_id
    }


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

            # Extrair sessionId da primeira linha
            session_id = file.stem  # Fallback
            try:
                first_data = json.loads(lines[0])
                session_id = first_data.get("sessionId", session_id)
            except:
                pass

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

            # Verificar se há outputs para esta sessão
            session_output_dir = RAG_OUTPUTS_DIR / session_id
            has_outputs = session_output_dir.exists()
            output_count = len(list(session_output_dir.iterdir())) if has_outputs else 0

            sessions.append({
                "session_id": session_id,
                "file_name": file.name,
                "file": str(file),
                "message_count": message_count,
                "model": model,
                "updated_at": file.stat().st_mtime * 1000,
                "has_outputs": has_outputs,
                "output_count": output_count
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


@app.get("/rag-outputs/{session_id}")
async def list_rag_outputs_by_session(session_id: str):
    """Lista arquivos de uma sessão específica do RAG agent."""
    session_dir = RAG_OUTPUTS_DIR / session_id

    if not session_dir.exists():
        return {"session_id": session_id, "files": [], "count": 0}

    files = []
    for file in session_dir.iterdir():
        if file.is_file():
            stat = file.stat()
            files.append({
                "name": file.name,
                "path": str(file.relative_to(RAG_OUTPUTS_DIR)),
                "size": stat.st_size,
                "modified": stat.st_mtime * 1000
            })

    files.sort(key=lambda f: f["modified"], reverse=True)
    return {"session_id": session_id, "files": files, "count": len(files)}


@app.get("/sessions/{session_id}/rag-outputs")
async def get_session_rag_outputs_detailed(session_id: str):
    """Retorna informação detalhada dos outputs RAG de uma sessão."""
    session_dir = RAG_OUTPUTS_DIR / session_id

    if not session_dir.exists():
        return {
            "session_id": session_id,
            "exists": False,
            "files": [],
            "total_size": 0,
            "count": 0
        }

    files = []
    total_size = 0

    for file in session_dir.iterdir():
        if file.is_file():
            stat = file.stat()
            total_size += stat.st_size

            # Ler primeiras linhas para preview
            preview = ""
            try:
                with open(file, 'r', encoding='utf-8') as f:
                    preview = f.read(200)
            except:
                preview = "[binary file]"

            files.append({
                "name": file.name,
                "path": str(file.relative_to(RAG_OUTPUTS_DIR)),
                "size": stat.st_size,
                "modified": stat.st_mtime * 1000,
                "preview": preview
            })

    files.sort(key=lambda f: f["modified"], reverse=True)

    return {
        "session_id": session_id,
        "exists": True,
        "files": files,
        "total_size": total_size,
        "count": len(files)
    }


@app.delete("/sessions/{session_id}/rag-outputs")
async def delete_session_rag_outputs(session_id: str, api_key: str = Depends(verify_api_key)):
    """Deleta todos os outputs RAG de uma sessão."""
    session_dir = RAG_OUTPUTS_DIR / session_id

    if not session_dir.exists():
        return {"success": False, "error": "Sessão não encontrada"}

    try:
        shutil.rmtree(session_dir)
        return {"success": True, "message": f"Outputs da sessão {session_id} deletados"}
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
