#!/usr/bin/env bash
# Teste real local (macOS/Linux): credenciais MNI + download de UM processo.
#
# Pré-requisito: .env na raiz do repo com MNI_USERNAME e MNI_PASSWORD preenchidos
# (cp .env.example .env). Nada aqui imprime credenciais.
#
# Uso:
#   bash tools/smoke_real_local.sh                 # só valida as credenciais
#   bash tools/smoke_real_local.sh <numero-CNJ>    # valida e baixa um processo
#
# Saída: ./downloads/smoke/ (gitignored). Auditoria CNJ 615: ./downloads/audit/.
set -euo pipefail

cd "$(dirname "$0")/.."

if [ ! -f .env ]; then
  echo "ERRO: falta .env. Rode: cp .env.example .env  e preencha MNI_USERNAME e MNI_PASSWORD." >&2
  exit 3
fi
for var in MNI_USERNAME MNI_PASSWORD; do
  val=$(grep -E "^${var}=" .env | head -1 | cut -d= -f2- | sed -E 's/[[:space:]]+#.*$//; s/[[:space:]]+$//')
  if [ -z "$val" ] || [ "$val" = "12345678900" ] || [ "$val" = "senha_mni" ]; then
    echo "ERRO: ${var} vazio ou ainda com o valor de exemplo no .env." >&2
    exit 3
  fi
done

# Sem isto a auditoria tenta /data/audit, que não existe fora do Docker, e falha em silêncio.
export AUDIT_LOG_DIR="${AUDIT_LOG_DIR:-$PWD/downloads/audit}"
export DOWNLOAD_BASE_DIR="${DOWNLOAD_BASE_DIR:-$PWD/downloads}"
mkdir -p "$AUDIT_LOG_DIR" downloads/smoke

VENV="${PJE_VENV:-$HOME/.cache/pje-download-venv}"
if [ ! -x "$VENV/bin/python" ]; then
  echo "Criando venv em $VENV (uma vez só)..."
  uv venv -q -p 3.12 "$VENV"
  uv pip install -q -p "$VENV/bin/python" -r requirements.txt
fi
PY="$VENV/bin/python"

echo "== 1/2 Credenciais MNI (uma chamada consultarProcesso real, número inexistente)"
set +e
"$PY" tools/verify_mni_credentials.py
rc=$?
set -e
case $rc in
  0) echo "OK: credenciais válidas." ;;
  1) echo "FALHA: o tribunal rejeitou as credenciais." >&2; exit 1 ;;
  *) echo "INCONCLUSIVO (código $rc): rede, timeout ou configuração." >&2; exit 2 ;;
esac

if [ $# -lt 1 ]; then
  echo "Credenciais conferidas. Para baixar um processo: bash tools/smoke_real_local.sh <numero-CNJ>"
  exit 0
fi

echo "== 2/2 Download real de um processo"
"$PY" batch_downloader.py -p "$1" -o downloads/smoke --no-resume

echo
echo "Arquivos baixados:"
find downloads/smoke -type f ! -name "*.json" | wc -l | tr -d ' '
du -sh downloads/smoke | cut -f1
echo "Linhas de auditoria gravadas hoje:"
cat "$AUDIT_LOG_DIR"/audit-"$(date +%F)".jsonl 2>/dev/null | wc -l | tr -d ' '
echo "Abra a pasta com: open downloads/smoke"
