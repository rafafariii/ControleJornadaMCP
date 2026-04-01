#!/usr/bin/env bash
# setup_github.sh — Cria repositório GitHub e faz push do mcp-peoplesoft
# Uso: chmod +x setup_github.sh && ./setup_github.sh

set -euo pipefail

REPO_NAME="mcp-peoplesoft"
GITHUB_USER="rafafariii"
BRANCH="main"

echo "========================================"
echo "  mcp-peoplesoft — Setup GitHub"
echo "========================================"

# ── Passo 1: Autenticar no GitHub CLI
echo ""
echo "[1/5] Verificando autenticação GitHub CLI..."
if ! gh auth status &>/dev/null; then
    echo "Não autenticado. Iniciando login..."
    gh auth login
else
    echo "✅ Já autenticado no GitHub CLI."
fi

# ── Passo 2: Criar repositório remoto (público)
echo ""
echo "[2/5] Criando repositório ${GITHUB_USER}/${REPO_NAME} no GitHub..."
if gh repo view "${GITHUB_USER}/${REPO_NAME}" &>/dev/null; then
    echo "ℹ️  Repositório já existe: https://github.com/${GITHUB_USER}/${REPO_NAME}"
else
    gh repo create "${GITHUB_USER}/${REPO_NAME}" \
        --public \
        --description "MCP Server para PeopleSoft — Oracle DB, PeopleTools e Integration Broker" \
        --confirm 2>/dev/null || \
    gh repo create "${REPO_NAME}" \
        --public \
        --description "MCP Server para PeopleSoft — Oracle DB, PeopleTools e Integration Broker"
    echo "✅ Repositório criado: https://github.com/${GITHUB_USER}/${REPO_NAME}"
fi

# ── Passo 3: Inicializar Git local
echo ""
echo "[3/5] Inicializando repositório Git local..."
if [ ! -d ".git" ]; then
    git init
    echo "✅ Git inicializado."
else
    echo "ℹ️  Git já inicializado."
fi

# Configura branch principal
git checkout -b "${BRANCH}" 2>/dev/null || git checkout "${BRANCH}" 2>/dev/null || true

# ── Passo 4: Configurar remote
echo ""
echo "[4/5] Configurando remote origin..."
REMOTE_URL="https://github.com/${GITHUB_USER}/${REPO_NAME}.git"
if git remote get-url origin &>/dev/null; then
    git remote set-url origin "${REMOTE_URL}"
    echo "ℹ️  Remote atualizado: ${REMOTE_URL}"
else
    git remote add origin "${REMOTE_URL}"
    echo "✅ Remote adicionado: ${REMOTE_URL}"
fi

# ── Passo 5: Commit e push
echo ""
echo "[5/5] Fazendo commit e push..."
git add \
    server.py \
    requirements.txt \
    queries.sql \
    README.md \
    .gitignore \
    setup_github.sh

# Garante que a pasta knowledge/ (sem arquivos sensíveis) seja rastreada
mkdir -p knowledge/traces knowledge/vectors knowledge/exports
touch knowledge/traces/.gitkeep knowledge/vectors/.gitkeep knowledge/exports/.gitkeep
git add knowledge/

git commit -m "feat: initial commit — mcp-peoplesoft com 9 ferramentas PeopleSoft

- trace_workflow: parse e indexação de trace files (FTS5)
- get_table_metadata: metadados PSRECDEFN/PSRECFIELD/PSDBFIELD
- get_peoplecode: extração de PeopleCode (PSPCMPROG+PSPCMTXT)
- search_references: busca de referências em PC/AE/RecField
- run_safe_query: SELECT whitelist em tabelas PS autorizadas
- suggest_change: análise de impacto e passos de mudança
- get_workflow_def: definição de Workflow (PSACTIVITY/PSROUTE)
- get_ib_service: Integration Broker (PSIBSVCSETUP/PSIBSUBDEFN)
- export_knowledge: exportação markdown/JSON do knowledge base" \
|| echo "ℹ️  Nada para commitar ou commit já existente."

git push -u origin "${BRANCH}" --force-with-lease \
|| git push -u origin "${BRANCH}"

echo ""
echo "========================================"
echo "✅ Push concluído!"
echo "   Repositório: https://github.com/${GITHUB_USER}/${REPO_NAME}"
echo "========================================"
