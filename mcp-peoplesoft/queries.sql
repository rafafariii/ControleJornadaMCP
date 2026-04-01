-- ============================================================
-- queries.sql — Consultas de referência para mcp-peoplesoft
-- Todas as queries são READ-ONLY (somente SELECT)
-- ============================================================

-- ── 1. Record definition ─────────────────────────────────────
-- Metadados de um record específico
SELECT RECNAME, RECDESCR, RECTYPE, FIELDCOUNT, AUDITRECNAME
FROM   PSRECDEFN
WHERE  RECNAME = :recname;

-- ── 2. Fields de um record ────────────────────────────────────
SELECT f.FIELDNAME, f.FIELDTYPE, f.LENGTH, f.DECIMALPOS,
       d.LONGNAME, f.USEEDIT, f.FIELDNUM
FROM   PSRECFIELD f
JOIN   PSDBFIELD  d ON d.FIELDNAME = f.FIELDNAME
WHERE  f.RECNAME = :recname
ORDER  BY f.FIELDNUM;

-- ── 3. PeopleCode de um objeto ────────────────────────────────
SELECT p.OBJECTVALUE1, p.OBJECTVALUE2, p.OBJECTVALUE3,
       t.PCMPTEXT
FROM   PSPCMPROG p
JOIN   PSPCMTXT  t ON t.OBJECTID1    = p.OBJECTID1
                  AND t.OBJECTVALUE1 = p.OBJECTVALUE1
WHERE  p.OBJECTVALUE1 = :objectid1
  AND  (:objectid2   IS NULL OR p.OBJECTVALUE2 = :objectid2)
  AND  (:event_name  IS NULL OR UPPER(p.OBJECTVALUE3) = UPPER(:event_name));

-- ── 4. Onde um field é referenciado (PeopleCode) ─────────────
SELECT OBJECTVALUE1, OBJECTVALUE2, OBJECTVALUE3
FROM   PSPCMPROG
WHERE  UPPER(PCMPTEXT) LIKE UPPER('%' || :field_name || '%');

-- ── 5. Application Engine — steps de uma aplicação ───────────
SELECT AE_APPLID, AE_SECTION, AESTEPNUM, STMTTYPE,
       SUBSTR(STMTTEXT,1,200) AS STMTTEXT_PREVIEW
FROM   PSAESTEPDEFN
WHERE  AE_APPLID = :ae_applid
ORDER  BY AE_SECTION, AESTEPNUM;

-- ── 6. Status de processos agendados ─────────────────────────
SELECT PRCSINSTANCE, PRCSTYPE, PRCSNAME, RUNSTATUS,
       BEGINDTTM, ENDDTTM, OPRID
FROM   PSPRCSRQST
WHERE  PRCSNAME = :prcsname
ORDER  BY PRCSINSTANCE DESC
FETCH  FIRST 50 ROWS ONLY;

-- ── 7. Worklist items pendentes ───────────────────────────────
SELECT BUSPROCNAME, ACTIVITYNAME, EVENTNAME, WORKLISTNAME,
       INSTSTATUS, ORIGOPRID, ROUTEDTOOPRID, INSTEADDT
FROM   PSWORKLIST
WHERE  INSTSTATUS = 1  -- 1 = Open/Active
ORDER  BY INSTEADDT DESC
FETCH  FIRST 100 ROWS ONLY;

-- ── 8. Workflow: activities de um Business Process ────────────
SELECT BUSPROCNAME, ACTIVITYNAME, DESCR, STATUS
FROM   PSACTIVITY
WHERE  BUSPROCNAME = :business_process;

-- ── 9. Integration Broker — serviços disponíveis ─────────────
SELECT IBSERVICENAME, DESCR, SERVICETYPE, CONTVERSIONNUMBER
FROM   PSIBSVCSETUP
ORDER  BY IBSERVICENAME;

-- ── 10. IB — Subscriptions de um serviço ─────────────────────
SELECT IBSUBNAME, SUBTYPE, DESCR
FROM   PSIBSUBDEFN
WHERE  IBSERVICENAME = :service_name;

-- ── 11. IB — Nodes configurados ──────────────────────────────
SELECT MSGNODENAME, DESCR, DEFAULTMSGNODE
FROM   PSMSGNODEDEFN
ORDER  BY MSGNODENAME;

-- ── 12. Locks ativos ─────────────────────────────────────────
SELECT OPRID, RECNAME, KEYVALUE, DTTM_STAMP
FROM   PSLOCK
ORDER  BY DTTM_STAMP DESC
FETCH  FIRST 50 ROWS ONLY;

-- ── 13. Itens de um projeto de migração ──────────────────────
SELECT pi.PROJECTNAME, pi.OBJECTTYPE, pi.OBJECTID1, pi.OBJECTID2,
       pi.OBJECTID3, pi.OBJECTID4, pd.DESCR
FROM   PSPROJITEM  pi
JOIN   PSPROJECTDEFN pd ON pd.PROJECTNAME = pi.PROJECTNAME
WHERE  pi.PROJECTNAME = :project_name
ORDER  BY pi.OBJECTTYPE, pi.OBJECTID1;

-- ── 14. AE Application State (reinicialização de processos) ──
SELECT AE_APPLID, PROCESS_INSTANCE, AE_SECTION, AESTEPNUM,
       AESTATUS
FROM   PSAEAPPLSTATE
WHERE  AE_APPLID = :ae_applid
  AND  PROCESS_INSTANCE = :process_instance;
