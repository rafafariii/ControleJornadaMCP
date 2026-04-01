"""
tools/sentry.py — PeopleSoft Sentry: Monitoramento de Produção + Base de SOPs
==============================================================================
Inspirado no projeto peoplesoft_sentry (CAG + AIOps Diagnostic Engine).

Bloco D — 5 ferramentas de produção:

  ps_get_ib_errors        → Erros recentes no Integration Broker (PS_MSG_INST)
  ps_get_process_errors   → Processos com falha no Process Monitor (PSPRCSRQST)
  ps_get_system_summary   → Resumo de saúde do ambiente (IB + Processos)
  ps_health_check         → Diagnóstico completo + match automático de SOP
  ps_lookup_sop           → Busca SOP na base de conhecimento por texto de erro

Técnica CAG (Cache-Augmented Generation):
  As SOPs ficam em memória — lookup em O(1), sem latência de banco.
  Estendida com SOPs específicos do Itaú (T&L, Jornada Mista, TL_TA).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

from db import execute_query, execute_query_with_limit


# ===========================================================================
# SOP Library — Cache-Augmented Generation (CAG)
# ===========================================================================

@dataclass
class SOP:
    key: str
    title: str
    root_cause: str
    symptoms: list[str]
    resolution: list[str]
    escalate_to: str
    tags: list[str] = field(default_factory=list)


_SOP_LIBRARY: list[SOP] = [

    # ── Oracle / Banco ────────────────────────────────────────────────────
    SOP(
        key="ora-01555",
        title="ORA-01555: Snapshot Too Old",
        root_cause=(
            "O segmento de undo/rollback do Oracle é insuficiente ou o parâmetro "
            "UNDO_RETENTION está muito baixo, causando perda de consistência de leitura "
            "em queries de longa duração."
        ),
        symptoms=[
            "PSPRCSRQST mostra RUNSTATUS=14 (Error) para jobs Application Engine",
            "Log de mensagem contém 'ORA-01555 snapshot too old'",
            "Ocorre tipicamente em fechamentos de período ou cargas de dados massivas",
        ],
        resolution=[
            "1. Verificar UNDO_RETENTION (recomendado ≥ 3600 s):",
            "   SELECT VALUE FROM V$PARAMETER WHERE NAME='undo_retention';",
            "2. Aumentar UNDO_RETENTION:",
            "   ALTER SYSTEM SET UNDO_RETENTION=7200 SCOPE=BOTH;",
            "3. Verificar tamanho do tablespace UNDO e adicionar datafile se < 10 GB livre:",
            "   ALTER TABLESPACE UNDOTBS1 ADD DATAFILE SIZE 4G AUTOEXTEND ON;",
            "4. Re-executar o processo pelo Process Monitor (Actions → Restart).",
            "5. Agendar jobs de grande volume fora do horário de pico.",
        ],
        escalate_to="Time DBA — Fila: ORA-DB-PERF",
        tags=["oracle", "undo", "ae", "batch"],
    ),

    SOP(
        key="ora-04031",
        title="ORA-04031: Unable to Allocate Shared Memory",
        root_cause=(
            "O Shared Pool do Oracle está esgotado. Causado por excesso de cursors não "
            "reutilizáveis (SQL não parametrizado) ou configuração insuficiente do SGA."
        ),
        symptoms=[
            "Erros ORA-04031 em logs do Application Server (PSAPPSRV)",
            "Performance degradada nas páginas PeopleSoft",
            "Alert log Oracle com 'shared pool' ou 'large pool'",
        ],
        resolution=[
            "1. Verificar uso do Shared Pool:",
            "   SELECT pool, name, bytes/1024/1024 mb FROM V$SGASTAT WHERE pool='shared pool' ORDER BY bytes DESC;",
            "2. Liberar cursors não utilizados: ALTER SYSTEM FLUSH SHARED_POOL;",
            "3. Aumentar SHARED_POOL_SIZE se necessário (mínimo 1 GB para PeopleSoft).",
            "4. Verificar se há SQL sem bind variables (cursor explosion):",
            "   SELECT sql_text, executions FROM V$SQLAREA WHERE executions=1 AND LENGTH(sql_text)>100 AND ROWNUM<=20;",
            "5. Aplicar cursor_sharing=FORCE como medida emergencial.",
        ],
        escalate_to="Time DBA — Fila: ORA-DB-MEMORY",
        tags=["oracle", "memory", "shared pool", "sga"],
    ),

    # ── Integration Broker ────────────────────────────────────────────────
    SOP(
        key="ib-connection-refused",
        title="IB Error: Nó Destino Recusou Conexão",
        root_cause=(
            "O Integration Broker não consegue estabelecer conexão TCP/HTTP com o nó "
            "destino (subscriber). O endpoint remoto está fora do ar, bloqueado por "
            "firewall, ou a Gateway URL está mal configurada."
        ),
        symptoms=[
            "PS_MSG_INST com MSG_STATUS=7 e 'Connection refused' em ERROR_MSG",
            "Múltiplas mensagens enfileiradas para o mesmo sub-node",
            "IB Monitor mostrando falhas de ping para o nó",
        ],
        resolution=[
            "1. Testar conectividade ao nó destino a partir do App Server:",
            "   curl -v https://<target-node-url>/PSIGW/PeopleSoftServiceListeningConnector",
            "2. Verificar a Gateway URL (PeopleTools → Integration Broker → Gateways).",
            "3. Confirmar que o nó destino está Ativo (Node Definitions → Status = Active).",
            "4. Verificar regras de firewall entre os VLANs de origem e destino.",
            "5. Reiniciar o Integration Gateway (managed server WebLogic) se necessário.",
            "6. Resubmeter transações com erro via IB Monitor → Service Operations.",
        ],
        escalate_to="Time Middleware/Integração — Fila: IB-CONNECT",
        tags=["ib", "integration broker", "node", "connectivity"],
    ),

    SOP(
        key="ib-timeout",
        title="IB Error: Timeout no Nó Destino",
        root_cause=(
            "O nó subscriber não respondeu dentro da janela de timeout configurada. "
            "Causas: sistema destino lento, payload grande ou latência de rede."
        ),
        symptoms=[
            "ERROR_MSG contém 'Timeout' ou 'No response'",
            "MSG_STATUS=7 em linhas da PS_MSG_INST",
            "Falhas esporádicas (não uma interrupção completa)",
        ],
        resolution=[
            "1. Verificar tempo de resposta do nó destino — teste de ping pelo IB Monitor.",
            "2. Aumentar timeout da Gateway (Gateway Properties → Connector timeout).",
            "3. Analisar tamanho do payload — habilitar chunking para mensagens > 5 MB.",
            "4. Revisar métricas de performance do sistema destino na janela de falha.",
            "5. Resubmeter mensagens com falha via IB Monitor após corrigir a causa raiz.",
        ],
        escalate_to="Time Integração — Fila: IB-PERF",
        tags=["ib", "timeout", "performance"],
    ),

    # ── Folha de Pagamento ────────────────────────────────────────────────
    SOP(
        key="pychkusa-company-not-found",
        title="PYCHKUSA – Company Not Found",
        root_cause=(
            "O Pay Run ID referencia uma Empresa que não existe ou está inativa em "
            "PS_COMPANY_TBL, ou o Run Control foi configurado com parâmetros incorretos."
        ),
        symptoms=[
            "PSPRCSRQST RUNSTATUS=14 para processo PYCHKUSA",
            "Mensagem: 'Company not found for Pay Run ID'",
            "Administradores de folha incapazes de confirmar impressão de contracheque",
        ],
        resolution=[
            "1. Verificar o código da Empresa no Run Control:",
            "   SELECT * FROM PS_RC_PAY WHERE OPRID=:oprid AND RUN_CNTL_ID=:runcntl;",
            "2. Confirmar que a Empresa está ativa:",
            "   SELECT EFFDT, EFF_STATUS FROM PS_COMPANY_TBL WHERE COMPANY=:company ORDER BY EFFDT DESC;",
            "3. Se inativa, reativar via Set Up HCM → Foundation Tables → Company.",
            "4. Corrigir o Run Control e re-executar PYCHKUSA pelo Process Monitor.",
            "5. Notificar o Gerente de Folha antes do re-run para confirmar os detalhes do ciclo.",
        ],
        escalate_to="Time Funcional Folha/HCM — Fila: PAY-CONFIG",
        tags=["payroll", "pychkusa", "company", "hcm"],
    ),

    # ── Time & Labor (Itaú-específico) ────────────────────────────────────
    SOP(
        key="tl-ta-failure",
        title="TL_TA: Time Administration Abortou",
        root_cause=(
            "O Application Engine TL_TA (Time Administration) falhou durante o "
            "processamento de julgamento de ponto. Causas comuns: SQL inválido em step "
            "de regra, falta de dados de calendário ou overflow de TRC não mapeado."
        ),
        symptoms=[
            "PSPRCSRQST RUNSTATUS=14 para processo TL_TA ou TL_TIMEADMIN",
            "PS_TL_IPT sem linhas geradas para o grupo/período esperado",
            "Log de AE com 'Step failed' ou 'SQL Error' em steps TL_TA.*",
        ],
        resolution=[
            "1. Identificar o step com falha pelo log de AE:",
            "   SELECT * FROM PSPRCSRQST WHERE PRCSNAME='TL_TA' AND RUNSTATUS='14' ORDER BY BEGINDTTM DESC;",
            "2. Verificar o SQL do step suspeito com a ferramenta tl_get_rule_step_sql.",
            "3. Checar se o calendário de trabalho está configurado para o período:",
            "   SELECT * FROM PS_TL_WORK_SCHEDL WHERE EMPLID=:emplid ORDER BY EFFDT DESC;",
            "4. Verificar se todos os TRCs usados nas fórmulas existem em PS_TL_TRC_TBL.",
            "5. Rodar diagnóstico de cobertura do grupo: ferramenta tl_group_coverage_report.",
            "6. Reprocessar pelo Process Monitor após corrigir o step problemático.",
        ],
        escalate_to="Time T&L Itaú — Fila: TL-PROCESSAMENTO",
        tags=["tl", "time and labor", "tl_ta", "ae", "jornada"],
    ),

    SOP(
        key="tl-jornada-mista-he-incorreta",
        title="T&L: Jornada Mista Gerando HE Indevida (Grupo SEMESTRAL)",
        root_cause=(
            "Um ou mais steps de regra de ponto do grupo SEMESTRAL calculam Horas Extras "
            "(HE) sem verificar se o funcionário está em jornada mista (turno que cruza "
            "as 22h). Nesses casos, apenas o Adicional Noturno (AN) deveria ser gerado."
        ),
        symptoms=[
            "PS_TL_IPT com TRC de HE (ex: HENOTURNA, HE_50) para empregados em jornada mista",
            "Relatório de folha mostrando HE + AN no mesmo dia para funcionários SEMESTRAL",
            "tl_detect_mixed_shift_bug retorna steps com flag he=True E an=True",
        ],
        resolution=[
            "1. Executar diagnóstico: ferramenta tl_detect_mixed_shift_bug no grupo SEMESTRAL.",
            "2. Para cada step identificado, verificar o SQL completo com tl_get_rule_step_sql.",
            "3. Gerar proposta de correção com tl_generate_fix_proposal — adiciona bloco NOT EXISTS",
            "   que exclui empregados com punch antes E depois das 22h no mesmo dia.",
            "4. Validar a correção em ambiente de homologação com empregados de teste.",
            "5. Aplicar o script DMS gerado em produção via Data Mover com aprovação formal.",
            "6. Reprocessar TL_TA para o período afetado e verificar PS_TL_IPT.",
        ],
        escalate_to="Time T&L Itaú / Arquitetura PeopleSoft — Fila: TL-JORNADA-MISTA",
        tags=["tl", "jornada mista", "semestral", "he", "adicional noturno", "itau"],
    ),

    SOP(
        key="tl-ipt-sem-dados",
        title="T&L: PS_TL_IPT Sem Dados para Funcionário/Período",
        root_cause=(
            "A tabela PS_TL_IPT não possui registros para o funcionário e período "
            "esperado. Causas: TL_TA não foi rodado, funcionário não pertence ao grupo "
            "de julgamento, calendário ausente ou punch não registrado."
        ),
        symptoms=[
            "tl_get_employee_ipt retorna lista vazia para emplid e período válidos",
            "Relatório de ponto em branco para o funcionário",
            "Funcionário aparece no grupo mas sem processamento visível",
        ],
        resolution=[
            "1. Confirmar que o TL_TA foi executado para o período:",
            "   SELECT * FROM PSPRCSRQST WHERE PRCSNAME='TL_TA' AND RUNSTATUS='9' ORDER BY ENDDTTM DESC;",
            "2. Verificar se o funcionário pertence ao grupo SEMESTRAL:",
            "   SELECT * FROM PS_TL_EMPL_GROUP WHERE EMPLID=:emplid AND TL_GROUP_ID='SEMESTRAL';",
            "3. Checar existência de punches no período:",
            "   SELECT * FROM PS_TL_PUNCH_TBL WHERE EMPLID=:emplid AND PUNCH_DTTM BETWEEN :inicio AND :fim;",
            "4. Verificar configuração do calendário de trabalho do funcionário.",
            "5. Se tudo estiver correto, re-executar TL_TA para o funcionário/período.",
        ],
        escalate_to="Time T&L Itaú — Fila: TL-IPT-AUSENTE",
        tags=["tl", "ipt", "ps_tl_ipt", "processamento", "funcionario"],
    ),

    # ── Processo Genérico ─────────────────────────────────────────────────
    SOP(
        key="generic-process-error",
        title="Erro Genérico no Process Monitor",
        root_cause="Processo encerrou de forma anormal — revisar o log de mensagem para códigos ORA- ou ABN: específicos.",
        symptoms=[
            "RUNSTATUS=14 em PSPRCSRQST",
            "Nenhum padrão de erro específico identificado",
        ],
        resolution=[
            "1. Abrir o Process Monitor e clicar na instância com falha.",
            "2. Clicar em 'Message Log' para revisar o output detalhado do erro.",
            "3. Verificar o log do servidor: $PS_LOGDIR/<server>/<process>_<instance>.log",
            "4. Buscar na base de SOPs pelo código de erro específico (ferramenta ps_lookup_sop).",
            "5. Escalar para o Suporte Técnico com o arquivo de log anexado.",
        ],
        escalate_to="Suporte Técnico PeopleSoft — Fila: PSFT-GENERAL",
        tags=["generic", "process"],
    ),
]

# Índice por chave para lookup O(1)
_INDEX: dict[str, SOP] = {sop.key: sop for sop in _SOP_LIBRARY}

# Padrões de matching (prioridade top-down)
_PATTERNS: list[tuple[str, str]] = [
    (r"ora-01555|snapshot too old",                     "ora-01555"),
    (r"ora-04031|shared pool|shared memory",            "ora-04031"),
    (r"connection refused",                             "ib-connection-refused"),
    (r"timeout|no response",                            "ib-timeout"),
    (r"company not found|pychkusa",                     "pychkusa-company-not-found"),
    (r"tl_ta|time administration|time admin",           "tl-ta-failure"),
    (r"jornada mista|mixed shift|he indev|he.*an.*semestral", "tl-jornada-mista-he-incorreta"),
    (r"tl_ipt.*vaz|ipt.*sem dado|ps_tl_ipt.*empty",    "tl-ipt-sem-dados"),
]


def _lookup_sop(error_text: str) -> Optional[SOP]:
    """Retorna o SOP mais relevante para o texto de erro dado."""
    if not error_text:
        return None
    lower = error_text.lower()
    for pattern, key in _PATTERNS:
        if re.search(pattern, lower):
            return _INDEX.get(key)
    return _INDEX.get("generic-process-error")


def _format_sop(sop: SOP) -> dict:
    return {
        "key":          sop.key,
        "title":        sop.title,
        "root_cause":   sop.root_cause,
        "symptoms":     sop.symptoms,
        "resolution":   sop.resolution,
        "escalate_to":  sop.escalate_to,
        "tags":         sop.tags,
    }


def get_all_sops_as_text() -> str:
    """
    Serializa toda a biblioteca de SOPs em texto estruturado para inclusão
    em system prompt (técnica CAG — zero latência de retrieval).
    """
    blocks = []
    for sop in _SOP_LIBRARY:
        lines = [
            f"## SOP: {sop.title}",
            f"**Causa Raiz:** {sop.root_cause}",
            "**Sintomas:**",
            *[f"  - {s}" for s in sop.symptoms],
            "**Passos de Resolução:**",
            *[f"  {r}" for r in sop.resolution],
            f"**Escalação:** {sop.escalate_to}",
        ]
        blocks.append("\n".join(lines))
    return "\n\n---\n\n".join(blocks)


# ===========================================================================
# FastMCP Tool Registration
# ===========================================================================

def register_tools(mcp) -> None:  # noqa: ANN001
    """Registra as 5 ferramentas Sentry no servidor FastMCP."""

    # ── 1. ps_get_ib_errors ─────────────────────────────────────────────

    @mcp.tool()
    async def ps_get_ib_errors(hours_back: int = 24) -> str:
        """
        Busca erros recentes no Integration Broker (PS_MSG_INST com MSG_STATUS=7).

        Retorna: transaction_id, message_name, queue, pub_node, sub_node,
                 timestamp, error_detail e SOP recomendado (quando disponível).

        Args:
            hours_back: Janela de busca em horas (padrão: 24).
        """
        cutoff = (datetime.utcnow() - timedelta(hours=hours_back)).strftime(
            "%Y-%m-%d %H:%M:%S"
        )

        rows = await execute_query(
            """
            SELECT IB_TRANSACTIONID,
                   MESSAGE_NAME,
                   QUEUE_NAME,
                   PUBNODE,
                   SUBNODE,
                   TO_CHAR(DTTM_STAMP_SEC, 'YYYY-MM-DD HH24:MI:SS') AS TS,
                   ERROR_MSG
              FROM PS.PS_MSG_INST
             WHERE MSG_STATUS = '7'
               AND DTTM_STAMP_SEC >= TO_DATE(:cutoff, 'YYYY-MM-DD HH24:MI:SS')
             ORDER BY DTTM_STAMP_SEC DESC
            """,
            {"cutoff": cutoff},
        )

        errors = []
        for r in rows:
            err_text = r[6] or ""
            sop = _lookup_sop(err_text)
            errors.append({
                "transaction_id": r[0],
                "message_name":   r[1],
                "queue":          r[2],
                "pub_node":       r[3],
                "sub_node":       r[4],
                "timestamp":      r[5],
                "error_detail":   err_text,
                "sop_match":      sop.key if sop else None,
                "sop_title":      sop.title if sop else None,
            })

        return json.dumps(
            {"tool": "ps_get_ib_errors", "hours_back": hours_back, "count": len(errors), "errors": errors},
            ensure_ascii=False, indent=2,
        )

    # ── 2. ps_get_process_errors ────────────────────────────────────────

    @mcp.tool()
    async def ps_get_process_errors(hours_back: int = 24) -> str:
        """
        Busca processos com falha no Process Monitor (PSPRCSRQST com RUNSTATUS=14).

        Retorna: process_instance, type, name, operator, run_control,
                 begin/end dttm, server, error_text e SOP recomendado.

        Args:
            hours_back: Janela de busca em horas (padrão: 24).
        """
        cutoff = (datetime.utcnow() - timedelta(hours=hours_back)).strftime(
            "%Y-%m-%d %H:%M:%S"
        )

        rows = await execute_query(
            """
            SELECT PRCSINSTANCE,
                   PRCSTYPE,
                   PRCSNAME,
                   OPRID,
                   RUNCNTLID,
                   TO_CHAR(BEGINDTTM, 'YYYY-MM-DD HH24:MI:SS') AS BDT,
                   TO_CHAR(ENDDTTM,   'YYYY-MM-DD HH24:MI:SS') AS EDT,
                   SERVERNM,
                   MESSAGE_TEXT
              FROM PSPRCSRQST
             WHERE RUNSTATUS = '14'
               AND BEGINDTTM >= TO_DATE(:cutoff, 'YYYY-MM-DD HH24:MI:SS')
             ORDER BY BEGINDTTM DESC
            """,
            {"cutoff": cutoff},
        )

        errors = []
        for r in rows:
            err_text = r[8] or ""
            sop = _lookup_sop(err_text)
            errors.append({
                "process_instance": r[0],
                "process_type":     r[1],
                "process_name":     r[2],
                "operator":         r[3],
                "run_control":      r[4],
                "begin_dttm":       r[5],
                "end_dttm":         r[6],
                "server":           r[7],
                "error_text":       err_text,
                "sop_match":        sop.key if sop else None,
                "sop_title":        sop.title if sop else None,
            })

        return json.dumps(
            {"tool": "ps_get_process_errors", "hours_back": hours_back, "count": len(errors), "errors": errors},
            ensure_ascii=False, indent=2,
        )

    # ── 3. ps_get_system_summary ────────────────────────────────────────

    @mcp.tool()
    async def ps_get_system_summary() -> str:
        """
        Retorna um resumo de saúde do ambiente PeopleSoft:
        contagem de erros IB, erros de processo, processos em execução e
        health score geral (HEALTHY / DEGRADED / CRITICAL).
        """
        ib_errors_rows = await execute_query(
            "SELECT COUNT(*) FROM PS.PS_MSG_INST WHERE MSG_STATUS = '7'", {}
        )
        ib_total_rows = await execute_query(
            "SELECT COUNT(*) FROM PS.PS_MSG_INST", {}
        )
        proc_errors_rows = await execute_query(
            "SELECT COUNT(*) FROM PSPRCSRQST WHERE RUNSTATUS = '14'", {}
        )
        proc_running_rows = await execute_query(
            "SELECT COUNT(*) FROM PSPRCSRQST WHERE RUNSTATUS = '7'", {}
        )
        proc_total_rows = await execute_query(
            "SELECT COUNT(*) FROM PSPRCSRQST", {}
        )

        ib_errors   = ib_errors_rows[0][0]   if ib_errors_rows   else 0
        ib_total    = ib_total_rows[0][0]     if ib_total_rows     else 0
        proc_errors = proc_errors_rows[0][0]  if proc_errors_rows  else 0
        proc_run    = proc_running_rows[0][0] if proc_running_rows else 0
        proc_total  = proc_total_rows[0][0]   if proc_total_rows   else 0

        total_errors = (ib_errors or 0) + (proc_errors or 0)
        if total_errors == 0:
            health = "HEALTHY"
        elif total_errors <= 5:
            health = "DEGRADED"
        else:
            health = "CRITICAL"

        summary = {
            "tool":                    "ps_get_system_summary",
            "timestamp_utc":           datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
            "ib_total_messages":       ib_total,
            "ib_error_count":          ib_errors,
            "process_total":           proc_total,
            "process_error_count":     proc_errors,
            "process_running_count":   proc_run,
            "overall_health":          health,
        }

        return json.dumps(summary, ensure_ascii=False, indent=2)

    # ── 4. ps_health_check ──────────────────────────────────────────────

    @mcp.tool()
    async def ps_health_check(hours_back: int = 6) -> str:
        """
        Diagnóstico completo do ambiente PeopleSoft.

        Combina IB, Process Monitor e base de SOPs para retornar um relatório
        estruturado com: resumo de saúde, lista de incidentes ativos e
        recomendações de ação para cada falha identificada.

        Args:
            hours_back: Janela de análise em horas (padrão: 6).
        """
        cutoff = (datetime.utcnow() - timedelta(hours=hours_back)).strftime(
            "%Y-%m-%d %H:%M:%S"
        )

        # Busca IB errors
        ib_rows = await execute_query(
            """
            SELECT IB_TRANSACTIONID, MESSAGE_NAME, SUBNODE,
                   TO_CHAR(DTTM_STAMP_SEC, 'YYYY-MM-DD HH24:MI:SS'),
                   ERROR_MSG
              FROM PS.PS_MSG_INST
             WHERE MSG_STATUS = '7'
               AND DTTM_STAMP_SEC >= TO_DATE(:cutoff, 'YYYY-MM-DD HH24:MI:SS')
             ORDER BY DTTM_STAMP_SEC DESC
            """,
            {"cutoff": cutoff},
        )

        # Busca process errors
        proc_rows = await execute_query(
            """
            SELECT PRCSINSTANCE, PRCSNAME, OPRID,
                   TO_CHAR(BEGINDTTM, 'YYYY-MM-DD HH24:MI:SS'),
                   MESSAGE_TEXT
              FROM PSPRCSRQST
             WHERE RUNSTATUS = '14'
               AND BEGINDTTM >= TO_DATE(:cutoff, 'YYYY-MM-DD HH24:MI:SS')
             ORDER BY BEGINDTTM DESC
            """,
            {"cutoff": cutoff},
        )

        incidents = []

        for r in (ib_rows or []):
            err_text = r[4] or ""
            sop = _lookup_sop(err_text)
            incidents.append({
                "source":       "Integration Broker",
                "id":           r[0],
                "name":         r[1],
                "target":       r[2],
                "timestamp":    r[3],
                "error":        err_text[:200],
                "sop":          _format_sop(sop) if sop else None,
            })

        for r in (proc_rows or []):
            err_text = r[4] or ""
            sop = _lookup_sop(err_text)
            incidents.append({
                "source":       "Process Monitor",
                "id":           str(r[0]),
                "name":         r[1],
                "operator":     r[2],
                "timestamp":    r[3],
                "error":        err_text[:200],
                "sop":          _format_sop(sop) if sop else None,
            })

        total_errors = len(incidents)
        health = "HEALTHY" if total_errors == 0 else ("DEGRADED" if total_errors <= 5 else "CRITICAL")

        report = {
            "tool":           "ps_health_check",
            "hours_back":     hours_back,
            "timestamp_utc":  datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
            "overall_health": health,
            "total_incidents": total_errors,
            "incidents":      incidents,
        }

        return json.dumps(report, ensure_ascii=False, indent=2)

    # ── 5. ps_lookup_sop ────────────────────────────────────────────────

    @mcp.tool()
    async def ps_lookup_sop(error_text: str, list_all: bool = False) -> str:
        """
        Busca um SOP (Standard Operating Procedure) na base de conhecimento
        PeopleSoft pelo texto do erro.

        Retorna o SOP mais relevante com causa raiz, passos de resolução
        e informação de escalação. Se list_all=True, retorna todos os SOPs
        disponíveis (útil para exploração da base).

        Args:
            error_text: Texto do erro ou palavra-chave (ex: 'ORA-01555', 'IB timeout').
            list_all:   Se True, lista todos os SOPs da base (ignora error_text).
        """
        if list_all:
            all_sops = [_format_sop(s) for s in _SOP_LIBRARY]
            return json.dumps(
                {"tool": "ps_lookup_sop", "mode": "list_all", "count": len(all_sops), "sops": all_sops},
                ensure_ascii=False, indent=2,
            )

        sop = _lookup_sop(error_text)
        if not sop:
            return json.dumps(
                {"tool": "ps_lookup_sop", "found": False, "query": error_text},
                ensure_ascii=False, indent=2,
            )

        return json.dumps(
            {"tool": "ps_lookup_sop", "found": True, "query": error_text, "sop": _format_sop(sop)},
            ensure_ascii=False, indent=2,
        )
