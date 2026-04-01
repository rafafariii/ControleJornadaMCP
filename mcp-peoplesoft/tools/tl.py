"""
Time & Labor (T&L) — Ferramentas específicas Itaú Unibanco
===========================================================
Ferramentas de análise do módulo Time & Labor do PeopleSoft,
customizadas para as regras de julgamento de ponto do Itaú.

Foco principal:
  - Jornada Mista (cruzamento diurno→noturno)
  - Grupo SEMESTRAL
  - Detecção de geração indevida de Horas Extras + Adicional Noturno
"""
from db import execute_query, execute_query_with_limit


def register_tools(mcp):
    """Registra todas as ferramentas T&L no servidor MCP."""

    # ──────────────────────────────────────────────────────────────
    # 1. Listar regras de um grupo T&L
    # ──────────────────────────────────────────────────────────────
    @mcp.tool()
    async def tl_list_group_rules(tl_group_id: str, rule_type: str | None = None) -> dict:
        """
        Lista todas as regras de julgamento de ponto associadas a um grupo T&L.

        Consulta PS_TL_GROUP_RULE + PS_TL_RULE_DEFN. Use para inventariar
        as fórmulas do grupo antes de fazer qualquer modificação.

        Args:
            tl_group_id: ID do grupo (ex: 'SEMESTRAL', 'MENSALISTA', 'HORISTA')
            rule_type:   Filtro opcional por tipo: 'DAY', 'PERIOD', 'WEEKLY', 'FLEX'

        Returns:
            Lista de regras com ID, nome, tipo e sequência de execução.
        """
        sql = """
            SELECT
                gr.TL_GROUP_ID,
                gr.TL_RULE_ID,
                rd.DESCR          AS RULE_NAME,
                rd.TL_RULE_TYPE,
                gr.EFFDT,
                gr.EFF_STATUS,
                gr.TL_RULE_SEQ
            FROM PS.PS_TL_GROUP_RULE gr
            JOIN PS.PS_TL_RULE_DEFN  rd ON rd.TL_RULE_ID = gr.TL_RULE_ID
            WHERE gr.TL_GROUP_ID = :1
        """
        params = [tl_group_id.upper()]

        if rule_type:
            sql += " AND rd.TL_RULE_TYPE = :2"
            params.append(rule_type.upper())

        sql += " ORDER BY gr.TL_RULE_SEQ, rd.TL_RULE_TYPE"

        result = await execute_query(sql, params)
        if "error" in result:
            return result
        if not result.get("results"):
            return {"error": f"Nenhuma regra encontrada para o grupo '{tl_group_id}'."}

        return {
            "tl_group_id":  tl_group_id.upper(),
            "total_rules":  len(result["results"]),
            "rules":        result["results"],
        }

    # ──────────────────────────────────────────────────────────────
    # 2. Obter SQL de um step de regra
    # ──────────────────────────────────────────────────────────────
    @mcp.tool()
    async def tl_get_rule_step_sql(tl_rule_id: str, step_num: int | None = None) -> dict:
        """
        Retorna o texto SQL completo de um ou mais steps de uma regra T&L.

        Consulta PS_TL_RULE_STEPS + PS_TL_STP_SQL_TBL. Essencial para
        entender o que cada fórmula faz antes de propor alterações.

        Args:
            tl_rule_id: ID da regra (ex: 'TL_DAY_STD_8H')
            step_num:   Número do step específico. Se omitido, retorna todos.

        Returns:
            Lista de steps com SQL completo, binds detectados e análise de impacto.
        """
        import re
        sql = """
            SELECT
                rs.TL_RULE_ID,
                rs.TL_STEP_NUM,
                rs.TL_STEP_NAME,
                rs.TL_STEP_TYPE,
                rs.TL_ACTION_TYPE,
                ss.TL_SQL_TEXT,
                ss.TL_SQL_NAME
            FROM PS.PS_TL_RULE_STEPS   rs
            LEFT JOIN PS.PS_TL_STP_SQL_TBL ss ON ss.TL_SQL_NAME = rs.TL_SQL_NAME
            WHERE rs.TL_RULE_ID = :1
        """
        params = [tl_rule_id.upper()]

        if step_num is not None:
            sql += " AND rs.TL_STEP_NUM = :2"
            params.append(step_num)

        sql += " ORDER BY rs.TL_STEP_NUM"

        result = await execute_query(sql, params)
        if "error" in result:
            return result
        if not result.get("results"):
            return {"error": f"Nenhum step encontrado para a regra '{tl_rule_id}'."}

        enriched = []
        for row in result["results"]:
            sql_text  = row.get("TL_SQL_TEXT") or ""
            binds     = list(set(re.findall(r":[A-Z_0-9]+", sql_text)))
            upper_sql = sql_text.upper()
            enriched.append({
                **{k: v for k, v in row.items() if k != "TL_SQL_TEXT"},
                "sql_text":       sql_text,
                "binds_detected": binds,
                "analysis": {
                    "has_overtime_reference": any(
                        kw in upper_sql for kw in ["OVERTIME", "OT_HRS", "HE_", "HORA_EXTRA"]
                    ),
                    "writes_to_ipt":  "PS_TL_IPT" in upper_sql,
                    "has_night_logic": any(
                        kw in upper_sql for kw in ["NOTURNO", "NIGHT", "22", "ADICNOTURNO"]
                    ),
                    "sql_length": len(sql_text),
                },
            })

        return {
            "tl_rule_id":   tl_rule_id.upper(),
            "total_steps":  len(enriched),
            "steps":        enriched,
        }

    # ──────────────────────────────────────────────────────────────
    # 3. Encontrar regras que geram Horas Extras
    # ──────────────────────────────────────────────────────────────
    @mcp.tool()
    async def tl_find_overtime_rules(
        tl_group_id: str | None = None,
        trc_pattern: str = "HE%",
    ) -> dict:
        """
        Varre PS_TL_STP_SQL_TBL para encontrar todos os steps que contêm
        lógica de horas extras (OVERTIME, OT_HRS, HE_*, etc.).

        Use para filtrar, entre as centenas de fórmulas, somente as que
        impactam a geração de HE e precisam ser analisadas/modificadas.

        Args:
            tl_group_id: Filtrar por grupo específico (opcional)
            trc_pattern: Padrão LIKE para TRC de hora extra (padrão: 'HE%')

        Returns:
            Lista de steps de risco com diagnóstico de impacto.
        """
        sql = """
            SELECT DISTINCT
                rs.TL_RULE_ID,
                rs.TL_STEP_NUM,
                rs.TL_STEP_NAME,
                rs.TL_ACTION_TYPE,
                ss.TL_SQL_NAME,
                SUBSTR(ss.TL_SQL_TEXT, 1, 500) AS SQL_EXCERPT
            FROM PS.PS_TL_STP_SQL_TBL ss
            JOIN PS.PS_TL_RULE_STEPS  rs ON rs.TL_SQL_NAME = ss.TL_SQL_NAME
            WHERE (
                   UPPER(ss.TL_SQL_TEXT) LIKE '%OVERTIME%'
                OR UPPER(ss.TL_SQL_TEXT) LIKE '%OT_HRS%'
                OR UPPER(ss.TL_SQL_TEXT) LIKE '%HE_%'
                OR UPPER(ss.TL_SQL_TEXT) LIKE '%HORA_EXTRA%'
            )
        """
        params: list = []

        if tl_group_id:
            sql += """
              AND rs.TL_RULE_ID IN (
                  SELECT TL_RULE_ID FROM PS.PS_TL_GROUP_RULE
                  WHERE TL_GROUP_ID = :1
              )
            """
            params.append(tl_group_id.upper())

        sql += " ORDER BY rs.TL_RULE_ID, rs.TL_STEP_NUM"

        result = await execute_query(sql, params)
        if "error" in result:
            return result

        rows = result.get("results", [])
        enriched = []
        for row in rows:
            excerpt    = row.get("SQL_EXCERPT") or ""
            upper_exc  = excerpt.upper()
            generates_he = any(k in upper_exc for k in ["OVERTIME", "OT_HRS", "HE_"])
            generates_an = any(k in upper_exc for k in ["NIGHT", "NOTURNO", "ADICNOTURNO"])
            op = (
                "INSERT" if "INSERT" in upper_exc else
                "UPDATE" if "UPDATE" in upper_exc else
                "DELETE" if "DELETE" in upper_exc else "SELECT/OTHER"
            )
            enriched.append({
                **row,
                "operation_type":       op,
                "combined_bug_pattern": generates_he and generates_an,
                "risk_level":           "ALTO" if (generates_he and generates_an) else "MÉDIO",
            })

        high_risk = [r for r in enriched if r["risk_level"] == "ALTO"]

        return {
            "search_group":   tl_group_id or "TODOS",
            "total_found":    len(enriched),
            "high_risk_count": len(high_risk),
            "steps":          enriched,
        }

    # ──────────────────────────────────────────────────────────────
    # 4. Lançamentos intermediários de um funcionário (PS_TL_IPT)
    # ──────────────────────────────────────────────────────────────
    @mcp.tool()
    async def tl_get_employee_ipt(
        emplid: str,
        begin_dt: str | None = None,
        end_dt: str | None = None,
    ) -> dict:
        """
        Consulta os lançamentos intermediários (PS_TL_IPT) de um funcionário.

        Use para confirmar se Horas Extras estão sendo geradas e em qual TRC,
        após o processamento do Time Administration.

        Args:
            emplid:   EMPLID do funcionário de teste (ex: '00012345')
            begin_dt: Data inicial no formato YYYY-MM-DD (opcional)
            end_dt:   Data final no formato YYYY-MM-DD (opcional)

        Returns:
            Lançamentos agrupados por data + lista de dias com HE detectada.
        """
        date_filter = ""
        params: list = [emplid.upper()]

        if begin_dt:
            date_filter += " AND ipt.DUR >= TO_DATE(:2, 'YYYY-MM-DD')"
            params.append(begin_dt)
        if end_dt:
            date_filter += f" AND ipt.DUR <= TO_DATE(:{len(params)+1}, 'YYYY-MM-DD')"
            params.append(end_dt)

        sql = f"""
            SELECT
                ipt.EMPLID,
                ipt.EMPL_RCD,
                TO_CHAR(ipt.DUR, 'YYYY-MM-DD')     AS DATA_JULGAMENTO,
                ipt.TRC,
                ROUND(ipt.QUANTITY, 4)              AS QTD_HORAS,
                TO_CHAR(ipt.PUNCH_DTTM_IN,  'HH24:MI') AS ENTRADA,
                TO_CHAR(ipt.PUNCH_DTTM_OUT, 'HH24:MI') AS SAIDA,
                ipt.TL_QUANTITY_TYPE
            FROM PS.PS_TL_IPT ipt
            WHERE ipt.EMPLID = :1
            {date_filter}
            ORDER BY ipt.DUR, ipt.TRC
        """

        result = await execute_query(sql, params)
        if "error" in result:
            return result

        rows = result.get("results", [])

        by_date: dict = {}
        for row in rows:
            dt = row.get("DATA_JULGAMENTO", "")
            by_date.setdefault(dt, []).append(row)

        days_with_overtime = [
            dt for dt, entries in by_date.items()
            if any("HE" in str(e.get("TRC", "")).upper() or "OT" in str(e.get("TRC", "")).upper()
                   for e in entries)
        ]

        return {
            "emplid":              emplid.upper(),
            "period":              f"{begin_dt or 'início'} → {end_dt or 'hoje'}",
            "total_lancamentos":   len(rows),
            "days_with_overtime":  days_with_overtime,
            "lancamentos_por_data": by_date,
        }

    # ──────────────────────────────────────────────────────────────
    # 5. Detectar padrão de jornada mista (bug HE + AN)
    # ──────────────────────────────────────────────────────────────
    @mcp.tool()
    async def tl_detect_mixed_shift_bug(
        tl_group_id: str,
        night_threshold: int = 22,
    ) -> dict:
        """
        Detecta steps do grupo T&L que geram Horas Extras E Adicional Noturno
        simultaneamente para jornadas mistas (cruzamento diurno→noturno).

        Este é o padrão de bug que afeta o grupo SEMESTRAL: funcionários com
        jornada que cruza as 22h recebem HE + AN, quando deveriam receber
        apenas AN (a jornada é normal, apenas o período é noturno).

        Args:
            tl_group_id:     Grupo alvo (ex: 'SEMESTRAL')
            night_threshold: Hora de início do período noturno (padrão CLT: 22)

        Returns:
            Steps suspeitos com nível de risco e diagnóstico detalhado.
        """
        sql = """
            SELECT
                rs.TL_RULE_ID,
                rs.TL_STEP_NUM,
                rs.TL_STEP_NAME,
                rs.TL_ACTION_TYPE,
                ss.TL_SQL_NAME,
                ss.TL_SQL_TEXT
            FROM PS.PS_TL_STP_SQL_TBL ss
            JOIN PS.PS_TL_RULE_STEPS  rs ON rs.TL_SQL_NAME = ss.TL_SQL_NAME
            JOIN PS.PS_TL_GROUP_RULE  gr ON gr.TL_RULE_ID  = rs.TL_RULE_ID
            WHERE gr.TL_GROUP_ID = :1
              AND (
                   UPPER(ss.TL_SQL_TEXT) LIKE '%OVERTIME%'
                OR UPPER(ss.TL_SQL_TEXT) LIKE '%HE_%'
                OR UPPER(ss.TL_SQL_TEXT) LIKE '%OT_HRS%'
              )
              AND (
                   UPPER(ss.TL_SQL_TEXT) LIKE '%NOTURNO%'
                OR UPPER(ss.TL_SQL_TEXT) LIKE '%NIGHT%'
                OR UPPER(ss.TL_SQL_TEXT) LIKE '%ADICNOTURNO%'
                OR ss.TL_SQL_TEXT LIKE '%22%'
              )
            ORDER BY rs.TL_RULE_ID, rs.TL_STEP_NUM
        """

        result = await execute_query(sql, [tl_group_id.upper()])
        if "error" in result:
            return result

        suspects = []
        for row in result.get("results", []):
            sql_text  = row.get("TL_SQL_TEXT") or ""
            upper_sql = sql_text.upper()

            gen_he = any(k in upper_sql for k in ["OVERTIME", "OT_HRS", "HE_"])
            gen_an = any(k in upper_sql for k in ["NIGHT", "NOTURNO", "ADICNOTURNO"])
            ref_22 = str(night_threshold) in sql_text

            suspects.append({
                "tl_rule_id":    row["TL_RULE_ID"],
                "tl_step_num":   row["TL_STEP_NUM"],
                "tl_step_name":  row["TL_STEP_NAME"],
                "tl_sql_name":   row["TL_SQL_NAME"],
                "risk_level":    "ALTO" if (gen_he and gen_an) else "MÉDIO",
                "diagnosis": {
                    "generates_overtime":         gen_he,
                    "generates_night_additional": gen_an,
                    "references_threshold":       ref_22,
                    "is_combined_bug":            gen_he and gen_an,
                },
                "sql_excerpt": sql_text[:500],
            })

        high_risk = [s for s in suspects if s["risk_level"] == "ALTO"]

        return {
            "tl_group_id":     tl_group_id.upper(),
            "night_threshold": f"{night_threshold}:00h",
            "total_suspects":  len(suspects),
            "high_risk_count": len(high_risk),
            "recommendation": (
                f"{len(high_risk)} step(s) com risco ALTO detectado(s). "
                "Use tl_generate_fix_proposal para ver a proposta de correção."
            ) if high_risk else "Nenhum padrão de bug combinado HE+AN detectado.",
            "suspects": suspects,
        }

    # ──────────────────────────────────────────────────────────────
    # 6. Gerar proposta de correção (ANTES/DEPOIS)
    # ──────────────────────────────────────────────────────────────
    @mcp.tool()
    async def tl_generate_fix_proposal(
        tl_rule_id: str,
        step_num: int,
        target_group: str = "SEMESTRAL",
    ) -> dict:
        """
        Gera a proposta de correção (SQL ANTES e DEPOIS) para neutralizar
        a geração de Horas Extras em jornadas mistas do grupo alvo.

        Estratégia: adicionar condição NOT EXISTS que verifica se:
        a) O funcionário pertence ao grupo alvo (ex: SEMESTRAL)
        b) A jornada do dia cruza o limite de 22h (jornada mista)

        Se ambas forem verdadeiras, o INSERT de HE é suprimido.

        Args:
            tl_rule_id:   ID da regra a corrigir
            step_num:     Número do step SQL
            target_group: Grupo que deve ser excluído da HE (padrão: 'SEMESTRAL')

        Returns:
            SQL original, SQL corrigido, resumo das alterações e script DMS.
        """
        import re

        sql = """
            SELECT
                rs.TL_RULE_ID,
                rs.TL_STEP_NUM,
                rs.TL_STEP_NAME,
                rs.TL_SQL_NAME,
                ss.TL_SQL_TEXT
            FROM PS.PS_TL_RULE_STEPS   rs
            JOIN PS.PS_TL_STP_SQL_TBL ss ON ss.TL_SQL_NAME = rs.TL_SQL_NAME
            WHERE rs.TL_RULE_ID = :1
              AND rs.TL_STEP_NUM = :2
        """

        result = await execute_query(sql, [tl_rule_id.upper(), step_num])
        if "error" in result:
            return result
        if not result.get("results"):
            return {"error": "Step não encontrado. Verifique tl_rule_id e step_num."}

        row          = result["results"][0]
        original_sql = row.get("TL_SQL_TEXT") or ""
        sql_name     = row.get("TL_SQL_NAME") or ""

        exclusion = f"""
    /* === CORREÇÃO: Excluir HE para grupo {target_group} em jornada mista ===
       Funcionários do grupo {target_group} com marcação cruzando as 22h
       não devem receber Horas Extras — apenas Adicional Noturno.
       Chamado: #<<NUM_CHAMADO>> */
    AND NOT (
        IPT.EMPLID IN (
            SELECT eg.EMPLID
            FROM PS.PS_TL_EMPL_GROUP eg
            WHERE eg.TL_GROUP_ID = '{target_group}'
              AND eg.EFF_STATUS  = 'A'
              AND eg.EFFDT = (
                  SELECT MAX(eg2.EFFDT)
                  FROM PS.PS_TL_EMPL_GROUP eg2
                  WHERE eg2.EMPLID      = eg.EMPLID
                    AND eg2.TL_GROUP_ID = eg.TL_GROUP_ID
                    AND eg2.EFFDT      <= IPT.DUR
              )
        )
        AND EXISTS (
            SELECT 1 FROM PS.PS_TL_PUNCH_TBL pt1
            WHERE pt1.EMPLID   = IPT.EMPLID
              AND pt1.EMPL_RCD = IPT.EMPL_RCD
              AND pt1.DUR      = IPT.DUR
              AND TO_NUMBER(TO_CHAR(pt1.PUNCH_DTTM, 'HH24')) < 22
        )
        AND EXISTS (
            SELECT 1 FROM PS.PS_TL_PUNCH_TBL pt2
            WHERE pt2.EMPLID   = IPT.EMPLID
              AND pt2.EMPL_RCD = IPT.EMPL_RCD
              AND pt2.DUR      = IPT.DUR
              AND TO_NUMBER(TO_CHAR(pt2.PUNCH_DTTM, 'HH24')) >= 22
        )
    )"""

        # Inserir antes do ORDER BY ou no final
        fixed_sql = original_sql
        for keyword in ["ORDER BY", "GROUP BY", "HAVING"]:
            idx = original_sql.upper().rfind(keyword)
            if idx > 0:
                fixed_sql = original_sql[:idx] + exclusion + "\n" + original_sql[idx:]
                break
        else:
            fixed_sql = original_sql.rstrip(";") + exclusion + ";"

        escaped   = fixed_sql.replace("'", "''")
        dms_script = f"""-- DMS: Correção Jornada Mista {target_group}
-- Executar APENAS em homologação

-- 1. Backup
INSERT INTO PS.PS_TL_STP_SQL_BCK (TL_SQL_NAME, TL_SQL_TEXT, BACKUP_DTTM, BACKUP_REASON)
SELECT TL_SQL_NAME, TL_SQL_TEXT, SYSDATE, 'FIX JORNADA MISTA {target_group}'
FROM PS.PS_TL_STP_SQL_TBL WHERE TL_SQL_NAME = '{sql_name}';

-- 2. Aplicar correção
UPDATE PS.PS_TL_STP_SQL_TBL
SET    TL_SQL_TEXT = '{escaped}'
WHERE  TL_SQL_NAME = '{sql_name}';
COMMIT;

-- ROLLBACK:
-- UPDATE PS.PS_TL_STP_SQL_TBL SET TL_SQL_TEXT =
--   (SELECT TL_SQL_TEXT FROM PS.PS_TL_STP_SQL_BCK
--    WHERE TL_SQL_NAME = '{sql_name}' ORDER BY BACKUP_DTTM DESC FETCH FIRST 1 ROWS ONLY)
-- WHERE TL_SQL_NAME = '{sql_name}'; COMMIT;
"""

        return {
            "tl_rule_id":    tl_rule_id.upper(),
            "step_num":      step_num,
            "sql_name":      sql_name,
            "target_group":  target_group,
            "before": {
                "description": "SQL original — gera HE sem distinção de grupo ou tipo de jornada",
                "sql": original_sql,
            },
            "after": {
                "description": f"SQL corrigido — exclui HE para grupo {target_group} em jornada mista",
                "sql": fixed_sql,
                "changes": [
                    f"Adicionado bloco NOT(...) verificando grupo {target_group}",
                    "Verificação de jornada mista via EXISTS em PS_TL_PUNCH_TBL (antes/depois das 22h)",
                    "Alteração cirúrgica — outros grupos e fórmulas não são afetados",
                ],
            },
            "dms_script": dms_script,
        }

    # ──────────────────────────────────────────────────────────────
    # 7. Relatório de cobertura do grupo
    # ──────────────────────────────────────────────────────────────
    @mcp.tool()
    async def tl_group_coverage_report(tl_group_id: str) -> dict:
        """
        Gera um relatório de cobertura do grupo T&L mostrando:
        - Quantidade de regras por tipo (DAY, PERIOD, WEEKLY)
        - Steps que impactam Horas Extras
        - Steps que impactam Adicional Noturno
        - Funcionários ativos no grupo

        Args:
            tl_group_id: ID do grupo (ex: 'SEMESTRAL')

        Returns:
            Relatório consolidado para análise antes de qualquer modificação.
        """
        rules_sql = """
            SELECT rd.TL_RULE_TYPE, COUNT(*) AS QTD
            FROM PS.PS_TL_GROUP_RULE gr
            JOIN PS.PS_TL_RULE_DEFN  rd ON rd.TL_RULE_ID = gr.TL_RULE_ID
            WHERE gr.TL_GROUP_ID = :1
            GROUP BY rd.TL_RULE_TYPE
            ORDER BY QTD DESC
        """

        ot_sql = """
            SELECT COUNT(DISTINCT rs.TL_RULE_ID || '.' || rs.TL_STEP_NUM) AS QTD
            FROM PS.PS_TL_STP_SQL_TBL ss
            JOIN PS.PS_TL_RULE_STEPS  rs ON rs.TL_SQL_NAME = ss.TL_SQL_NAME
            JOIN PS.PS_TL_GROUP_RULE  gr ON gr.TL_RULE_ID  = rs.TL_RULE_ID
            WHERE gr.TL_GROUP_ID = :1
              AND (UPPER(ss.TL_SQL_TEXT) LIKE '%OVERTIME%' OR UPPER(ss.TL_SQL_TEXT) LIKE '%HE_%')
        """

        an_sql = """
            SELECT COUNT(DISTINCT rs.TL_RULE_ID || '.' || rs.TL_STEP_NUM) AS QTD
            FROM PS.PS_TL_STP_SQL_TBL ss
            JOIN PS.PS_TL_RULE_STEPS  rs ON rs.TL_SQL_NAME = ss.TL_SQL_NAME
            JOIN PS.PS_TL_GROUP_RULE  gr ON gr.TL_RULE_ID  = rs.TL_RULE_ID
            WHERE gr.TL_GROUP_ID = :1
              AND (UPPER(ss.TL_SQL_TEXT) LIKE '%NOTURNO%' OR UPPER(ss.TL_SQL_TEXT) LIKE '%NIGHT%')
        """

        emp_sql = """
            SELECT COUNT(DISTINCT EMPLID) AS QTD
            FROM PS.PS_TL_EMPL_GROUP
            WHERE TL_GROUP_ID = :1 AND EFF_STATUS = 'A'
        """

        rules_r, ot_r, an_r, emp_r = await __import__('asyncio').gather(
            execute_query(rules_sql, [tl_group_id.upper()]),
            execute_query(ot_sql, [tl_group_id.upper()]),
            execute_query(an_sql, [tl_group_id.upper()]),
            execute_query(emp_sql, [tl_group_id.upper()]),
        )

        return {
            "tl_group_id":        tl_group_id.upper(),
            "rules_by_type":      rules_r.get("results", []),
            "steps_with_overtime": (ot_r.get("results") or [{}])[0].get("QTD", 0),
            "steps_with_night":    (an_r.get("results") or [{}])[0].get("QTD", 0),
            "active_employees":    (emp_r.get("results") or [{}])[0].get("QTD", 0),
            "recommendation": (
                "Execute tl_detect_mixed_shift_bug antes de qualquer alteração "
                "para identificar os steps de risco."
            ),
        }
