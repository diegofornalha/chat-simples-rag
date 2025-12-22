#!/usr/bin/env python3
# =============================================================================
# TESTE COMPLETO DO RAG AGENT
# =============================================================================
# Valida todas as partes do sistema:
# 1. Conexao com banco de dados
# 2. Extensao sqlite-vec
# 3. Modelo FastEmbed
# 4. Busca semantica
# 5. MCP Server tools
# 6. Configuracao do agente
# =============================================================================

import sys
from pathlib import Path

# Cores para output
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
RESET = "\033[0m"

def ok(msg):
    print(f"{GREEN}[OK]{RESET} {msg}")

def fail(msg):
    print(f"{RED}[FAIL]{RESET} {msg}")

def warn(msg):
    print(f"{YELLOW}[WARN]{RESET} {msg}")

def test_imports():
    """Testa se todas as dependencias estao instaladas."""
    print("\n=== Teste de Imports ===")

    deps = [
        ("apsw", "apsw"),
        ("sqlite_vec", "sqlite-vec"),
        ("fastembed", "fastembed"),
        ("mcp.server.fastmcp", "mcp"),
    ]

    all_ok = True
    for module, package in deps:
        try:
            __import__(module)
            ok(f"{module}")
        except ImportError:
            fail(f"{module} - instale com: pip install {package}")
            all_ok = False

    return all_ok


def test_database():
    """Testa conexao com o banco de dados."""
    print("\n=== Teste de Banco de Dados ===")

    import apsw
    import sqlite_vec

    db_path = Path(__file__).parent.parent / "teste" / "documentos.db"

    if not db_path.exists():
        fail(f"Banco nao encontrado: {db_path}")
        return False

    ok(f"Banco existe: {db_path}")

    try:
        conn = apsw.Connection(str(db_path))
        conn.enableloadextension(True)
        conn.loadextension(sqlite_vec.loadable_path())
        conn.enableloadextension(False)
        ok("sqlite-vec carregado")
    except Exception as e:
        fail(f"Erro ao carregar sqlite-vec: {e}")
        return False

    cursor = conn.cursor()

    # Verificar tabelas
    tables = []
    for row in cursor.execute("SELECT name FROM sqlite_master WHERE type='table'"):
        tables.append(row[0])

    if "documentos" in tables:
        ok("Tabela 'documentos' existe")
    else:
        fail("Tabela 'documentos' nao existe")
        return False

    if "vec_documentos" in tables:
        ok("Tabela 'vec_documentos' existe")
    else:
        fail("Tabela 'vec_documentos' nao existe")
        return False

    # Contar registros
    for row in cursor.execute("SELECT COUNT(*) FROM documentos"):
        doc_count = row[0]

    for row in cursor.execute("SELECT COUNT(*) FROM vec_documentos"):
        vec_count = row[0]

    if doc_count > 0:
        ok(f"Documentos: {doc_count}")
    else:
        fail("Nenhum documento no banco")
        return False

    if vec_count > 0:
        ok(f"Embeddings: {vec_count}")
    else:
        fail("Nenhum embedding no banco")
        return False

    if doc_count == vec_count:
        ok("Documentos e embeddings sincronizados")
    else:
        warn(f"Dessincronizado: {doc_count} docs vs {vec_count} embeddings")

    conn.close()
    return True


def test_fastembed():
    """Testa modelo de embeddings."""
    print("\n=== Teste de FastEmbed ===")

    from fastembed import TextEmbedding

    try:
        model = TextEmbedding("BAAI/bge-small-en-v1.5")
        ok("Modelo carregado: BAAI/bge-small-en-v1.5")
    except Exception as e:
        fail(f"Erro ao carregar modelo: {e}")
        return False

    # Testar embedding
    test_text = "politica de uso de IA em grandes empresas"
    embeddings = list(model.embed([test_text]))

    if len(embeddings) == 1:
        ok(f"Embedding gerado: {len(embeddings[0])} dimensoes")
    else:
        fail("Erro ao gerar embedding")
        return False

    if len(embeddings[0]) == 384:
        ok("Dimensoes corretas (384)")
    else:
        warn(f"Dimensoes inesperadas: {len(embeddings[0])}")

    return True


def test_search():
    """Testa busca semantica."""
    print("\n=== Teste de Busca Semantica ===")

    import apsw
    import sqlite_vec
    from fastembed import TextEmbedding

    db_path = Path(__file__).parent.parent / "teste" / "documentos.db"
    model = TextEmbedding("BAAI/bge-small-en-v1.5")

    # Gerar embedding da query
    query = "principios obrigatorios da politica de uso de IA"
    embeddings = list(model.embed([query]))
    query_vec = sqlite_vec.serialize_float32(embeddings[0].tolist())

    # Conectar e buscar
    conn = apsw.Connection(str(db_path))
    conn.enableloadextension(True)
    conn.loadextension(sqlite_vec.loadable_path())
    conn.enableloadextension(False)
    cursor = conn.cursor()

    results = []
    for row in cursor.execute("""
        SELECT v.doc_id, v.distance, d.nome
        FROM vec_documentos v
        JOIN documentos d ON d.id = v.doc_id
        WHERE v.embedding MATCH ? AND k = 3
    """, (query_vec,)):
        results.append(row)

    conn.close()

    if len(results) > 0:
        ok(f"Busca retornou {len(results)} resultados")
    else:
        fail("Busca nao retornou resultados")
        return False

    # Verificar se o documento correto aparece primeiro
    first_result = results[0]
    doc_id, distance, nome = first_result

    if "Politica" in nome or "politica" in nome.lower():
        ok(f"Top result correto: {nome}")
    else:
        warn(f"Top result inesperado: {nome}")

    similarity = max(0, 1 - distance)
    if similarity > 0.3:
        ok(f"Similaridade adequada: {similarity:.2%}")
    else:
        warn(f"Similaridade baixa: {similarity:.2%}")

    return True


def test_mcp_server():
    """Testa funcoes do MCP server."""
    print("\n=== Teste de MCP Server ===")

    try:
        from mcp_server import search_documents, list_sources, count_documents, get_document
        ok("Import do mcp_server")
    except Exception as e:
        fail(f"Erro ao importar mcp_server: {e}")
        return False

    # Testar count_documents
    try:
        stats = count_documents()
        if stats["status"] == "ok":
            ok(f"count_documents: {stats['total_documentos']} docs, {stats['total_embeddings']} embeddings")
        else:
            warn(f"count_documents: status = {stats['status']}")
    except Exception as e:
        fail(f"count_documents: {e}")
        return False

    # Testar list_sources
    try:
        sources = list_sources()
        if len(sources) > 0:
            ok(f"list_sources: {len(sources)} fontes")
        else:
            fail("list_sources: nenhuma fonte")
            return False
    except Exception as e:
        fail(f"list_sources: {e}")
        return False

    # Testar search_documents
    try:
        results = search_documents("politica de IA", top_k=3)
        if len(results) > 0:
            ok(f"search_documents: {len(results)} resultados")
            for r in results:
                print(f"       [{r['similarity']:.2f}] {r['source']}")
        else:
            fail("search_documents: nenhum resultado")
            return False
    except Exception as e:
        fail(f"search_documents: {e}")
        return False

    # Testar get_document
    try:
        doc = get_document(1)
        if "error" not in doc:
            ok(f"get_document: {doc.get('nome', 'ok')}")
        else:
            warn(f"get_document: {doc['error']}")
    except Exception as e:
        fail(f"get_document: {e}")
        return False

    return True


def test_config():
    """Testa configuracao do agente."""
    print("\n=== Teste de Configuracao ===")

    try:
        from config import RAG_AGENT_OPTIONS
        ok("Import da config")
    except Exception as e:
        fail(f"Erro ao importar config: {e}")
        return False

    # Verificar campos
    if hasattr(RAG_AGENT_OPTIONS, 'model'):
        ok(f"Model: {RAG_AGENT_OPTIONS.model}")
    else:
        fail("Model nao definido")
        return False

    if hasattr(RAG_AGENT_OPTIONS, 'system_prompt'):
        prompt_len = len(RAG_AGENT_OPTIONS.system_prompt)
        ok(f"System prompt: {prompt_len} chars")
    else:
        fail("System prompt nao definido")
        return False

    if hasattr(RAG_AGENT_OPTIONS, 'allowed_tools'):
        tools = RAG_AGENT_OPTIONS.allowed_tools
        ok(f"Tools permitidas: {len(tools)}")
        for t in tools:
            print(f"       - {t}")
    else:
        fail("Tools nao definidas")
        return False

    if hasattr(RAG_AGENT_OPTIONS, 'mcp_servers'):
        servers = RAG_AGENT_OPTIONS.mcp_servers
        ok(f"MCP Servers: {list(servers.keys())}")
    else:
        fail("MCP servers nao definidos")
        return False

    return True


def run_all_tests():
    """Executa todos os testes."""
    print("=" * 60)
    print("RAG AGENT - TESTE COMPLETO")
    print("=" * 60)

    results = {
        "imports": test_imports(),
        "database": test_database(),
        "fastembed": test_fastembed(),
        "search": test_search(),
        "mcp_server": test_mcp_server(),
        "config": test_config(),
    }

    print("\n" + "=" * 60)
    print("RESUMO")
    print("=" * 60)

    passed = sum(1 for v in results.values() if v)
    total = len(results)

    for name, result in results.items():
        status = f"{GREEN}PASS{RESET}" if result else f"{RED}FAIL{RESET}"
        print(f"  {name}: {status}")

    print(f"\nTotal: {passed}/{total} testes passaram")

    if passed == total:
        print(f"\n{GREEN}SUCESSO! Sistema 100% funcional.{RESET}")
        return 0
    else:
        print(f"\n{RED}FALHA! Corrija os erros acima.{RESET}")
        return 1


if __name__ == "__main__":
    sys.exit(run_all_tests())
