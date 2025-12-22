# =============================================================================
# MCP SERVER - RAG Tools para Desafio Atlantyx
# =============================================================================
# Ferramentas de busca semantica usando FastEmbed + sqlite-vec
# Com todas as integrações: logging, métricas, cache, hybrid search,
# reranking, circuit breaker, prompt guard, RBAC
# =============================================================================

import time
from mcp.server.fastmcp import FastMCP
from fastembed import TextEmbedding
import apsw
import sqlite_vec
from pathlib import Path
from typing import Optional

# Imports do core
import sys
sys.path.insert(0, str(Path(__file__).parent))

from core.logger import logger, set_request_id, set_conversation_id
from core.cache import get_embedding_cache, get_response_cache
from core.circuit_breaker import get_or_create_circuit_breaker, CircuitBreakerError
from core.prompt_guard import get_prompt_guard, ThreatLevel
from core.reranker import LightweightReranker
from api.metrics import get_metrics

# Caminho do banco de dados
DB_PATH = Path(__file__).parent.parent / "teste" / "documentos.db"

# Inicializar MCP Server
mcp = FastMCP("rag-tools")

# Modelo de embeddings (mesmo usado na indexacao)
model = TextEmbedding("BAAI/bge-small-en-v1.5")

# Coletor de métricas
metrics = get_metrics()

# Cache de embeddings
embedding_cache = get_embedding_cache()

# Cache de respostas
response_cache = get_response_cache()

# Circuit breaker para operações de DB
db_circuit = get_or_create_circuit_breaker("database", failure_threshold=3, timeout=30.0)

# Prompt guard
prompt_guard = get_prompt_guard(strict_mode=False)

# Reranker
reranker = LightweightReranker()


def get_connection():
    """Cria conexao com sqlite-vec carregado usando apsw."""
    conn = apsw.Connection(str(DB_PATH))
    conn.enableloadextension(True)
    conn.loadextension(sqlite_vec.loadable_path())
    conn.enableloadextension(False)
    return conn


def serialize_embedding(embedding: list) -> bytes:
    """Converte lista de floats para bytes usando sqlite_vec."""
    return sqlite_vec.serialize_float32(embedding)


def get_embedding_cached(text: str) -> list[float]:
    """Obtém embedding com cache."""
    cached = embedding_cache.get(text)
    if cached is not None:
        return cached

    embeddings = list(model.embed([text]))
    embedding = embeddings[0].tolist()
    embedding_cache.set(text, embedding)
    return embedding


@mcp.tool()
def search_documents(query: str, top_k: int = 5, use_reranking: bool = True) -> list:
    """
    Busca semantica nos documentos indexados.

    Args:
        query: Pergunta ou texto para buscar
        top_k: Numero de resultados (padrao 5)
        use_reranking: Aplicar re-ranking para melhor precisao (padrao True)

    Returns:
        Lista de documentos relevantes com source, content e score
    """
    request_id = set_request_id()
    start_time = time.perf_counter()

    try:
        # Verificar prompt injection
        scan_result = prompt_guard.scan(query)
        if not scan_result.is_safe:
            logger.warning(
                "prompt_blocked",
                query=query[:100],
                threat_level=scan_result.threat_level.value,
                threats=scan_result.threats_detected[:3],
            )
            metrics.record_error("PromptInjectionBlocked")
            return [{
                "error": "Query blocked by security filter",
                "threat_level": scan_result.threat_level.value,
            }]

        # Verificar cache de resposta
        cache_key = f"{query}:{top_k}:{use_reranking}"
        cached_response = response_cache.get(query, top_k)
        if cached_response:
            logger.info("cache_hit", query=query[:50])
            return cached_response

        # Usar circuit breaker para operação de DB
        def do_search():
            # Gerar embedding com cache
            embedding = get_embedding_cached(query)
            query_vec = serialize_embedding(embedding)

            conn = get_connection()
            cursor = conn.cursor()

            # Buscar mais resultados para re-ranking
            fetch_k = top_k * 2 if use_reranking else top_k

            results = []
            for row in cursor.execute("""
                SELECT v.doc_id, v.distance, d.nome, d.conteudo, d.tipo
                FROM vec_documentos v
                JOIN documentos d ON d.id = v.doc_id
                WHERE v.embedding MATCH ? AND k = ?
            """, (query_vec, fetch_k)):
                doc_id, distance, nome, conteudo, tipo = row
                similarity = max(0, 1 - distance)

                results.append({
                    "doc_id": doc_id,
                    "source": nome,
                    "type": tipo,
                    "content": conteudo[:1000] if conteudo else "",
                    "similarity": round(similarity, 3)
                })

            conn.close()
            return results

        # Executar com circuit breaker
        try:
            results = db_circuit.call(do_search)
        except CircuitBreakerError as e:
            logger.log_error("CircuitBreakerOpen", str(e))
            metrics.record_error("CircuitBreakerOpen")
            return [{"error": "Service temporarily unavailable", "retry_after": 30}]

        # Aplicar re-ranking se habilitado
        if use_reranking and len(results) > 0:
            docs_for_rerank = [
                (r["doc_id"], r["content"], r["similarity"], {"source": r["source"], "type": r["type"]})
                for r in results
            ]
            reranked = reranker.rerank(query, docs_for_rerank, top_k=top_k)

            results = [
                {
                    "doc_id": r.doc_id,
                    "source": r.metadata["source"],
                    "type": r.metadata["type"],
                    "content": r.content,
                    "similarity": r.original_score,
                    "rerank_score": r.rerank_score,
                    "rank": r.final_rank,
                }
                for r in reranked
            ]
        else:
            results = results[:top_k]

        # Calcular latencia
        latency_ms = (time.perf_counter() - start_time) * 1000

        # Registrar metricas
        metrics.record_query(latency_ms, len(results))

        # Log estruturado
        doc_ids = [r["doc_id"] for r in results]
        similarities = [r["similarity"] for r in results]
        logger.log_query(query, top_k, len(results), latency_ms)
        logger.log_retrieval(doc_ids, similarities, latency_ms)

        # Salvar em cache
        response_cache.set(query, top_k, results)

        return results

    except Exception as e:
        latency_ms = (time.perf_counter() - start_time) * 1000
        metrics.record_error(type(e).__name__)
        logger.log_error(type(e).__name__, str(e), query=query[:100])
        raise


@mcp.tool()
def search_hybrid(query: str, top_k: int = 5, vector_weight: float = 0.7) -> list:
    """
    Busca hibrida combinando BM25 (lexica) e vetorial.

    Args:
        query: Pergunta ou texto para buscar
        top_k: Numero de resultados (padrao 5)
        vector_weight: Peso da busca vetorial (0-1, padrao 0.7)

    Returns:
        Lista de documentos com scores hibridos
    """
    from core.hybrid_search import HybridSearch

    request_id = set_request_id()
    start_time = time.perf_counter()

    try:
        # Verificar prompt injection
        scan_result = prompt_guard.scan(query)
        if not scan_result.is_safe:
            metrics.record_error("PromptInjectionBlocked")
            return [{"error": "Query blocked by security filter"}]

        # Busca hibrida
        hybrid = HybridSearch(
            str(DB_PATH),
            vector_weight=vector_weight,
            bm25_weight=1 - vector_weight,
        )

        results = hybrid.search(query, top_k=top_k)

        # Converter para formato de resposta
        response = [
            {
                "doc_id": r.doc_id,
                "source": r.nome,
                "type": r.tipo,
                "content": r.content,
                "vector_score": r.vector_score,
                "bm25_score": r.bm25_score,
                "hybrid_score": r.hybrid_score,
                "rank": r.rank,
            }
            for r in results
        ]

        # Metricas e logs
        latency_ms = (time.perf_counter() - start_time) * 1000
        metrics.record_query(latency_ms, len(response))
        logger.log_query(query, top_k, len(response), latency_ms)

        return response

    except Exception as e:
        latency_ms = (time.perf_counter() - start_time) * 1000
        metrics.record_error(type(e).__name__)
        logger.log_error(type(e).__name__, str(e), query=query[:100])
        raise


@mcp.tool()
def get_document(doc_id: int) -> dict:
    """
    Recupera documento completo pelo ID.

    Args:
        doc_id: ID do documento

    Returns:
        Documento completo com todos os campos
    """
    def fetch_doc():
        conn = get_connection()
        cursor = conn.cursor()

        row = None
        for r in cursor.execute("""
            SELECT id, nome, tipo, conteudo, caminho, criado_em
            FROM documentos
            WHERE id = ?
        """, (doc_id,)):
            row = r
            break

        conn.close()
        return row

    try:
        row = db_circuit.call(fetch_doc)
    except CircuitBreakerError:
        return {"error": "Service temporarily unavailable"}

    if not row:
        return {"error": f"Documento {doc_id} nao encontrado"}

    return {
        "id": row[0],
        "nome": row[1],
        "tipo": row[2],
        "conteudo": row[3],
        "caminho": row[4],
        "criado_em": str(row[5]) if row[5] else None
    }


@mcp.tool()
def list_sources() -> list:
    """
    Lista todas as fontes/documentos disponiveis no banco.

    Returns:
        Lista de documentos com nome e tipo
    """
    def fetch_sources():
        conn = get_connection()
        cursor = conn.cursor()

        results = [
            {"id": r[0], "nome": r[1], "tipo": r[2], "tamanho": r[3]}
            for r in cursor.execute("""
                SELECT id, nome, tipo, LENGTH(conteudo) as tamanho
                FROM documentos
                ORDER BY nome
            """)
        ]

        conn.close()
        return results

    try:
        return db_circuit.call(fetch_sources)
    except CircuitBreakerError:
        return [{"error": "Service temporarily unavailable"}]


@mcp.tool()
def count_documents() -> dict:
    """
    Conta documentos e embeddings no banco.

    Returns:
        Estatisticas do banco
    """
    def count():
        conn = get_connection()
        cursor = conn.cursor()

        total_docs = 0
        for r in cursor.execute("SELECT COUNT(*) FROM documentos"):
            total_docs = r[0]

        total_embeddings = 0
        for r in cursor.execute("SELECT COUNT(*) FROM vec_documentos"):
            total_embeddings = r[0]

        conn.close()

        return {
            "total_documentos": total_docs,
            "total_embeddings": total_embeddings,
            "status": "ok" if total_docs == total_embeddings else "incompleto"
        }

    try:
        return db_circuit.call(count)
    except CircuitBreakerError:
        return {"error": "Service temporarily unavailable", "status": "unavailable"}


@mcp.tool()
def get_metrics_summary() -> dict:
    """
    Retorna metricas do sistema RAG.

    Returns:
        Estatisticas de uso, latencia, custos e erros
    """
    all_metrics = metrics.get_all_metrics()

    # Incluir stats de cache
    emb_cache_stats = embedding_cache.stats
    resp_cache_stats = response_cache.stats

    return {
        "uptime_seconds": all_metrics["uptime_seconds"],
        "queries": {
            "total": all_metrics["rag"]["queries_total"],
            "latency_avg_ms": all_metrics["rag"]["query_latency"]["avg"],
            "latency_p95_ms": all_metrics["rag"]["query_latency"]["p95"],
        },
        "cache": {
            "embedding": {
                "hits": emb_cache_stats.hits,
                "misses": emb_cache_stats.misses,
                "hit_rate": round(emb_cache_stats.hit_rate, 2),
            },
            "response": {
                "hits": resp_cache_stats.hits,
                "misses": resp_cache_stats.misses,
                "hit_rate": round(resp_cache_stats.hit_rate, 2),
            },
        },
        "circuit_breaker": {
            "state": db_circuit.state.value,
            "stats": {
                "total": db_circuit.stats.total_calls,
                "failures": db_circuit.stats.failed_calls,
                "rejected": db_circuit.stats.rejected_calls,
            },
        },
        "errors": all_metrics["rag"]["errors_by_type"],
    }


@mcp.tool()
def get_health() -> dict:
    """
    Retorna status de saude do sistema.

    Returns:
        Health check com status de todos os componentes
    """
    from api.health import HealthChecker

    checker = HealthChecker(str(DB_PATH))
    report = checker.check_health(include_details=False)

    return {
        "status": report.status.value,
        "uptime_seconds": round(report.uptime_seconds, 2),
        "components": [
            {
                "name": c.name,
                "status": c.status.value,
                "latency_ms": round(c.latency_ms, 2),
            }
            for c in report.components
        ],
    }


if __name__ == "__main__":
    mcp.run()
