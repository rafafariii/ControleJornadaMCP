# Prompt para Copilot — Limpeza e Formatação de Trace PeopleSoft T&L

> Cole este prompt no GitHub Copilot Chat (ou qualquer LLM) junto com o conteúdo bruto do trace.

---

## Prompt

```
Você é um especialista em PeopleSoft Time & Labor.
Vou te passar o conteúdo bruto de um arquivo de trace de execução do Application Engine TL_TA
(julgamento de ponto eletrônico). Sua tarefa é limpar e reformatar esse arquivo para que
ele possa ser consumido por um servidor MCP (motor de conhecimento).

## Regras de limpeza obrigatórias

1. **Mantenha exatamente** os cabeçalhos de Section e Step no formato:
   Section: NOME_DA_SECAO
   Step: NOME_DO_STEP

2. **Mantenha** os blocos de SQL completos — não truncar, não parafrasear.

3. **Mantenha** as linhas de tempo no formato:
   Elapsed Time: X.XXX (segundos)
   ou
   Elapsed Time = X.XXX

4. **Remova** (são ruído, não agregam ao MCP):
   - Linhas de header do trace com informações do ambiente (hostname, data/hora bruta de geração, versão do PeopleTools)
   - Linhas repetidas de "PeopleSoft Trace" ou "Application Engine" sem conteúdo de step
   - Linhas em branco duplicadas (mais de 2 consecutivas → reduzir para 1)
   - Dump de memória hexadecimal (linhas como "0x00001234 ...")
   - Linhas de separador puro (somente traços "---" ou "===" sem texto)

5. **Anonimize** os seguintes dados antes de salvar:
   - EMPLID (matrícula do funcionário): substituir por EMPLID_ANONIMIZADO
   - OPRID (operador): substituir por OPRID_ANONIMIZADO
   - Senhas ou tokens: substituir por ***REDACTED***
   - Nomes pessoais literais se aparecerem em valores de coluna: substituir por NOME_ANONIMIZADO

6. **Preserve o fluxo lógico**: a sequência Section → Step → SQL → Elapsed Time
   deve ser mantida integralmente. Não reordene nada.

7. **Formato de saída**: texto puro (.txt ou .trc), UTF-8, sem BOM.
   Não use markdown, não adicione comentários externos ao conteúdo do trace.

## Estrutura esperada de saída (exemplo)

Section: TL_TA.INIT
Step: GET_EMPL_DATA
  SELECT A.EMPLID, A.EMPL_RCD, B.TL_GROUP_ID
    FROM PS_JOB A, PS_TL_EMPL_GROUP B
   WHERE A.EMPLID = B.EMPLID
     AND A.EMPLID = 'EMPLID_ANONIMIZADO'
  Elapsed Time: 0.032

Section: TL_TA.CALC_HRS
Step: APPLY_OVERTIME_RULE
  SELECT ... (SQL completo da regra)
  Elapsed Time: 1.204

## Input

[COLE AQUI O CONTEÚDO BRUTO DO TRACE]
```

---

## Como usar

1. Copie o prompt acima.
2. No Copilot Chat, cole o prompt e logo abaixo cole o conteúdo bruto do seu arquivo `.trc`.
3. O Copilot vai devolver o trace limpo e anonimizado.
4. Salve o resultado como `tl_ta_julgamento_AAAAMMDD.trc` (ou nome descritivo).
5. Mova o arquivo para a pasta `mcp-peoplesoft/knowledge/traces/`.
6. Chame a ferramenta MCP: `Analise o arquivo 'tl_ta_julgamento_AAAAMMDD.trc'`.
