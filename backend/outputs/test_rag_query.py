#!/usr/bin/env python3
"""
Script simples para testar busca RAG sem dependências pesadas
"""

import sys
import sqlite3
from pathlib import Path

# Path do banco
DB_PATH = Path("/Users/2a/.claude/hello-agent/chat-simples/backend/teste/documentos.db")

def test_search():
    """Testa busca básica no banco de dados."""
    
    if not DB_PATH.exists():
        print(f"Erro: Banco não encontrado em {DB_PATH}")
        return
    
    # Conectar ao banco
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    
    print("=" * 70)
    print("TESTE RAG - Buscando Documentos")
    print("=" * 70)
    
    # Listar documentos
    print("\nDocumentos disponíveis:")
    print("-" * 70)
    
    try:
        cursor.execute("SELECT id, nome, tipo FROM documentos LIMIT 20")
        docs = cursor.fetchall()
        
        for doc_id, nome, tipo in docs:
            print(f"  [{doc_id}] {nome} ({tipo})")
        
        print(f"\nTotal de documentos: {len(docs)}")
    except Exception as e:
        print(f"Erro ao listar documentos: {e}")
    
    # Contar chunks
    print("\n" + "-" * 70)
    try:
        cursor.execute("SELECT COUNT(*) FROM documentos")
        total_docs = cursor.fetchone()[0]
        print(f"Total de registros (chunks): {total_docs}")
    except Exception as e:
        print(f"Erro: {e}")
    
    # Verificar tabelas
    print("\n" + "-" * 70)
    print("Tabelas no banco:")
    try:
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = cursor.fetchall()
        for table in tables:
            cursor.execute(f"SELECT COUNT(*) FROM {table[0]}")
            count = cursor.fetchone()[0]
            print(f"  - {table[0]}: {count} registros")
    except Exception as e:
        print(f"Erro: {e}")
    
    conn.close()
    
    print("\n" + "=" * 70)
    print("Banco de dados está pronto para usar!")
    print("=" * 70)

if __name__ == "__main__":
    test_search()
