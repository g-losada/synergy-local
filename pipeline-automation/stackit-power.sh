#!/usr/bin/env bash
#
# stackit-power.sh
# ---------------------------------------------------------------------------
# Liga/desliga o servidor (IaaS) e os clusters STACKIT Kubernetes Engine (SKE).
# Mostra sempre o output real da CLI (nada de "AVISO" a esconder o erro).
#
# Uso:
#   ./stackit-power.sh on                     # liga servidor + 3 clusters
#   ./stackit-power.sh off                    # desliga tudo
#   ./stackit-power.sh status                 # mostra estado de tudo
#   ./stackit-power.sh on  --only server       # só o servidor
#   ./stackit-power.sh off --only clusters     # só os clusters
# ---------------------------------------------------------------------------

set -uo pipefail   # nota: sem "-e" -> um comando falhado não mata o script sem explicação

# ====================== CONFIGURAÇÃO (edite aqui) ==========================
PROJECT_ID="${STACKIT_PROJECT_ID:-e063b730-b91e-46fa-bf26-0929dc012a33}"
SERVER_ID="${STACKIT_SERVER_ID:-cba179cc-7501-49a4-9968-1a28ba94d737}"

# Nomes dos clusters SKE (hibernate/wakeup usam o NOME, não o UUID)
CLUSTERS=(
  "core-dev"
  "mgmt"
  "unified"
)
# =============================================================================

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

# --- verificações prévias, com mensagens claras sobre o que falta -----------
if ! command -v stackit >/dev/null 2>&1; then
  echo "ERRO: comando 'stackit' não encontrado no PATH."
  echo "      -> Confirma a instalação com: stackit --version"
  exit 1
fi

if [[ "$PROJECT_ID" != "e063b730-b91e-46fa-bf26-0929dc012a33" ]]; then
  echo "ERRO: PROJECT_ID por preencher no script (ou exporta STACKIT_PROJECT_ID)."
  echo "      -> Corre 'stackit project list' para encontrares o ID."
  exit 1
fi

if [[ "$SERVER_ID" != "cba179cc-7501-49a4-9968-1a28ba94d737" ]]; then
  echo "ERRO: SERVER_ID por preencher no script (ou exporta STACKIT_SERVER_ID)."
  echo "      -> Corre 'stackit server list -p $PROJECT_ID' para encontrares o ID."
  exit 1
fi

# Confirma que há sessão válida (falha cedo, com mensagem clara, em vez de
# um erro genérico mais tarde)
if ! stackit auth get-access-token >/dev/null 2>&1; then
  echo "ERRO: não autenticado (ou sessão expirada)."
  echo "      -> Corre: stackit auth login"
  exit 1
fi

# ------------------------------ SERVIDOR ------------------------------------
 
server_on() {
  log "A ligar servidor $SERVER_ID..."
  stackit server start "$SERVER_ID" -p "$PROJECT_ID" --assume-yes
  [[ $? -eq 0 ]] && log "OK: servidor - comando de arranque enviado." \
                  || log "FALHOU: servidor - ver erro da CLI acima."
}
 
server_off() {
  log "A desligar servidor $SERVER_ID..."
  stackit server stop "$SERVER_ID" -p "$PROJECT_ID" --assume-yes
  [[ $? -eq 0 ]] && log "OK: servidor - comando de paragem enviado." \
                  || log "FALHOU: servidor - ver erro da CLI acima."
}
 
server_status() {
  echo "--- servidor ($SERVER_ID) ---"
  stackit server describe "$SERVER_ID" -p "$PROJECT_ID"
}
 
# ------------------------------ CLUSTERS -------------------------------------
 
cluster_on() {
  local cluster="$1"
  log "A acordar (wakeup) cluster: $cluster..."
  stackit ske cluster wakeup "$cluster" -p "$PROJECT_ID" --assume-yes
  [[ $? -eq 0 ]] && log "OK: cluster $cluster - wakeup enviado." \
                  || log "FALHOU: cluster $cluster - ver erro da CLI acima."
}
 
cluster_off() {
  local cluster="$1"
  log "A hibernar (hibernate) cluster: $cluster..."
  stackit ske cluster hibernate "$cluster" -p "$PROJECT_ID" --assume-yes
  [[ $? -eq 0 ]] && log "OK: cluster $cluster - hibernate enviado." \
                  || log "FALHOU: cluster $cluster - ver erro da CLI acima."
}
 
cluster_status() {
  local cluster="$1"
  echo "--- cluster: $cluster ---"
  stackit ske cluster describe "$cluster" -p "$PROJECT_ID"
}
 
clusters_on()     { for c in "${CLUSTERS[@]}"; do cluster_on "$c"; done; }
clusters_off()    { for c in "${CLUSTERS[@]}"; do cluster_off "$c"; done; }
clusters_status() { for c in "${CLUSTERS[@]}"; do cluster_status "$c"; done; }
 
# ------------------------------ MAIN -----------------------------------------
 
ACTION="${1:-}"
ONLY="all"
if [[ "${2:-}" == "--only" && -n "${3:-}" ]]; then
  ONLY="$3"
fi
 
usage() {
  cat <<EOF
Uso: $0 <on|off|status> [--only server|clusters]
EOF
}
 
case "$ACTION" in
  on)
    log "=== LIGAR (only=$ONLY) ==="
    [[ "$ONLY" == "all" || "$ONLY" == "server" ]]   && server_on
    [[ "$ONLY" == "all" || "$ONLY" == "clusters" ]] && clusters_on
    ;;
  off)
    log "=== DESLIGAR (only=$ONLY) ==="
    [[ "$ONLY" == "all" || "$ONLY" == "clusters" ]] && clusters_off
    [[ "$ONLY" == "all" || "$ONLY" == "server" ]]   && server_off
    ;;
  status)
    [[ "$ONLY" == "all" || "$ONLY" == "server" ]]   && server_status
    [[ "$ONLY" == "all" || "$ONLY" == "clusters" ]] && clusters_status
    ;;
  *)
    usage
    exit 1
    ;;
esac