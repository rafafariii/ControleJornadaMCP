# mcp-peoplesoft

> **MCP Server unificado para PeopleSoft — Itaú Unibanco**
> Análise de metadados, RH, Folha, Time & Labor e diagnóstico de Jornada Mista.

---

## O que é isso? (ELI5 — Explica como se eu tivesse 5 anos)

Imagina que o PeopleSoft é uma cidade enorme com milhares de ruas e prédios.
Você precisa achar um problema específico num prédio chamado "Time & Labor", mas não tem um mapa.

O **mcp-peoplesoft** é esse mapa inteligente. Você faz uma pergunta em português para o Claude Desktop, ele usa esse servidor para consultar o banco Oracle do PeopleSoft, e te devolve a resposta — sem você precisar saber SQL, sem risco de modificar dados de produção acidentalmente.

É como ter um consultor PeopleSoft que responde perguntas na hora, 24h por dia.

---

## O que este servidor faz

São **3 blocos de ferramentas** trabalhando juntos:

### Bloco A — Análise de Código e Metadados
Ferramentas para entender a estrutura interna do PeopleSoft:
- **`trace_workflow`** — Lê arquivos de trace (.trc / .tracesql), identifica steps lentos e permite busca no conteúdo
- **`get_table_metadata`** — Mostra todos os campos de uma tabela PeopleSoft (com cache local)
- **`get_peoplecode`** — Extrai código PeopleCode de eventos como FieldChange, SavePreChange
- **`search_references`** — Busca onde um campo ou tabela é usado em PeopleCode, AE e RecField
- **`run_safe_query`** — Executa SELECT livre com proteção: bloqueia INSERT/UPDATE/DELETE
- **`suggest_change`** — Analisa o impacto de uma mudança e gera plano de implementação
- **`get_workflow_def`** — Consulta definições de Workflow (Business Process + Activities)
- **`get_ib_service`** — Detalhes do Integration Broker (serviços, subscriptions, nodes)
- **`export_knowledge`** — Exporta o conhecimento acumulado em markdown ou JSON

### Bloco B — Ferramentas Semânticas de RH e Folha
Integradas do projeto [rgrz/peoplesoft-mcp](https://github.com/rgrz/peoplesoft-mcp):
- **`describe_table`** — Estrutura completa de qualquer tabela PeopleSoft
- **`list_tables`** — Busca tabelas por nome ou módulo
- **`get_translate_values`** — Decodifica valores de campo (ex: `HR_STATUS`: 'A' = Ativo)
- **`get_table_indexes`** — Índices de uma tabela para otimizar queries
- **`get_table_relationships`** — Tabelas relacionadas por chaves compartilhadas
- **`get_employee`** — Perfil completo de um funcionário (dados, cargo, salário)
- **`search_employees`** — Busca funcionários por nome, departamento, cargo
- **`get_job_history`** — Histórico completo de movimentações de um funcionário
- **`get_org_chart`** — Hierarquia organizacional por gestor ou departamento
- **`get_department_info`** — Detalhes de um departamento e seus funcionários
- **`get_payroll_results`** — Resultados de folha (proventos e descontos) por funcionário
- **`get_record_definition`** — Definição completa de um Record PeopleSoft
- **`get_application_engine_steps`** — Steps de um Application Engine
- **`get_sql_definition`** / **`search_sql_definitions`** — SQLs do PeopleTools
- **`search_peoplecode`** — Busca texto em PeopleCode
- **`explain_peoplesoft_concept`** — Explica conceitos PeopleSoft (effective dating, SetID, etc.)
- *(+ 10 ferramentas adicionais de PeopleTools, Benefits e ePerformance)*

### Bloco C — Time & Labor (Itaú / Jornada Mista / SEMESTRAL)
Ferramentas customizadas para o projeto de correção do módulo T&L:
- **`tl_list_group_rules`** — Lista todas as regras de julgamento de ponto de um grupo
- **`tl_get_rule_step_sql`** — SQL completo de um step de regra, com análise de binds
- **`tl_find_overtime_rules`** — Filtra, entre as 850+ fórmulas, as que geram Horas Extras
- **`tl_get_employee_ipt`** — Lançamentos intermediários (PS_TL_IPT) de um funcionário
- **`tl_detect_mixed_shift_bug`** — Detecta steps que geram HE + AN simultaneamente (o bug)
- **`tl_generate_fix_proposal`** — Gera SQL ANTES/DEPOIS da correção + script DMS
- **`tl_group_coverage_report`** — Relatório de cobertura do grupo antes de qualquer mudança

### Bloco D — Sentry: Monitoramento de Produção + Base de SOPs (CAG)
Inspirado no projeto [peoplesoft_sentry](https://github.com) (AIOps Diagnostic Engine):
- **`ps_get_ib_errors`** — Busca erros recentes no Integration Broker (PS_MSG_INST com MSG_STATUS=7) — com match automático de SOP
- **`ps_get_process_errors`** — Processos com falha no Process Monitor (PSPRCSRQST RUNSTATUS=14) — com SOP recomendado
- **`ps_get_system_summary`** — Resumo de saúde do ambiente: contagem de erros IB + processos, status HEALTHY/DEGRADED/CRITICAL
- **`ps_health_check`** — Diagnóstico completo: varre IB + Process Monitor, cruza com base de SOPs e retorna plano de ação por incidente
- **`ps_lookup_sop`** — Consulta à base de conhecimento: dado um texto de erro, retorna o SOP com causa raiz, passos de resolução e escalação

> **Técnica CAG (Cache-Augmented Generation):** As SOPs ficam em memória (zero latência de banco).
> O resource `peoplesoft://sop-library` expõe toda a base para injeção no system prompt.
> Biblioteca inclui SOPs específicos do Itaú: TL_TA, Jornada Mista SEMESTRAL, PS_TL_IPT vazio, além dos genéricos Oracle + IB + Folha.

---

## Estrutura do Projeto

```
mcp-peoplesoft/
├── server.py                  # Servidor principal (FastMCP, registra tudo)
├── db.py                      # Conexão Oracle async + sync
├── requirements.txt           # Dependências Python
├── .env.example               # Modelo de variáveis de ambiente
├── queries.sql                # Queries SQL de referência (read-only)
├── tools/
│   ├── __init__.py
│   ├── introspection.py       # describe_table, list_tables, get_translate_values...
│   ├── hr.py                  # get_employee, search_employees, get_org_chart...
│   ├── payroll.py             # get_payroll_results, accumulators...
│   ├── performance.py         # ePerformance tools
│   ├── benefits.py            # Benefits tools
│   ├── peopletools.py         # AE, PeopleCode, Components, Security...
│   ├── tl.py                  # ⭐ Time & Labor Itaú (jornada mista / SEMESTRAL)
│   └── sentry.py              # 🔴 Monitoramento de produção + SOPs CAG (IB, Process Monitor)
├── docs/
│   ├── peoplesoft_schema_guide.md
│   ├── peoplesoft_concepts.md
│   ├── peopletools_guide.md
│   ├── sql_query_examples.md
│   └── ...
└── knowledge/
    ├── traces/                # Coloque aqui seus arquivos .trc / .tracesql
    ├── vectors/               # Embeddings gerados em runtime
    └── exports/               # Exportações markdown/JSON
```

---

## Instalação

```bash
# 1. Entrar na pasta do projeto
cd mcp-peoplesoft

# 2. Criar ambiente virtual
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # Linux/macOS

# 3. Instalar dependências
pip install -r requirements.txt

# 4. Configurar credenciais
cp .env.example .env
# Edite .env com seu ORACLE_DSN, ORACLE_USER, ORACLE_PASSWORD

# 5. Testar (deve iniciar sem erros)
python server.py
```

---

## Configuração no Claude Desktop

Edite `%APPDATA%\Claude\claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "mcp-peoplesoft": {
      "command": "python",
      "args": ["C:\\Users\\farizinnn\\Documents\\GitHub\\ControleJornadaMCP\\mcp-peoplesoft\\server.py"],
      "env": {
        "ORACLE_DSN":       "host:1521/PSFTPRD",
        "ORACLE_USER":      "SYSADM",
        "ORACLE_PASSWORD":  "sua_senha",
        "MCP_PROJECT_DIR":  "C:\\Users\\farizinnn\\Documents\\GitHub\\ControleJornadaMCP\\mcp-peoplesoft"
      }
    }
  }
}
```

Reinicie o Claude Desktop após salvar.

---

## Variáveis de Ambiente

| Variável | Descrição | Exemplo |
|----------|-----------|---------|
| `ORACLE_DSN` | DSN Oracle (host:porta/service) | `ora01:1521/PSFTPRD` |
| `ORACLE_USER` | Usuário do banco | `SYSADM` |
| `ORACLE_PASSWORD` | Senha | `****` |
| `MCP_PROJECT_DIR` | Caminho do projeto (opcional) | `C:\...\mcp-peoplesoft` |

> As variáveis também aceitam os nomes legados `PS_DB_DSN`, `PS_DB_USER`, `PS_DB_PASSWORD`.

---

## Segurança

- **Zero DML em produção** — `run_safe_query` bloqueia INSERT/UPDATE/DELETE/DROP
- **Whitelist** de 30+ tabelas autorizadas para queries ad-hoc
- **Credenciais** lidas apenas via variáveis de ambiente, nunca expostas em logs
- **`.env` está no `.gitignore`** — nunca commitado
- **Traces e vetores** ficam apenas localmente em `knowledge/`

---

## Prompts de Teste — Como usar no Claude Desktop

Cole qualquer um dos prompts abaixo numa conversa com o Claude Desktop com o servidor ativo.

### 🔍 Exploração de Metadados

```
Descreva a estrutura da tabela PS_TL_IPT — quais são seus campos, tipos e chaves primárias?
```

```
Liste todas as tabelas do PeopleSoft relacionadas a Time & Labor (busque pelo padrão 'TL_').
```

```
O campo HR_STATUS usa translate values. Quais são os valores possíveis e o que cada um significa?
```

```
Quais tabelas são relacionadas a PS_TL_GROUP_RULE por chaves compartilhadas?
```

---

### 👤 RH e Funcionários

```
Busque as informações completas do funcionário com EMPLID '00012345'.
```

```
Mostre o histórico de movimentações do funcionário '00012345' nos últimos 2 anos.
```

```
Liste todos os funcionários ativos do departamento 'TECH01'.
```

```
Gere o organograma a partir do gestor com EMPLID '00099999', mostrando até 3 níveis.
```

---

### 💰 Folha de Pagamento

```
Mostre os proventos e descontos da folha mais recente do funcionário '00012345'.
```

```
Filtre apenas os proventos (earnings) da última folha do funcionário '00012345'.
```

---

### ⏱️ Time & Labor — Diagnóstico de Jornada Mista (SEMESTRAL)

```
Liste todas as regras de julgamento de ponto do grupo SEMESTRAL, ordenadas por sequência de execução.
```

```
Gere um relatório de cobertura completo do grupo SEMESTRAL: quantas regras por tipo,
quantos steps afetam Horas Extras, quantos afetam Adicional Noturno e quantos funcionários estão no grupo.
```

```
Encontre todos os steps de regra do grupo SEMESTRAL que contêm lógica de Horas Extras (HE ou OVERTIME).
```

```
Detecte o padrão de bug de jornada mista no grupo SEMESTRAL: quais steps geram
Horas Extras E Adicional Noturno ao mesmo tempo?
```

```
Para o funcionário de teste '00012345', mostre os lançamentos intermediários (PS_TL_IPT)
do período de 2024-01-01 a 2024-01-31 e identifique os dias onde HE foi gerada.
```

```
Pegue o step de risco mais alto encontrado pelo diagnóstico e gere a proposta de correção
(SQL antes e depois) para neutralizar a HE no grupo SEMESTRAL em jornadas mistas.
```

```
Mostre o SQL completo do step TL_RULE_ID='<<REGRA>>', STEP_NUM=<<N>> e analise
se ele trata a transição de horário após as 22h.
```

---

### 🔧 PeopleTools e Application Engine

```
Quais são os steps do Application Engine TL_TA (Time Administration)?
```

```
Mostre o PeopleCode do evento SavePreChange do record PS_TL_PUNCH_TBL.
```

```
Busque onde o field TRC é referenciado em PeopleCode e em steps de AE.
```

```
Explique o conceito de "Effective Dating" no PeopleSoft e como ele afeta queries SQL.
```

```
Obtenha a definição do SQL Object 'TL_MIXED_SHIFT_OT' do PeopleTools.
```

---

### 📊 Trace e Análise de Performance

```
Analise o arquivo 'trace_processamento.trc' e mostre os 15 steps mais lentos do processamento.
```

```
No trace 'trace_processamento.trc', busque por steps que mencionam 'OVERTIME' ou 'HE_'.
```

```
Exporte o conhecimento acumulado sobre o tópico 'SEMESTRAL' em formato markdown.
```

---

### 🔒 Queries Seguras Ad-Hoc

```
Execute esta query no banco PeopleSoft (leitura segura):
SELECT TL_GROUP_ID, COUNT(*) AS QTD
FROM PS.PS_TL_GROUP_RULE
GROUP BY TL_GROUP_ID
ORDER BY QTD DESC
```

```
Qual é o impacto de modificar o field TRC na tabela PS_TL_IPT?
Faça uma análise de referências e gere um plano de implementação.
```

---

### 🔴 Monitoramento de Produção (Sentry / CAG)

```
Faça um diagnóstico completo do ambiente PeopleSoft nas últimas 6 horas.
Quais incidentes estão ativos e qual é o SOP recomendado para cada um?
```

```
Verifique se há erros no Integration Broker nas últimas 24 horas.
Para cada erro encontrado, indique a causa raiz provável e os passos de resolução.
```

```
Liste todos os processos que falharam no Process Monitor hoje.
Existe algum processo TL_TA ou de Folha entre eles?
```

```
O ambiente está saudável? Mostre o resumo de saúde atual (IB + Process Monitor).
```

```
Tenho o seguinte erro: 'ORA-01555 snapshot too old'.
Qual é o SOP para isso e quem devo acionar?
```

```
Temos uma suspeita de jornada mista gerando HE indevida no grupo SEMESTRAL.
Consulte o SOP específico para esse problema e mostre o plano de ação completo.
```

```
Liste todos os SOPs disponíveis na base de conhecimento PeopleSoft.
```

```
Verifique os erros de IB da última hora. Se houver 'Connection refused',
execute automaticamente o SOP de reconexão e diga quem deve ser escalado.
```

---

## Histórico do Projeto

| Data | O que foi feito |
|------|-----------------|
| 2025-03 | Criação inicial do `mcp-peoplesoft` com 9 ferramentas de análise (trace, metadata, PeopleCode, AE, IB, run_safe_query, suggest_change, workflow, export) |
| 2025-03 | Análise do problema de Jornada Mista / Grupo SEMESTRAL no módulo Time & Labor |
| 2025-03 | Geração dos scripts de diagnóstico: `sql_analysis_queries.sql`, `trace_analyzer.py`, `fix_jornada_mista_semestral.dms` |
| 2025-03 | Integração com `rgrz/peoplesoft-mcp`: absorção dos módulos HR, Payroll, Benefits, Performance, PeopleTools e Introspection |
| 2025-03 | Criação do módulo `tools/tl.py` com 7 ferramentas específicas de T&L (jornada mista, grupo SEMESTRAL) |
| 2025-03 | Migração do server.py para FastMCP (API moderna), criação do `db.py` async unificado |
| 2025-03 | README unificado com ELI5, inventário completo e prompts de teste |
| 2026-03 | Integração do `peoplesoft_sentry` (AIOps): criação do `tools/sentry.py` com Bloco D — 5 ferramentas de monitoramento de produção + base de 8 SOPs CAG específicos do Itaú (T&L, Jornada Mista, IB, Oracle, Folha) |

---

## Contribuição

Projeto interno — Itaú Unibanco / Rafael Limas
Baseado no trabalho de [rgrz/peoplesoft-mcp](https://github.com/rgrz/peoplesoft-mcp) (Roger Martin)

---

## Licença

MIT
