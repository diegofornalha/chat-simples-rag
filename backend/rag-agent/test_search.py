#!/usr/bin/env python3
# =============================================================================
# TESTE DE BUSCA SEMANTICA
# =============================================================================

from fastembed import TextEmbedding
import apsw
import sqlite_vec
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "teste" / "documentos.db"

# Carregar modelo uma vez
print("Carregando modelo de embeddings...")
model = TextEmbedding("BAAI/bge-small-en-v1.5")
print("Modelo carregado!")

def serialize_embedding(embedding: list) -> bytes:
    return sqlite_vec.serialize_float32(embedding)

def test_search(query: str, top_k: int = 3):
    print(f"\n{'='*60}")
    print(f"Query: {query}")
    print(f"{'='*60}")

    # Gerar embedding
    embeddings = list(model.embed([query]))
    query_vec = serialize_embedding(embeddings[0].tolist())

    # Conectar banco com apsw
    conn = apsw.Connection(str(DB_PATH))
    conn.enableloadextension(True)
    conn.loadextension(sqlite_vec.loadable_path())
    conn.enableloadextension(False)
    cursor = conn.cursor()

    # Buscar
    print(f"\nTop {top_k} resultados:")
    print("-" * 60)

    for row in cursor.execute("""
        SELECT v.doc_id, v.distance, d.nome, d.tipo
        FROM vec_documentos v
        JOIN documentos d ON d.id = v.doc_id
        WHERE v.embedding MATCH ? AND k = ?
    """, (query_vec, top_k)):
        doc_id, distance, nome, tipo = row
        similarity = max(0, 1 - distance)
        print(f"  [{similarity:.3f}] {nome} ({tipo})")

    conn.close()


if __name__ == "__main__":
    # Testar com perguntas do desafio
    queries = [
        "principios obrigatorios da politica de uso de IA",
        "componentes obrigatorios arquitetura RAG enterprise",
        "politica de retencao de logs",
        "metricas minimas para assistente IA em producao",
        "mitigar prompt injection em RAG",
    ]

    for q in queries:
        test_search(q)
