#!/bin/bash
# Wrapper do watchdog da VPS para o cron do Hermes (roda no Mac mini).
#
# Instalação (uma vez, ver docs/ops/hermes-watchdog.md):
#   cp ops/hermes/pje-watchdog.sh ~/.hermes/scripts/pje-watchdog.sh
#   cp ops/hermes/pje-watchdog.sh ~/.hermes/scripts/pje-watchdog-digest.sh
#   chmod +x ~/.hermes/scripts/pje-watchdog*.sh
#
# O modo vem do NOME do arquivo: se contém "digest", pede o resumo diário;
# caso contrário, a checagem normal. (O cron sem LLM do Hermes não passa
# argumentos ao script.)
#
# Contrato com o Hermes: saída vazia = nada a avisar; saída com texto = vai ao
# Telegram; código diferente de zero = o Hermes envia um alerta de erro. Se o
# ssh falhar (VPS fora do ar, chave recusada), este script sai com 1: a queda
# da VPS vira alerta sem nenhuma checagem extra.
set -u

case "$(basename "$0")" in
  *digest*) MODE=digest ;;
  *) MODE=check ;;
esac

KEY="${PJE_WATCHDOG_KEY:-$HOME/.ssh/pje_watchdog_ed25519}"
TARGET="${PJE_WATCHDOG_TARGET:-deploy@pje-vps}"

# A chave só executa o watchdog na VPS (comando forçado no authorized_keys);
# o argumento abaixo é apenas uma palavra que o watchdog compara, nunca executa.
out=$(ssh -i "$KEY" \
  -o BatchMode=yes \
  -o IdentitiesOnly=yes \
  -o StrictHostKeyChecking=yes \
  -o ConnectTimeout=20 \
  -o ServerAliveInterval=10 \
  -o ServerAliveCountMax=3 \
  "$TARGET" "$MODE" 2>&1)
rc=$?

if [ "$rc" -ne 0 ]; then
  echo "WATCHDOG: falha ao consultar a VPS (ssh/script saiu com $rc): ${out:0:300}"
  exit 1
fi

[ -n "$out" ] && printf '%s\n' "$out"
exit 0
