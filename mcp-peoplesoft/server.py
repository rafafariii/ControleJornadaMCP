"""
mcp-peoplesoft: MCP Server para integração com PeopleSoft
Ferramentas de análise de fluxo, metadados e manutenção de código PeopleSoft.
"""

import os
import re
import json
import sqlite3
import hashlib
import datetime
from pathlib import Path
from typing import Any, Optional

import mcp.server.stdio
import mcp.types as types
from mcp.server import Server

# ─── Paths ────────────────────────────────────────────────────────────────────
BASE_DIR = Path(os.getenv("MCP_PROJECT_DIR", Path(__file__).parent))
KNOWLEDGE_DIR = BASE_DIR / "knowledge"
TRACES_DIR    = KNOWLEDGE_DIR / "traces"
VECTORS_DIR   = KNOWLEDGE_DIR / "vectors"
EXPORTS_DIR   = KNOWLEDGE_DIR / "exports"
DB_PATH       = KNOWLEDGE_DIR / "knowledge.db"

for d in [KNOWLEDGE_DIR, TRACES_DIR, VECTORS_DIR, EXPORTS_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# ─── Oracle credentials ────────────────────────────────────────────────────────
PS_DB_USER     = os.getenv("PS_DB_USER", "")
PS_DB_PASSWORD = os.getenv("PS_DB_PASSWORD", "")
PS_DB_DSN      = os.getenv("PS_DB_DSN", "")

# ─── Whitelist de tabelas para run_safe_query ──────────────────────────────────
ALLOWED_TABLES = {
    "PSRECDEFN", "PSRECFIELD", "PSDBFIELD",
    "PSPCMPROG", "PSPCMTXT",
    "PSAEAPPLDEFN", "PSAESECTDEFN", "PSAESTEPDEFN", "PSAEAPPLSTATE",
    "PSPRCSRQST", "PS_PRCS_RQST",
    "PSWORKLIST", "PSACTIVITY", "PSROUTE",
    "PSIBSVCSETUP", "PSNODESMSGCONT", "PSMSGNODEDEFN", "PSIBSUBDEFN",
    "PSPROJITEM", "PSPROJECTDEFN", "PSLOCK",
}

# ─── SQLite / Knowledge DB ─────────────────────────────────────────────────────

def get_sqlite_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_sqlite_conn()
    cur = conn.cursor()
    # Tabela de chunks de trace (FTS5)
    cur.executescript("""
        CREATE TABLE IF NOT EXISTS trace_chunks (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            trace_file TEXT NOT NULL,
            section   TEXT,
            step      TEXT,
            elapsed   REAL,
            content   TEXT,
            indexed_at TEXT
        );

        CREATE VIRTUAL TABLE IF NOT EXISTS trace_fts
        USING fts5(trace_file, section, step, content, content=trace_chunks, content_rowid=id);

        CREATE TABLE IF NOT EXISTS metadata_cache (
            recname     TEXT PRIMARY KEY,
            payload     TEXT,
            cached_at   TEXT
        );

        CREATE TABLE IF NOT EXISTS knowledge_notes (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            topic       TEXT,
            category    TEXT,
            content     TEXT,
            created_at  TEXT
        );
    """)
    conn.commit()
    conn.close()

init_db()

# ─── Oracle helper ─────────────────────────────────────────────────────────────

def get_oracle_conn():
    try:
        import oracledb
        conn = oracledb.connect(
            user=PS_DB_USER,
            password=PS_DB_PASSWORD,
            dsn=PS_DB_DSN,
        )
        return conn
    except Exception as e:
        raise ConnectionError(f"Falha ao conectar ao Oracle: {e}")


def oracle_query(sql: str, params: dict | None = None, max_rows: int = 500) -> list[dict]:
    conn = get_oracle_conn()
    try:
        cur = conn.cursor()
        cur.execute(sql, params or {})
        cols = [d[0] for d in cur.description]
        rows = cur.fetchmany(max_rows)
        return [dict(zip(cols, row)) for row in rows]
    finally:
        conn.close()

# ─── Embedding (lazy load) ─────────────────────────────────────────────────────
_embed_model = None

def get_embed_model():
    global _embed_model
    if _embed_model is None:
        from sentence_transformers import SentenceTransformer
        _embed_model = SentenceTransformer("all-MiniLM-L6-v2")
    return _embed_model

# ─── Dependency graph ──────────────────────────────────────────────────────────
import networkx as nx
dep_graph = nx.DiGraph()

# ─── MCP Server ───────────────────────────────────────────────────────────────
server = Server("mcp-peoplesoft")

# ══════════════════════════════════════════════════════════════════════════════
# TOOL 1 — trace_workflow
# ══════════════════════════════════════════════════════════════════════════════
@server.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="trace_workflow",
            description=(
                "Faz parse de arquivos .tracesql/.trc do PeopleSoft. "
                "Quebra em chunks por Section/Step, extrai SQLs e timings (Elapsed Time). "
                "Indexa no SQLite FTS5 para busca de texto e retorna os N steps mais lentos."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "trace_file": {"type": "string", "description": "Nome do arquivo em knowledge/traces/"},
                    "query":      {"type": "string", "description": "Busca FTS no conteúdo do trace"},
                    "top_slow":   {"type": "integer", "description": "Retorna N steps mais lentos", "default": 10},
                },
                "required": ["trace_file"],
            },
        ),
        types.Tool(
            name="get_table_metadata",
            description=(
                "Consulta PSRECDEFN, PSRECFIELD e PSDBFIELD para retornar metadados "
                "completos de um record PeopleSoft. Cacheia resultado no SQLite local."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "recname":        {"type": "string", "description": "Nome do record (ex: JOB, PERSONAL_DATA)"},
                    "include_fields": {"type": "boolean", "description": "Incluir lista de fields", "default": True},
                },
                "required": ["recname"],
            },
        ),
        types.Tool(
            name="get_peoplecode",
            description=(
                "Extrai texto de PeopleCode de PSPCMPROG + PSPCMTXT. "
                "Permite filtrar por object, field/section e event name."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "objectid1":  {"type": "string", "description": "Record ou AE Application (ex: JOB, AE_APP)"},
                    "objectid2":  {"type": "string", "description": "Field ou Section (ex: EMPLID, MAIN_SECTION)"},
                    "event_name": {"type": "string", "description": "Evento PeopleCode (ex: FieldChange, SavePreChange)"},
                },
                "required": ["objectid1"],
            },
        ),
        types.Tool(
            name="search_references",
            description=(
                "Busca onde um field ou record é referenciado em PSPCMPROG, "
                "PSRECFIELD e PSAESTEPDEFN."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "field_or_record": {"type": "string", "description": "Nome do field ou record"},
                    "search_type":     {"type": "string", "enum": ["field", "record", "both"], "default": "both"},
                },
                "required": ["field_or_record"],
            },
        ),
        types.Tool(
            name="run_safe_query",
            description=(
                "Executa apenas SELECT em whitelist de tabelas PeopleSoft autorizadas. "
                "Bloqueia qualquer DML (INSERT/UPDATE/DELETE/DROP/TRUNCATE)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "sql":      {"type": "string", "description": "Instrução SELECT"},
                    "max_rows": {"type": "integer", "description": "Máximo de linhas retornadas", "default": 100},
                },
                "required": ["sql"],
            },
        ),
        types.Tool(
            name="suggest_change",
            description=(
                "Analisa impacto de uma mudança proposta buscando referências no Oracle "
                "e gera sugestão comentada com passos de implementação."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "target":      {"type": "string", "description": "Record ou field alvo"},
                    "change_desc": {"type": "string", "description": "Descrição da mudança proposta"},
                    "change_type": {
                        "type": "string",
                        "enum": ["field_add", "field_modify", "record_add", "peoplecode", "ae_step"],
                        "description": "Tipo de mudança",
                    },
                },
                "required": ["target", "change_desc", "change_type"],
            },
        ),
        types.Tool(
            name="get_workflow_def",
            description="Consulta PSACTIVITY e PSROUTE para retornar definição de workflow PeopleSoft.",
            inputSchema={
                "type": "object",
                "properties": {
                    "business_process": {"type": "string", "description": "Nome do Business Process"},
                    "activity":         {"type": "string", "description": "Nome da Activity"},
                },
                "required": ["business_process"],
            },
        ),
        types.Tool(
            name="get_ib_service",
            description=(
                "Introspecta PSIBSVCSETUP, PSIBSUBDEFN e PSMSGNODEDEFN "
                "para detalhes do Integration Broker."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "service_name": {"type": "string", "description": "Nome do serviço IB"},
                    "node_name":    {"type": "string", "description": "Nome do node (opcional)"},
                },
                "required": ["service_name"],
            },
        ),
        types.Tool(
            name="export_knowledge",
            description=(
                "Exporta conhecimento acumulado (metadados + referências de trace) "
                "em markdown ou JSON para knowledge/exports/."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "topic":  {"type": "string", "description": "Tópico ou filtro para exportar"},
                    "format": {"type": "string", "enum": ["markdown", "json"], "default": "markdown"},
                },
                "required": ["topic"],
            },
        ),
    ]


# ══════════════════════════════════════════════════════════════════════════════
# TOOL HANDLER
# ══════════════════════════════════════════════════════════════════════════════
@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[types.TextContent]:

    # ── Tool 1: trace_workflow ─────────────────────────────────────────────────
    if name == "trace_workflow":
        trace_file = arguments["trace_file"]
        query      = arguments.get("query", "")
        top_slow   = int(arguments.get("top_slow", 10))

        fpath = TRACES_DIR / trace_file
        if not fpath.exists():
            return [types.TextContent(type="text", text=f"Arquivo não encontrado: {fpath}")]

        raw = fpath.read_text(encoding="utf-8", errors="replace")

        # Parse: quebra por Section/Step e extrai Elapsed Time
        chunks = []
        current_section = ""
        current_step    = ""
        current_lines   = []
        elapsed_pattern = re.compile(r"Elapsed\s+Time\s*[:=]\s*([\d.]+)", re.IGNORECASE)

        for line in raw.splitlines():
            sec_m = re.match(r"^\s*Section\s*[:\-]?\s*(.+)", line, re.IGNORECASE)
            stp_m = re.match(r"^\s*Step\s*[:\-]?\s*(.+)",    line, re.IGNORECASE)

            if sec_m:
                if current_lines:
                    txt = "\n".join(current_lines)
                    elapsed_vals = [float(x) for x in elapsed_pattern.findall(txt)]
                    elapsed = max(elapsed_vals) if elapsed_vals else 0.0
                    chunks.append((current_section, current_step, elapsed, txt))
                current_section = sec_m.group(1).strip()
                current_step    = ""
                current_lines   = [line]
            elif stp_m:
                if current_lines:
                    txt = "\n".join(current_lines)
                    elapsed_vals = [float(x) for x in elapsed_pattern.findall(txt)]
                    elapsed = max(elapsed_vals) if elapsed_vals else 0.0
                    chunks.append((current_section, current_step, elapsed, txt))
                current_step  = stp_m.group(1).strip()
                current_lines = [line]
            else:
                current_lines.append(line)

        if current_lines:
            txt = "\n".join(current_lines)
            elapsed_vals = [float(x) for x in elapsed_pattern.findall(txt)]
            elapsed = max(elapsed_vals) if elapsed_vals else 0.0
            chunks.append((current_section, current_step, elapsed, txt))

        # Indexa no SQLite FTS5
        conn = get_sqlite_conn()
        cur  = conn.cursor()
        cur.execute("DELETE FROM trace_chunks WHERE trace_file = ?", (trace_file,))
        now  = datetime.datetime.utcnow().isoformat()
        for sec, stp, elapsed, content in chunks:
            cur.execute(
                "INSERT INTO trace_chunks (trace_file, section, step, elapsed, content, indexed_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (trace_file, sec, stp, elapsed, content, now),
            )
        # Rebuild FTS
        cur.execute("INSERT INTO trace_fts(trace_fts) VALUES('rebuild')")
        conn.commit()

        result_lines = [f"✅ {len(chunks)} chunks indexados de '{trace_file}'."]

        # Busca FTS
        if query:
            rows = cur.execute(
                "SELECT section, step, elapsed, snippet(trace_fts,3,'>>','<<','…',20) AS snip "
                "FROM trace_fts WHERE trace_fts MATCH ? LIMIT 20",
                (query,),
            ).fetchall()
            result_lines.append(f"\n🔍 Resultados para '{query}':")
            for r in rows:
                result_lines.append(f"  [{r['section']} / {r['step']}] {r['elapsed']:.3f}s — {r['snip']}")

        # Top slow steps
        slow = cur.execute(
            "SELECT section, step, elapsed FROM trace_chunks WHERE trace_file=? "
            "ORDER BY elapsed DESC LIMIT ?",
            (trace_file, top_slow),
        ).fetchall()
        result_lines.append(f"\n🐢 Top {top_slow} steps mais lentos:")
        for r in slow:
            result_lines.append(f"  [{r['section']} / {r['step']}] {r['elapsed']:.3f}s")

        conn.close()
        return [types.TextContent(type="text", text="\n".join(result_lines))]

    # ── Tool 2: get_table_metadata ─────────────────────────────────────────────
    elif name == "get_table_metadata":
        recname        = arguments["recname"].upper()
        include_fields = arguments.get("include_fields", True)

        # Verifica cache
        conn = get_sqlite_conn()
        cached = conn.execute(
            "SELECT payload FROM metadata_cache WHERE recname=?", (recname,)
        ).fetchone()
        conn.close()

        if cached:
            return [types.TextContent(type="text", text=f"[CACHE]\n{cached['payload']}")]

        try:
            rec = oracle_query(
                "SELECT RECNAME, RECDESCR, RECTYPE, FIELDCOUNT, AUDITRECNAME "
                "FROM PSRECDEFN WHERE RECNAME = :r",
                {"r": recname},
            )
            if not rec:
                return [types.TextContent(type="text", text=f"Record '{recname}' não encontrado.")]

            lines = [f"## Record: {recname}", f"Descrição : {rec[0].get('RECDESCR','')}",
                     f"Tipo      : {rec[0].get('RECTYPE','')}",
                     f"Qtd Fields: {rec[0].get('FIELDCOUNT','')}"]

            if include_fields:
                fields = oracle_query(
                    "SELECT f.FIELDNAME, f.FIELDTYPE, f.LENGTH, f.DECIMALPOS, "
                    "       d.LONGNAME, f.USEEDIT, f.FIELDNUM "
                    "FROM PSRECFIELD f "
                    "JOIN PSDBFIELD d ON d.FIELDNAME = f.FIELDNAME "
                    "WHERE f.RECNAME = :r ORDER BY f.FIELDNUM",
                    {"r": recname},
                )
                lines.append("\n### Fields:")
                for fld in fields:
                    lines.append(
                        f"  {fld['FIELDNUM']:3}. {fld['FIELDNAME']:<30} "
                        f"type={fld['FIELDTYPE']} len={fld['LENGTH']} "
                        f"| {fld.get('LONGNAME','')}"
                    )

            payload = "\n".join(lines)

            # Salva cache
            conn = get_sqlite_conn()
            conn.execute(
                "INSERT OR REPLACE INTO metadata_cache (recname, payload, cached_at) VALUES (?,?,?)",
                (recname, payload, datetime.datetime.utcnow().isoformat()),
            )
            conn.commit()
            conn.close()

            return [types.TextContent(type="text", text=payload)]

        except ConnectionError as e:
            return [types.TextContent(type="text", text=str(e))]

    # ── Tool 3: get_peoplecode ─────────────────────────────────────────────────
    elif name == "get_peoplecode":
        objectid1  = arguments["objectid1"].upper()
        objectid2  = arguments.get("objectid2", "").upper()
        event_name = arguments.get("event_name", "").upper()

        try:
            sql = (
                "SELECT p.OBJECTID1, p.OBJECTID2, p.OBJECTID3, p.OBJECTVALUE1, "
                "       p.OBJECTVALUE2, p.OBJECTVALUE3, t.PCMPTEXT "
                "FROM PSPCMPROG p "
                "JOIN PSPCMTXT  t ON t.OBJECTID1=p.OBJECTID1 "
                "                AND t.OBJECTVALUE1=p.OBJECTVALUE1 "
                "WHERE p.OBJECTVALUE1 = :oid1"
            )
            params: dict = {"oid1": objectid1}
            if objectid2:
                sql += " AND p.OBJECTVALUE2 = :oid2"
                params["oid2"] = objectid2
            if event_name:
                sql += " AND UPPER(p.OBJECTVALUE3) = :ev"
                params["ev"] = event_name

            rows = oracle_query(sql, params, max_rows=50)
            if not rows:
                return [types.TextContent(type="text", text="Nenhum PeopleCode encontrado para os filtros informados.")]

            lines = []
            for r in rows:
                lines.append(
                    f"--- {r.get('OBJECTVALUE1','')} / {r.get('OBJECTVALUE2','')} / {r.get('OBJECTVALUE3','')} ---"
                )
                lines.append(r.get("PCMPTEXT", ""))
                lines.append("")

            return [types.TextContent(type="text", text="\n".join(lines))]

        except ConnectionError as e:
            return [types.TextContent(type="text", text=str(e))]

    # ── Tool 4: search_references ──────────────────────────────────────────────
    elif name == "search_references":
        target      = arguments["field_or_record"].upper()
        search_type = arguments.get("search_type", "both")

        results: list[str] = [f"## Referências para: {target}\n"]

        try:
            # PeopleCode references
            if search_type in ("field", "both"):
                pc_rows = oracle_query(
                    "SELECT OBJECTVALUE1, OBJECTVALUE2, OBJECTVALUE3 FROM PSPCMPROG "
                    "WHERE UPPER(PCMPTEXT) LIKE :pat",
                    {"pat": f"%{target}%"},
                    max_rows=200,
                )
                results.append(f"### PSPCMPROG ({len(pc_rows)} ocorrências):")
                for r in pc_rows[:50]:
                    results.append(f"  {r['OBJECTVALUE1']} / {r['OBJECTVALUE2']} / {r['OBJECTVALUE3']}")

            # PSRECFIELD
            if search_type in ("record", "both"):
                rec_rows = oracle_query(
                    "SELECT RECNAME, FIELDNAME, FIELDNUM FROM PSRECFIELD "
                    "WHERE RECNAME=:t OR FIELDNAME=:t",
                    {"t": target},
                    max_rows=200,
                )
                results.append(f"\n### PSRECFIELD ({len(rec_rows)} ocorrências):")
                for r in rec_rows[:50]:
                    results.append(f"  {r['RECNAME']}.{r['FIELDNAME']} (pos {r['FIELDNUM']})")

            # PSAESTEPDEFN
            ae_rows = oracle_query(
                "SELECT AE_APPLID, AE_SECTION, AESTEPNUM, STMTTYPE FROM PSAESTEPDEFN "
                "WHERE UPPER(PCMPROG) LIKE :pat OR UPPER(STMTTEXT) LIKE :pat",
                {"pat": f"%{target}%"},
                max_rows=100,
            )
            results.append(f"\n### PSAESTEPDEFN ({len(ae_rows)} ocorrências):")
            for r in ae_rows[:30]:
                results.append(f"  {r['AE_APPLID']}.{r['AE_SECTION']}.{r['AESTEPNUM']} type={r['STMTTYPE']}")

        except ConnectionError as e:
            results.append(str(e))

        return [types.TextContent(type="text", text="\n".join(results))]

    # ── Tool 5: run_safe_query ─────────────────────────────────────────────────
    elif name == "run_safe_query":
        sql      = arguments["sql"].strip()
        max_rows = int(arguments.get("max_rows", 100))

        # Bloqueia DML
        dml_pattern = re.compile(
            r"\b(INSERT|UPDATE|DELETE|DROP|TRUNCATE|ALTER|CREATE|MERGE|EXEC|EXECUTE)\b",
            re.IGNORECASE,
        )
        if dml_pattern.search(sql):
            return [types.TextContent(type="text", text="❌ DML não permitido. Apenas SELECT é aceito.")]

        # Verifica tabelas na whitelist
        tables_used = re.findall(r"\bFROM\s+(\w+)|\bJOIN\s+(\w+)", sql, re.IGNORECASE)
        flat_tables = {t.upper() for pair in tables_used for t in pair if t}
        blocked = flat_tables - ALLOWED_TABLES
        if blocked:
            return [types.TextContent(
                type="text",
                text=f"❌ Tabelas não autorizadas: {', '.join(blocked)}.\n"
                     f"Permitidas: {', '.join(sorted(ALLOWED_TABLES))}",
            )]

        try:
            rows = oracle_query(sql, max_rows=max_rows)
            if not rows:
                return [types.TextContent(type="text", text="Consulta retornou 0 linhas.")]

            # Formata como tabela
            cols  = list(rows[0].keys())
            widths = {c: max(len(c), max(len(str(r.get(c,""))) for r in rows)) for c in cols}
            header = " | ".join(c.ljust(widths[c]) for c in cols)
            sep    = "-+-".join("-" * widths[c] for c in cols)
            lines  = [header, sep]
            for r in rows:
                lines.append(" | ".join(str(r.get(c,"")).ljust(widths[c]) for c in cols))
            lines.append(f"\n({len(rows)} linhas)")
            return [types.TextContent(type="text", text="\n".join(lines))]

        except ConnectionError as e:
            return [types.TextContent(type="text", text=str(e))]

    # ── Tool 6: suggest_change ─────────────────────────────────────────────────
    elif name == "suggest_change":
        target      = arguments["target"].upper()
        change_desc = arguments["change_desc"]
        change_type = arguments["change_type"]

        lines = [
            f"# Análise de Impacto: {change_type} em {target}",
            f"**Mudança proposta:** {change_desc}\n",
            "## 1. Referências encontradas",
        ]

        try:
            # PeopleCode references
            pc = oracle_query(
                "SELECT OBJECTVALUE1, OBJECTVALUE2, OBJECTVALUE3 FROM PSPCMPROG "
                "WHERE UPPER(PCMPTEXT) LIKE :p",
                {"p": f"%{target}%"}, max_rows=100,
            )
            lines.append(f"- **PeopleCode**: {len(pc)} programas referenciando `{target}`")

            # AE Steps
            ae = oracle_query(
                "SELECT AE_APPLID, AE_SECTION, AESTEPNUM FROM PSAESTEPDEFN "
                "WHERE UPPER(STMTTEXT) LIKE :p OR UPPER(PCMPROG) LIKE :p",
                {"p": f"%{target}%"}, max_rows=100,
            )
            lines.append(f"- **AE Steps**: {len(ae)} steps referenciando `{target}`")

            # Record fields
            rf = oracle_query(
                "SELECT RECNAME FROM PSRECFIELD WHERE RECNAME=:t OR FIELDNAME=:t",
                {"t": target}, max_rows=100,
            )
            lines.append(f"- **PSRECFIELD**: {len(rf)} records/fields com `{target}`")

        except ConnectionError as e:
            lines.append(f"⚠ Não foi possível consultar o Oracle: {e}")
            pc, ae, rf = [], [], []

        lines += [
            "\n## 2. Passos sugeridos de implementação",
        ]

        steps_map = {
            "field_add": [
                f"1. Adicionar field `{target}` ao record via App Designer.",
                "2. Executar build da tabela (alter table).",
                f"3. Atualizar PeopleCode relevante: {len(pc)} programas a revisar.",
                "4. Verificar pages e componentes que exibem o record.",
                "5. Atualizar Data Mover scripts se necessário.",
                "6. Testar em ambiente de desenvolvimento antes de migrar.",
            ],
            "field_modify": [
                f"1. Avaliar impacto em {len(pc)} programas PeopleCode.",
                f"2. Revisar {len(ae)} AE steps para compatibilidade.",
                "3. Modificar o field no App Designer com cuidado ao type/length.",
                "4. Executar build ALTER TABLE.",
                "5. Atualizar traduções/labels se aplicável.",
                "6. Testar formulários e processos batch.",
            ],
            "record_add": [
                f"1. Criar record `{target}` no App Designer.",
                "2. Definir keys e fields necessários.",
                "3. Executar build CREATE TABLE.",
                "4. Criar Data Mover script para dados iniciais.",
                "5. Adicionar permissões de segurança.",
                "6. Documentar no README do projeto.",
            ],
            "peoplecode": [
                f"1. Abrir PeopleCode de `{target}` no App Designer.",
                f"2. Aplicar mudança: {change_desc}",
                "3. Compilar e validar sem erros.",
                f"4. Revisar {len(pc)} outros programas relacionados.",
                "5. Testar evento acionador manualmente.",
                "6. Migrar via project em ambiente de QA.",
            ],
            "ae_step": [
                f"1. Abrir Application Engine `{target}` no App Designer.",
                f"2. Localizar e modificar step: {change_desc}",
                f"3. Revisar {len(ae)} steps relacionados.",
                "4. Executar AE em modo de trace para validar.",
                "5. Verificar PSAEAPPLSTATE para reinicialização correta.",
                "6. Migrar e testar em QA.",
            ],
        }

        for step in steps_map.get(change_type, ["Tipo de mudança não mapeado."]):
            lines.append(step)

        lines += [
            "\n## 3. Riscos",
            f"- Total de dependências identificadas: {len(pc)+len(ae)+len(rf)}",
            "- Recomendado: backup completo antes de executar em produção.",
            "- Validar com equipe de QA após cada etapa.",
        ]

        return [types.TextContent(type="text", text="\n".join(lines))]

    # ── Tool 7: get_workflow_def ───────────────────────────────────────────────
    elif name == "get_workflow_def":
        bp       = arguments["business_process"].upper()
        activity = arguments.get("activity", "").upper()

        try:
            act_sql = (
                "SELECT BUSPROCNAME, ACTIVITYNAME, DESCR, STATUS "
                "FROM PSACTIVITY WHERE BUSPROCNAME=:bp"
            )
            params: dict = {"bp": bp}
            if activity:
                act_sql += " AND ACTIVITYNAME=:act"
                params["act"] = activity

            activities = oracle_query(act_sql, params, max_rows=50)

            lines = [f"## Workflow: {bp}", f"Activities encontradas: {len(activities)}\n"]
            for a in activities:
                lines.append(f"### Activity: {a['ACTIVITYNAME']}")
                lines.append(f"  Descrição: {a.get('DESCR','')}")
                lines.append(f"  Status   : {a.get('STATUS','')}")

                routes = oracle_query(
                    "SELECT ROUTEDEFN, ROUTETYPE, DESCR FROM PSROUTE "
                    "WHERE BUSPROCNAME=:bp AND ACTIVITYNAME=:act",
                    {"bp": bp, "act": a["ACTIVITYNAME"]},
                )
                if routes:
                    lines.append("  Routes:")
                    for r in routes:
                        lines.append(f"    - {r['ROUTEDEFN']} ({r.get('ROUTETYPE','')}) {r.get('DESCR','')}")
                lines.append("")

            return [types.TextContent(type="text", text="\n".join(lines))]

        except ConnectionError as e:
            return [types.TextContent(type="text", text=str(e))]

    # ── Tool 8: get_ib_service ─────────────────────────────────────────────────
    elif name == "get_ib_service":
        service_name = arguments["service_name"].upper()
        node_name    = arguments.get("node_name", "").upper()

        try:
            svc = oracle_query(
                "SELECT IBSERVICENAME, DESCR, SERVICETYPE, CONTVERSIONNUMBER "
                "FROM PSIBSVCSETUP WHERE IBSERVICENAME=:s",
                {"s": service_name},
            )
            lines = [f"## Integration Broker Service: {service_name}"]
            if svc:
                lines.append(f"Descrição : {svc[0].get('DESCR','')}")
                lines.append(f"Tipo      : {svc[0].get('SERVICETYPE','')}")
                lines.append(f"Versão    : {svc[0].get('CONTVERSIONNUMBER','')}")
            else:
                lines.append("Serviço não encontrado em PSIBSVCSETUP.")

            # Subscriptions
            subs_sql = "SELECT IBSUBNAME, SUBTYPE, DESCR FROM PSIBSUBDEFN WHERE IBSERVICENAME=:s"
            subs = oracle_query(subs_sql, {"s": service_name})
            lines.append(f"\n### Subscriptions ({len(subs)}):")
            for s in subs:
                lines.append(f"  - {s['IBSUBNAME']} ({s.get('SUBTYPE','')}) {s.get('DESCR','')}")

            # Nodes
            node_sql = "SELECT MSGNODENAME, DESCR, DEFAULTMSGNODE FROM PSMSGNODEDEFN"
            if node_name:
                node_sql += " WHERE MSGNODENAME=:n"
                nodes = oracle_query(node_sql, {"n": node_name})
            else:
                nodes = oracle_query(node_sql, max_rows=20)
            lines.append(f"\n### Nodes ({len(nodes)}):")
            for n in nodes:
                lines.append(f"  - {n['MSGNODENAME']} {n.get('DESCR','')} default={n.get('DEFAULTMSGNODE','')}")

            return [types.TextContent(type="text", text="\n".join(lines))]

        except ConnectionError as e:
            return [types.TextContent(type="text", text=str(e))]

    # ── Tool 9: export_knowledge ───────────────────────────────────────────────
    elif name == "export_knowledge":
        topic  = arguments["topic"]
        fmt    = arguments.get("format", "markdown")

        conn = get_sqlite_conn()
        meta_rows = conn.execute(
            "SELECT recname, payload, cached_at FROM metadata_cache WHERE UPPER(recname) LIKE UPPER(?)",
            (f"%{topic}%",),
        ).fetchall()

        trace_rows = conn.execute(
            "SELECT trace_file, section, step, elapsed, content FROM trace_chunks "
            "WHERE UPPER(content) LIKE UPPER(?) LIMIT 50",
            (f"%{topic}%",),
        ).fetchall()

        notes_rows = conn.execute(
            "SELECT topic, category, content, created_at FROM knowledge_notes "
            "WHERE UPPER(topic) LIKE UPPER(?) LIMIT 50",
            (f"%{topic}%",),
        ).fetchall()
        conn.close()

        timestamp = datetime.datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        safe_topic = re.sub(r"[^\w]", "_", topic)
        filename   = EXPORTS_DIR / f"{safe_topic}_{timestamp}.{('md' if fmt=='markdown' else 'json')}"

        if fmt == "json":
            export_data = {
                "topic": topic,
                "exported_at": timestamp,
                "metadata_cache": [dict(r) for r in meta_rows],
                "trace_chunks":   [dict(r) for r in trace_rows],
                "notes":          [dict(r) for r in notes_rows],
            }
            filename.write_text(json.dumps(export_data, indent=2, default=str), encoding="utf-8")
        else:
            lines = [
                f"# Exportação de Conhecimento: {topic}",
                f"_Exportado em: {timestamp}_\n",
                f"## Metadados Cacheados ({len(meta_rows)} records)",
            ]
            for r in meta_rows:
                lines.append(f"\n### {r['recname']}")
                lines.append(r["payload"])

            lines.append(f"\n## Trace Chunks ({len(trace_rows)} trechos)")
            for r in trace_rows:
                lines.append(f"\n**{r['trace_file']}** | {r['section']} / {r['step']} | {r['elapsed']:.3f}s")
                lines.append(f"```\n{r['content'][:500]}\n```")

            lines.append(f"\n## Notas ({len(notes_rows)})")
            for r in notes_rows:
                lines.append(f"\n**{r['topic']}** [{r.get('category','')}] _{r.get('created_at','')}_")
                lines.append(r["content"])

            filename.write_text("\n".join(lines), encoding="utf-8")

        return [types.TextContent(
            type="text",
            text=f"✅ Exportado: {filename}\n"
                 f"  {len(meta_rows)} records | {len(trace_rows)} trace chunks | {len(notes_rows)} notas",
        )]

    else:
        return [types.TextContent(type="text", text=f"Ferramenta desconhecida: {name}")]


# ══════════════════════════════════════════════════════════════════════════════
# ENTRYPOINT
# ══════════════════════════════════════════════════════════════════════════════
async def main():
    async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
