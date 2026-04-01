# mcp-peoplesoft

> MCP Server em Python para integração com **PeopleSoft** via Oracle DB, PeopleTools e Integration Broker.

---

## Visão Geral

O `mcp-peoplesoft` expõe **9 ferramentas** via Model Context Protocol (MCP) que permitem ao Claude Desktop analisar, pesquisar e sugerir melhorias em ambientes PeopleSoft de forma segura e auditada — sem executar DML no banco de produção.

---

## Estrutura do Projeto

```
mcp-peoplesoft/
├── server.py                  # MCP Server principal (todas as 9 tools)
├── queries.sql                # Consultas SQL de referência (read-only)
├── requirements.txt           # Dependências Python
├── setup_github.sh            # Script para publicar no GitHub
├── .gitignore                 # Proteção de arquivos sensíveis
├── claude_desktop_config.json # Configuração do Claude Desktop (NÃO commitar)
└── knowledge/
    ├── traces/                # Arquivos .tracesql/.trc do PeopleSoft
    ├── vectors/               # Vetores de embedding (gerados em runtime)
    └── exports/               # Exportações markdown/JSON
```

---

## Ferramentas Disponíveis

| # | Tool | Descrição |
|---|------|-----------|
| 1 | `trace_workflow` | Parse de trace files PeopleSoft com indexação FTS5 e análise de steps lentos |
| 2 | `get_table_metadata` | Metadados completos de records (PSRECDEFN, PSRECFIELD, PSDBFIELD) com cache |
| 3 | `get_peoplecode` | Extração de código PeopleCode (PSPCMPROG + PSPCMTXT) |
| 4 | `search_references` | Busca de referências de fields/records em PC, AE e RecField |
| 5 | `run_safe_query` | SELECT com whitelist de tabelas autorizadas, bloqueia DML |
| 6 | `suggest_change` | Análise de impacto e passos para implementar mudanças |
| 7 | `get_workflow_def` | Definição de Workflow (PSACTIVITY + PSROUTE) |
| 8 | `get_ib_service` | Detalhes do Integration Broker (PSIBSVCSETUP, PSIBSUBDEFN, PSMSGNODEDEFN) |
| 9 | `export_knowledge` | Exportação de knowledge base em markdown ou JSON |

---

## Pré-requisitos

- Python 3.11+
- Oracle Client (ou `oracledb` em modo thin — não requer client)
- GitHub CLI (`gh`) para o script de setup
- Claude Desktop instalado

---

## Instalação

```bash
# 1. Clone o repositório
git clone https://github.com/rafafariii/mcp-peoplesoft.git
cd mcp-peoplesoft

# 2. Crie o ambiente virtual
python -m venv .venv
source .venv/bin/activate       # Linux/macOS
# .venv\Scripts\activate        # Windows

# 3. Instale as dependências
pip install -r requirements.txt

# 4. Teste o servidor (deve iniciar sem erros de importação)
python server.py
```

---

## Configuração do Claude Desktop

1. Copie `claude_desktop_config.json` para:
   - **macOS**: `~/Library/Application Support/Claude/claude_desktop_config.json`
   - **Windows**: `%APPDATA%\Claude\claude_desktop_config.json`

2. Edite as variáveis de ambiente com suas credenciais Oracle:

```json
{
  "mcpServers": {
    "mcp-peoplesoft": {
      "command": "python",
      "args": ["/caminho/para/mcp-peoplesoft/server.py"],
      "env": {
        "PS_DB_USER":      "seu_usuario",
        "PS_DB_PASSWORD":  "sua_senha",
        "PS_DB_DSN":       "host:port/service",
        "MCP_PROJECT_DIR": "/caminho/para/mcp-peoplesoft"
      }
    }
  }
}
```

3. Reinicie o Claude Desktop.

---

## Variáveis de Ambiente

| Variável | Descrição | Exemplo |
|----------|-----------|---------|
| `PS_DB_USER` | Usuário Oracle | `SYSADM` |
| `PS_DB_PASSWORD` | Senha Oracle | `****` |
| `PS_DB_DSN` | DSN Oracle | `orahost:1521/PSFT` |
| `MCP_PROJECT_DIR` | Caminho do projeto | `/opt/mcp-peoplesoft` |

---

## Segurança

- **Nenhum DML** é executado em produção — todas as queries são SELECT
- Whitelist de 21 tabelas PeopleSoft autorizadas em `run_safe_query`
- Credenciais nunca são expostas em logs ou respostas
- `claude_desktop_config.json` está no `.gitignore` e **não deve ser commitado**
- Dados de trace e vetores ficam apenas localmente em `knowledge/`

---

## Adicionando Traces

Coloque arquivos `.tracesql` ou `.trc` na pasta `knowledge/traces/` e use:

```
trace_workflow(trace_file="meu_trace.tracesql", top_slow=15)
```

---

## Publicar no GitHub

```bash
chmod +x setup_github.sh
./setup_github.sh
```

---

## Licença

MIT — Rafael Limas
