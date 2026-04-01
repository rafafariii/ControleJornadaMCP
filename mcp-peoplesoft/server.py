"""
mcp-peoplesoft — MCP Server para PeopleSoft (Itaú Unibanco)
============================================================
Servidor MCP unificado que combina:
  • Ferramentas de análise PeopleSoft (trace, metadata, PeopleCode, AE)
  • Ferramentas semânticas de RH, Folha, PeopleTools (rgrz/peoplesoft-mcp)
  • Ferramentas específicas de Time & Labor — Jornada Mista / Grupo SEMESTRAL
  • Monitoramento de produção + base de SOPs CAG (peoplesoft_sentry)

Dependências:
    pip install -r requirements.txt

Variáveis de ambiente (.env):
    ORACLE_DSN      = host:port/service  (ou PS_DB_DSN)
    ORACLE_USER     = SYSADM             (ou PS_DB_USER)
    ORACLE_PASSWORD = ****               (ou PS_DB_PASSWORD)
    MCP_PROJECT_DIR = /caminho/do/projeto (opcional)

Uso:
    python server.py
"""

import os
import re
import json
import sqlite3
import datetime
from pathlib import Path
from typing import Any
from contextlib import asynccontextmanager

from fastmcp import FastMCP
from dotenv import load_dotenv

load_dotenv()

# ─── Paths ──────────────────────────────────────────────────────────────────
BASE_DIR      = Path(os.getenv("MCP_PROJECT_DIR", Path(__file__).parent))
KNOWLEDGE_DIR = BASE_DIR / "knowledge"
TRACES_DIR    = KNOWLEDGE_DIR / "traces"
EXPORTS_DIR   = KNOWLEDGE_DIR / "exports"
DB_PATH       = KNOWLEDGE_DIR / "knowledge.db"
DOCS_DIR      = BASE_DIR / "docs"

for d in [KNOWLEDGE_DIR, TRACES_DIR, EXPORTS_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# ─── Whitelist de segurança para run_safe_query ──────────────────────────────
ALLOWED_TABLES = {
    # PeopleTools metadata
    "PSRECDEFN", "PSRECFIELD", "PSDBFIELD", "PSKEYDEFN", "PSXLATITEM",
    "PSPCMPROG", "PSPCMTXT",
    "PSAEAPPLDEFN", "PSAESECTDEFN", "PSAESTEPDEFN", "PSAEAPPLSTATE",
    "PSPRCSRQST", "PS_PRCS_RQST",
    "PSWORKLIST", "PSACTIVITY", "PSROUTE",
    "PSIBSVCSETUP", "PSNODESMSGCONT", "PSMSGNODEDEFN", "PSIBSUBDEFN",
    "PSPROJITEM", "PSPROJECTDEFN", "PSLOCK",
    # T&L metadata
    "PS_TL_RULE_DEFN", "PS_TL_GROUP_RULE", "PS_TL_RULE_STEPS",
    "PS_TL_STP_SQL_TBL", "PS_TL_EMPL_GROUP",
    # HR read-only
    "PS_PERSONAL_DATA", "PS_JOB", "PS_EMPLOYMENT", "PS_DEPT_TBL",
}

# ─── SQLite / Knowledge DB ───────────────────────────────────────────────────

def _get_sqlite():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def _init_db():
    conn = _get_sqlite()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS trace_chunks (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            trace_file TEXT NOT NULL,
            section    TEXT,
            step       TEXT,
            elapsed    REAL,
            content    TEXT,
            indexed_at TEXT
        );
        CREATE VIRTUAL TABLE IF NOT EXISTS trace_fts
        USING fts5(trace_file, section, step, content,
                   content=trace_chunks, content_rowid=id);
        CREATE TABLE IF NOT EXISTS metadata_cache (
            recname   TEXT PRIMARY KEY,
            payload   TEXT,
            cached_at TEXT
        );
        CREATE TABLE IF NOT EXISTS knowledge_notes (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            topic      TEXT,
            category   TEXT,
            content    TEXT,
            created_at TEXT
        );
    """)
    conn.commit()
    conn.close()


_init_db()

# ─── Oracle helper síncrono (para ferramentas de trace/metadata) ─────────────

def _oracle_query(sql: str, params: dict | None = None, max_rows: int = 500) -> list[dict]:
    """Query síncrona usada nas tools legadas (trace, metadata, references)."""
    from db import execute_query_sync
    return execute_query_sync(sql, params, max_rows)


# ─── MCP Server ──────────────────────────────────────────────────────────────
mcp = FastMCP(
    "mcp-peoplesoft",
    instructions=(
        "Servidor MCP para PeopleSoft (Itaú Unibanco). "
        "Ferramentas de análise de metadados PeopleTools, RH, Folha de Pagamento, "
        "Time & Labor e diagnóstico de jornadas mistas. "
        "Use sempre ferramentas de leitura/introspecção antes de propor alterações."
    ),
)

# ─── Resources (documentação inline) ────────────────────────────────────────

@mcp.resource("peoplesoft://schema-guide")
def get_schema_guide() -> str:
    """Guia de tabelas PeopleSoft por módulo (HR, GP, T&L, Benefits, System)."""
    p = DOCS_DIR / "peoplesoft_schema_guide.md"
    return p.read_text(encoding="utf-8") if p.exists() else "Arquivo não encontrado."


@mcp.resource("peoplesoft://concepts")
def get_concepts() -> str:
    """Conceitos PeopleSoft: effective dating, EMPLID, SetID, translate values."""
    p = DOCS_DIR / "peoplesoft_concepts.md"
    return p.read_text(encoding="utf-8") if p.exists() else "Arquivo não encontrado."


@mcp.resource("peoplesoft://query-examples")
def get_query_examples() -> str:
    """Exemplos de queries SQL PeopleSoft: effective-dated, joins, GP, T&L."""
    p = DOCS_DIR / "sql_query_examples.md"
    return p.read_text(encoding="utf-8") if p.exists() else "Arquivo não encontrado."


@mcp.resource("peoplesoft://peopletools-guide")
def get_peopletools_guide() -> str:
    """Arquitetura PeopleTools: Records, Pages, Components, AE, IB, Security."""
    p = DOCS_DIR / "peopletools_guide.md"
    return p.read_text(encoding="utf-8") if p.exists() else "Arquivo não encontrado."


# ════════════════════════════════════════════════════════════════════════════
# BLOCO A — FERRAMENTAS DE ANÁLISE (legado mcp-peoplesoft, mantidas e aprimoradas)
# ════════════════════════════════════════════════════════════════════════════

@mcp.tool()
async def trace_workflow(
    trace_file: str,
    query: str = "",
    top_slow: int = 10,
) -> dict:
    """
    Analisa arquivos de trace PeopleSoft (.tracesql / .trc).

    Quebra o arquivo em chunks por Section/Step, extrai SQLs e timings
    (Elapsed Time), indexa no SQLite FTS5 para busca semântica e
    retorna os N steps mais lentos.

    Coloque o arquivo em knowledge/traces/ antes de chamar esta tool.

    Args:
        trace_file: Nome do arquivo na pasta knowledge/traces/
        query:      Termo de busca FTS no conteúdo do trace (opcional)
        top_slow:   Retorna os N steps mais lentos (padrão: 10)

    Returns:
        Árvore de execução, steps lentos e resultados de busca FTS.
    """
    fpath = TRACES_DIR / trace_file
    if not fpath.exists():
        return {"error": f"Arquivo não encontrado: {fpath}. Copie para knowledge/traces/"}

    raw = fpath.read_text(encoding="utf-8", errors="replace")

    # Parse por Section/Step
    chunks: list[tuple] = []
    current_section, current_step = "", ""
    current_lines: list[str] = []
    elapsed_re = re.compile(r"Elapsed\s+Time\s*[:=]\s*([\d.]+)", re.IGNORECASE)

    for line in raw.splitlines():
        sec_m = re.match(r"^\s*Section\s*[:\-]?\s*(.+)", line, re.IGNORECASE)
        stp_m = re.match(r"^\s*Step\s*[:\-]?\s*(.+)",    line, re.IGNORECASE)

        if sec_m:
            if current_lines:
                txt = "\n".join(current_lines)
                elapsed_vals = [float(x) for x in elapsed_re.findall(txt)]
                chunks.append((current_section, current_step, max(elapsed_vals, default=0.0), txt))
            current_section, current_step = sec_m.group(1).strip(), ""
            current_lines = []
        elif stp_m:
            if current_lines:
                txt = "\n".join(current_lines)
                elapsed_vals = [float(x) for x in elapsed_re.findall(txt)]
                chunks.append((current_section, current_step, max(elapsed_vals, default=0.0), txt))
            current_step  = stp_m.group(1).strip()
            current_lines = []
        else:
            current_lines.append(line)

    if current_lines:
        txt = "\n".join(current_lines)
        elapsed_vals = [float(x) for x in elapsed_re.findall(txt)]
        chunks.append((current_section, current_step, max(elapsed_vals, default=0.0), txt))

    # Indexar no SQLite
    conn  = _get_sqlite()
    now   = datetime.datetime.now().isoformat()
    conn.execute("DELETE FROM trace_chunks WHERE trace_file = ?", (trace_file,))
    for sec, stp, elapsed, content in chunks:
        conn.execute(
            "INSERT INTO trace_chunks (trace_file, section, step, elapsed, content, indexed_at) VALUES (?,?,?,?,?,?)",
            (trace_file, sec, stp, elapsed, content, now),
        )
    conn.commit()

    # Top slow
    slow_cur = conn.execute(
        "SELECT section, step, elapsed FROM trace_chunks WHERE trace_file = ? ORDER BY elapsed DESC LIMIT ?",
        (trace_file, top_slow),
    )
    slow_steps = [dict(r) for r in slow_cur.fetchall()]

    # FTS
    fts_results: list[dict] = []
    if query.strip():
        fts_cur = conn.execute(
            """
            SELECT tc.section, tc.step, tc.elapsed, SUBSTR(tc.content, 1, 400) AS snippet
            FROM trace_fts
            JOIN trace_chunks tc ON tc.id = trace_fts.rowid
            WHERE trace_fts MATCH ? AND tc.trace_file = ?
            LIMIT 20
            """,
            (query, trace_file),
        )
        fts_results = [dict(r) for r in fts_cur.fetchall()]

    conn.close()

    return {
        "trace_file":    trace_file,
        "total_chunks":  len(chunks),
        "top_slow_steps": slow_steps,
        "fts_query":     query,
        "fts_results":   fts_results,
        "indexed_at":    now,
    }


@mcp.tool()
async def get_table_metadata(recname: str, include_fields: bool = True) -> dict:
    """
    Retorna metadados completos de um record PeopleSoft.

    Consulta PSRECDEFN, PSRECFIELD e PSDBFIELD. Cacheia o resultado
    no SQLite local para respostas mais rápidas em consultas repetidas.

    Args:
        recname:        Nome do record (ex: 'JOB', 'TL_GROUP_RULE'). PS_ é opcional.
        include_fields: Se True (padrão), inclui a lista de fields com tipos.

    Returns:
        Metadados do record + lista de fields (se include_fields=True).
    """
    clean = recname.upper().replace("PS_", "")

    # Verificar cache
    conn = _get_sqlite()
    row  = conn.execute("SELECT payload FROM metadata_cache WHERE recname = ?", (clean,)).fetchone()
    conn.close()
    if row:
        return json.loads(row["payload"])

    try:
        rec_rows = _oracle_query(
            "SELECT RECNAME, RECDESCR, RECTYPE, FIELDCOUNT FROM PSRECDEFN WHERE RECNAME = :recname",
            {"recname": clean},
        )
    except Exception as e:
        return {"error": str(e)}

    if not rec_rows:
        return {"error": f"Record '{recname}' não encontrado."}

    result: dict[str, Any] = {
        "record_name": clean,
        "table_name":  f"PS_{clean}",
        **rec_rows[0],
    }

    if include_fields:
        try:
            fields = _oracle_query(
                """
                SELECT f.FIELDNAME, f.FIELDNUM, f.FIELDTYPE, f.LENGTH,
                       d.LONGNAME, f.USEEDIT
                FROM PSRECFIELD f
                JOIN PSDBFIELD d ON d.FIELDNAME = f.FIELDNAME
                WHERE f.RECNAME = :recname
                ORDER BY f.FIELDNUM
                """,
                {"recname": clean},
            )
            result["fields"] = fields
        except Exception as e:
            result["fields_error"] = str(e)

    # Salvar cache
    conn = _get_sqlite()
    conn.execute(
        "INSERT OR REPLACE INTO metadata_cache (recname, payload, cached_at) VALUES (?,?,?)",
        (clean, json.dumps(result, default=str), datetime.datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()

    return result


@mcp.tool()
async def get_peoplecode(
    objectid1: str,
    objectid2: str | None = None,
    event_name: str | None = None,
) -> dict:
    """
    Extrai texto de PeopleCode de PSPCMPROG + PSPCMTXT.

    Use para ler o código de eventos como FieldChange, SavePreChange,
    ou código de Application Engine.

    Args:
        objectid1:  Record ou AE Application (ex: 'JOB', 'TL_TA')
        objectid2:  Field ou Section (ex: 'EMPLID', 'MAIN')
        event_name: Evento PeopleCode (ex: 'FieldChange', 'SavePreChange')

    Returns:
        Texto completo do PeopleCode para os objetos encontrados.
    """
    sql = """
        SELECT p.OBJECTVALUE1, p.OBJECTVALUE2, p.OBJECTVALUE3,
               t.PCMPTEXT
        FROM PSPCMPROG p
        JOIN PSPCMTXT  t ON t.OBJECTID1    = p.OBJECTID1
                        AND t.OBJECTVALUE1 = p.OBJECTVALUE1
        WHERE p.OBJECTVALUE1 = :objectid1
    """
    params: dict[str, Any] = {"objectid1": objectid1.upper()}
    if objectid2:
        sql += " AND p.OBJECTVALUE2 = :objectid2"
        params["objectid2"] = objectid2.upper()
    if event_name:
        sql += " AND UPPER(p.OBJECTVALUE3) = UPPER(:event_name)"
        params["event_name"] = event_name

    try:
        rows = _oracle_query(sql, params)
    except Exception as e:
        return {"error": str(e)}

    return {
        "object": objectid1,
        "count":  len(rows),
        "results": rows,
    }


@mcp.tool()
async def search_references(field_or_record: str, search_type: str = "both") -> dict:
    """
    Busca onde um field ou record é referenciado em PeopleCode, AE e RecField.

    Útil para análise de impacto antes de modificar um campo ou tabela.

    Args:
        field_or_record: Nome do field ou record a buscar
        search_type:     'field', 'record' ou 'both' (padrão)

    Returns:
        Referências em PeopleCode (PSPCMPROG), AE (PSAESTEPDEFN) e RecField (PSRECFIELD).
    """
    term   = field_or_record.upper()
    result = {"term": term, "references": {}}

    if search_type in ("field", "both"):
        try:
            pc_rows = _oracle_query(
                "SELECT OBJECTVALUE1, OBJECTVALUE2, OBJECTVALUE3 FROM PSPCMPROG "
                "WHERE UPPER(PCMPTEXT) LIKE :pat",
                {"pat": f"%{term}%"},
                max_rows=100,
            )
            result["references"]["peoplecode"] = pc_rows
        except Exception as e:
            result["references"]["peoplecode_error"] = str(e)

    if search_type in ("record", "both"):
        try:
            ae_rows = _oracle_query(
                "SELECT AE_APPLID, AE_SECTION, AESTEPNUM FROM PSAESTEPDEFN "
                "WHERE UPPER(STMTTEXT) LIKE :pat",
                {"pat": f"%{term}%"},
                max_rows=100,
            )
            result["references"]["application_engine"] = ae_rows
        except Exception as e:
            result["references"]["ae_error"] = str(e)

    try:
        rf_rows = _oracle_query(
            "SELECT RECNAME FROM PSRECFIELD WHERE FIELDNAME = :term",
            {"term": term},
            max_rows=200,
        )
        result["references"]["record_field"] = rf_rows
    except Exception as e:
        result["references"]["record_field_error"] = str(e)

    return result


@mcp.tool()
async def run_safe_query(sql: str, max_rows: int = 100) -> dict:
    """
    Executa SELECT em tabelas PeopleSoft autorizadas (whitelist de segurança).

    Bloqueia qualquer DML (INSERT, UPDATE, DELETE, DROP, TRUNCATE).
    Use para exploração ad-hoc do banco sem risco de modificação acidental.

    Args:
        sql:      Instrução SELECT (apenas leitura)
        max_rows: Número máximo de linhas retornadas (padrão: 100)

    Returns:
        Resultados da query ou mensagem de erro/bloqueio.
    """
    upper_sql = sql.upper().strip()

    # Bloquear DML
    for dml in ("INSERT", "UPDATE", "DELETE", "DROP", "TRUNCATE", "MERGE", "CREATE", "ALTER"):
        if re.search(rf"\b{dml}\b", upper_sql):
            return {"error": f"Operação '{dml}' bloqueada. Apenas SELECT é permitido."}

    # Verificar whitelist
    tables_in_query = set(re.findall(r"\bPS[_\.](\w+)\b", upper_sql))
    raw_tables      = set(re.findall(r"\bFROM\s+(\w+)\b|\bJOIN\s+(\w+)\b", upper_sql))
    all_tables      = tables_in_query | {t for pair in raw_tables for t in pair if t}

    blocked = all_tables - ALLOWED_TABLES
    if blocked and not any(t in ALLOWED_TABLES for t in all_tables):
        return {
            "warning": f"Tabelas fora da whitelist: {blocked}. Query executada com cautela.",
            "allowed_tables": sorted(ALLOWED_TABLES),
        }

    try:
        rows = _oracle_query(sql, max_rows=max_rows)
        return {"row_count": len(rows), "results": rows}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
async def suggest_change(
    target: str,
    change_desc: str,
    change_type: str,
) -> dict:
    """
    Analisa o impacto de uma mudança proposta e sugere passos de implementação.

    Busca referências do target no banco (PeopleCode, AE, RecField) e gera
    um plano de implementação comentado com riscos e dependências.

    Args:
        target:      Record ou field alvo da mudança (ex: 'PS_TL_IPT', 'TRC')
        change_desc: Descrição da mudança (ex: 'Adicionar condição de grupo')
        change_type: Tipo: 'field_add', 'field_modify', 'record_add', 'peoplecode', 'ae_step'

    Returns:
        Análise de impacto + plano de implementação.
    """
    refs = await search_references(target, "both")

    pc_count  = len(refs.get("references", {}).get("peoplecode", []))
    ae_count  = len(refs.get("references", {}).get("application_engine", []))
    rf_count  = len(refs.get("references", {}).get("record_field", []))

    risk = "ALTO" if (pc_count + ae_count) > 10 else "MÉDIO" if (pc_count + ae_count) > 3 else "BAIXO"

    steps_map = {
        "field_add":     ["Criar field em App Designer", "Adicionar ao record", "Build do record", "Atualizar PeopleCode se necessário", "Testar em homologação"],
        "field_modify":  ["Analisar impacto nos objetos referenciados", "Modificar field em App Designer", "Rebuild do record", "Atualizar queries/PeopleCode dependentes", "Testar"],
        "record_add":    ["Criar record em App Designer", "Definir keys e indexes", "Build SQL Table", "Criar PeopleCode/AE se necessário", "Testar"],
        "peoplecode":    ["Identificar evento e objeto", "Editar PeopleCode em App Designer", "Salvar e verificar sintaxe", "Testar com dados reais"],
        "ae_step":       ["Identificar Section/Step do AE", "Modificar SQL ou PeopleCode do step", "Testar com Process Scheduler", "Validar lançamentos gerados"],
    }

    return {
        "target":       target,
        "change_desc":  change_desc,
        "change_type":  change_type,
        "risk_level":   risk,
        "impact": {
            "peoplecode_references": pc_count,
            "ae_references":         ae_count,
            "record_field_refs":     rf_count,
        },
        "implementation_steps": steps_map.get(change_type, ["Consultar documentação Oracle PeopleTools"]),
        "references_detail":    refs.get("references", {}),
    }


@mcp.tool()
async def get_workflow_def(business_process: str, activity: str | None = None) -> dict:
    """
    Retorna definição de Workflow PeopleSoft (PSACTIVITY + PSROUTE).

    Args:
        business_process: Nome do Business Process
        activity:         Nome da Activity (opcional)

    Returns:
        Activities e Routes do workflow.
    """
    sql = "SELECT BUSPROCNAME, ACTIVITYNAME, DESCR, STATUS FROM PSACTIVITY WHERE BUSPROCNAME = :bp"
    params: dict[str, Any] = {"bp": business_process}
    if activity:
        sql += " AND ACTIVITYNAME = :act"
        params["act"] = activity

    try:
        activities = _oracle_query(sql, params)
        routes     = _oracle_query(
            "SELECT BUSPROCNAME, ACTIVITYNAME, ROUTENAME, DESCR FROM PSROUTE WHERE BUSPROCNAME = :bp",
            {"bp": business_process},
        )
    except Exception as e:
        return {"error": str(e)}

    return {
        "business_process": business_process,
        "activities":       activities,
        "routes":           routes,
    }


@mcp.tool()
async def get_ib_service(service_name: str, node_name: str | None = None) -> dict:
    """
    Retorna detalhes do Integration Broker para um serviço.

    Consulta PSIBSVCSETUP, PSIBSUBDEFN e PSMSGNODEDEFN.

    Args:
        service_name: Nome do serviço IB
        node_name:    Nome do node (opcional)

    Returns:
        Setup do serviço, subscriptions e nodes configurados.
    """
    try:
        svc  = _oracle_query(
            "SELECT IBSERVICENAME, DESCR, SERVICETYPE FROM PSIBSVCSETUP WHERE IBSERVICENAME = :svc",
            {"svc": service_name},
        )
        subs = _oracle_query(
            "SELECT IBSUBNAME, SUBTYPE, DESCR FROM PSIBSUBDEFN WHERE IBSERVICENAME = :svc",
            {"svc": service_name},
        )
        node_sql    = "SELECT MSGNODENAME, DESCR, DEFAULTMSGNODE FROM PSMSGNODEDEFN"
        node_params = {}
        if node_name:
            node_sql += " WHERE MSGNODENAME = :node"
            node_params["node"] = node_name
        nodes = _oracle_query(node_sql, node_params)
    except Exception as e:
        return {"error": str(e)}

    return {"service": svc, "subscriptions": subs, "nodes": nodes}


@mcp.tool()
async def export_knowledge(topic: str, format: str = "markdown") -> dict:
    """
    Exporta o conhecimento acumulado (traces indexados + notas) em markdown ou JSON.

    Args:
        topic:  Filtro por tópico (ex: 'TL_TA', 'overtime', 'SEMESTRAL')
        format: 'markdown' (padrão) ou 'json'

    Returns:
        Caminho do arquivo exportado + preview do conteúdo.
    """
    conn    = _get_sqlite()
    chunks  = conn.execute(
        "SELECT trace_file, section, step, content FROM trace_chunks WHERE content LIKE ?",
        (f"%{topic}%",),
    ).fetchall()
    notes   = conn.execute(
        "SELECT topic, category, content FROM knowledge_notes WHERE topic LIKE ?",
        (f"%{topic}%",),
    ).fetchall()
    conn.close()

    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    filename  = EXPORTS_DIR / f"export_{topic}_{timestamp}.{format}"

    if format == "markdown":
        content = f"# Knowledge Export: {topic}\n\n"
        content += f"Gerado em: {datetime.datetime.now().isoformat()}\n\n"
        if chunks:
            content += "## Trace Chunks\n\n"
            for c in chunks:
                content += f"### {c['trace_file']} — {c['section']}.{c['step']}\n```\n{c['content'][:500]}\n```\n\n"
        if notes:
            content += "## Notas de Conhecimento\n\n"
            for n in notes:
                content += f"### {n['topic']} ({n['category']})\n{n['content']}\n\n"
    else:
        content = json.dumps({
            "topic": topic, "trace_chunks": [dict(c) for c in chunks],
            "notes": [dict(n) for n in notes],
        }, indent=2, default=str)

    filename.write_text(content, encoding="utf-8")

    return {
        "exported_to": str(filename),
        "chunks_found": len(chunks),
        "notes_found":  len(notes),
        "preview":      content[:800],
    }


# ════════════════════════════════════════════════════════════════════════════
# BLOCO B — FERRAMENTAS SEMÂNTICAS (rgrz/peoplesoft-mcp)
# ════════════════════════════════════════════════════════════════════════════
from tools.introspection import register_tools as _reg_introspection
from tools.hr            import register_tools as _reg_hr
from tools.payroll       import register_tools as _reg_payroll
from tools.performance   import register_tools as _reg_performance
from tools.benefits      import register_tools as _reg_benefits
from tools.peopletools   import register_tools as _reg_peopletools

_reg_introspection(mcp)
_reg_hr(mcp)
_reg_payroll(mcp)
_reg_performance(mcp)
_reg_benefits(mcp)
_reg_peopletools(mcp)

# ════════════════════════════════════════════════════════════════════════════
# BLOCO C — FERRAMENTAS T&L ESPECÍFICAS (Itaú / Jornada Mista / SEMESTRAL)
# ════════════════════════════════════════════════════════════════════════════
from tools.tl import register_tools as _reg_tl
_reg_tl(mcp)

# ════════════════════════════════════════════════════════════════════════════
# BLOCO D — SENTRY: MONITORAMENTO DE PRODUÇÃO + BASE DE SOPs (CAG)
#   Inspirado em peoplesoft_sentry (AIOps Diagnostic Engine)
#   • ps_get_ib_errors        → Erros no Integration Broker (PS_MSG_INST)
#   • ps_get_process_errors   → Falhas no Process Monitor (PSPRCSRQST)
#   • ps_get_system_summary   → Resumo de saúde do ambiente
#   • ps_health_check         → Diagnóstico completo + match automático de SOP
#   • ps_lookup_sop           → Consulta à base de SOPs por texto de erro
# ════════════════════════════════════════════════════════════════════════════
from tools.sentry import register_tools as _reg_sentry
_reg_sentry(mcp)

# Resource: biblioteca de SOPs como contexto CAG (Cache-Augmented Generation)
@mcp.resource("peoplesoft://sop-library")
def get_sop_library() -> str:
    """Base de conhecimento de SOPs PeopleSoft para suporte a produção (CAG)."""
    from tools.sentry import get_all_sops_as_text
    return get_all_sops_as_text()


# ─── Entry point ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 60)
    print("  mcp-peoplesoft — PeopleSoft MCP Server (Itaú Unibanco)")
    print("=" * 60)
    print("  Blocos carregados:")
    print("  [A] Análise: trace_workflow, get_table_metadata,")
    print("      get_peoplecode, search_references, run_safe_query,")
    print("      suggest_change, get_workflow_def, get_ib_service,")
    print("      export_knowledge")
    print("  [B] Semântico: describe_table, list_tables,")
    print("      get_employee, search_employees, get_job_history,")
    print("      get_org_chart, get_payroll_results, + benefits,")
    print("      performance, 20+ PeopleTools tools")
    print("  [C] T&L: tl_list_group_rules, tl_get_rule_step_sql,")
    print("      tl_find_overtime_rules, tl_get_employee_ipt,")
    print("      tl_detect_mixed_shift_bug, tl_generate_fix_proposal,")
    print("      tl_group_coverage_report")
    print("  [D] Sentry: ps_get_ib_errors, ps_get_process_errors,")
    print("      ps_get_system_summary, ps_health_check, ps_lookup_sop")
    print("  Resources: schema-guide, concepts, query-examples,")
    print("      peopletools-guide, sop-library (CAG)")
    print("=" * 60)
    mcp.run()
