# =============================================================================
# MCP SERVER - RAG Tools para Desafio Atlantyx
# =============================================================================
# Ferramentas de busca semantica usando FastEmbed + sqlite-vec
# Com logging estruturado e metricas
# =============================================================================

import time
from mcp.server.fastmcp import FastMCP
from fastembed import TextEmbedding
import apsw
import sqlite_vec
from pathlib import Path

# Imports do core (logging e metricas)
import sys
sys.path.insert(0, str(Path(__file__).parent))
from core.logger import logger, set_request_id
from api.metrics import get_metrics, Timer

# Caminho do banco de dados
DB_PATH = Path(__file__).parent.parent / "teste" / "documentos.db"

# Inicializar MCP Server
mcp = FastMCP("rag-tools")

# Modelo de embeddings (mesmo usado na indexacao)
model = TextEmbedding("BAAI/bge-small-en-v1.5")

# Coletor de métricas
metrics = get_metrics()


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


@mcp.tool()
def search_documents(query: str, top_k: int = 5) -> list:
    """
    Busca semantica nos documentos indexados.

    Args:
        query: Pergunta ou texto para buscar
        top_k: Numero de resultados (padrao 5)

    Returns:
        Lista de documentos relevantes com source, content e score
    """
    request_id = set_request_id()
    start_time = time.perf_counter()

    try:
        # Gerar embedding da query
        embeddings = list(model.embed([query]))
        query_vec = serialize_embedding(embeddings[0].tolist())

        conn = get_connection()
        cursor = conn.cursor()

        # Buscar no indice vetorial (sqlite-vec usa k = ? para limit)
        results = []
        similarities = []
        doc_ids = []

        for row in cursor.execute("""
            SELECT v.doc_id, v.distance, d.nome, d.conteudo, d.tipo
            FROM vec_documentos v
            JOIN documentos d ON d.id = v.doc_id
            WHERE v.embedding MATCH ? AND k = ?
        """, (query_vec, top_k)):
            doc_id, distance, nome, conteudo, tipo = row
            # Converter distancia para similaridade (0-1)
            similarity = max(0, 1 - distance)

            results.append({
                "doc_id": doc_id,
                "source": nome,
                "type": tipo,
                "content": conteudo[:1000] if conteudo else "",  # Truncar
                "similarity": round(similarity, 3)
            })
            similarities.append(similarity)
            doc_ids.append(doc_id)

        conn.close()

        # Calcular latencia
        latency_ms = (time.perf_counter() - start_time) * 1000

        # Registrar metricas
        metrics.record_query(latency_ms, len(results))

        # Log estruturado
        logger.log_query(query, top_k, len(results), latency_ms)
        logger.log_retrieval(doc_ids, similarities, latency_ms)

        return results

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


@mcp.tool()
def count_documents() -> dict:
    """
    Conta documentos e embeddings no banco.

    Returns:
        Estatisticas do banco
    """
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


@mcp.tool()
def get_metrics_summary() -> dict:
    """
    Retorna metricas do sistema RAG.

    Returns:
        Estatisticas de uso, latencia, custos e erros
    """
    all_metrics = metrics.get_all_metrics()

    # Resumo simplificado
    return {
        "uptime_seconds": all_metrics["uptime_seconds"],
        "queries": {
            "total": all_metrics["rag"]["queries_total"],
            "latency_avg_ms": all_metrics["rag"]["query_latency"]["avg"],
            "latency_p95_ms": all_metrics["rag"]["query_latency"]["p95"],
        },
        "llm": {
            "latency_avg_ms": all_metrics["rag"]["llm_latency"]["avg"],
            "tokens_input": all_metrics["rag"]["tokens"]["input"],
            "tokens_output": all_metrics["rag"]["tokens"]["output"],
            "total_cost_usd": round(all_metrics["rag"]["total_cost_usd"], 4),
        },
        "errors": all_metrics["rag"]["errors_by_type"],
        "rbac": all_metrics["rbac"],
    }


if __name__ == "__main__":
    mcp.run()
