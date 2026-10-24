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
# ssh falhar (VPS fora do ar, chave recusada, travado além do limite), este
# script sai com 1: a queda da VPS vira alerta sem nenhuma checagem extra. A
# mensagem de falha é fixa (+ código de saída); nada do stderr do ssh vai ao chat.
set -u

case "$(basename "$0")" in
  *digest*) MODE=digest ;;
  *) MODE=check ;;
esac

KEY="${PJE_WATCHDOG_KEY:-$HOME/.ssh/pje_watchdog_ed25519}"
TARGET="${PJE_WATCHDOG_TARGET:-deploy@pje-vps}"
# Limite externo de tempo do ssh inteiro (s). ConnectTimeout/ServerAlive* não
# cobrem um servidor que conecta e fica pingando. Sem `timeout` do GNU no macOS:
# o alarm do perl sobrevive ao exec e mata o ssh com SIGALRM (rc 142).
LIMIT="${PJE_WATCHDOG_TIMEOUT:-120}"

SSH_ARGS=(
  -i "$KEY"
  -o BatchMode=yes
  -o IdentitiesOnly=yes
  -o StrictHostKeyChecking=yes
  -o ConnectTimeout=20
  -o ServerAliveInterval=10
  -o ServerAliveCountMax=3
  "$TARGET" "$MODE"
)

# A chave só executa o watchdog na VPS (comando forçado no authorized_keys);
# o argumento acima é apenas uma palavra que o watchdog compara, nunca executa.
#
# stderr do ssh NUNCA vai para o stdout: ele traz avisos do cliente (rc 0) e, nas
# falhas, o IP real da VPS e o caminho do known_hosts, e o stdout vai ao Telegram.
if command -v perl >/dev/null 2>&1; then
  out=$(perl -e 'alarm shift; exec @ARGV or exit 127' "$LIMIT" ssh "${SSH_ARGS[@]}" 2>/dev/null </dev/null)
else
  out=$(ssh "${SSH_ARGS[@]}" 2>/dev/null </dev/null)
fi
rc=$?

if [ "$rc" -ne 0 ]; then
  echo "WATCHDOG: falha ao consultar a VPS (ssh/script saiu com $rc)"
  # Só as linhas que o PRÓPRIO watchdog imprime em erro (já sem dados sensíveis).
  printf '%s\n' "$out" | grep -E '^(WATCHDOG: |\[ALERTA\] watchdog )' | head -n 3 | cut -c1-200
  exit 1
fi

[ -n "$out" ] && printf '%s\n' "$out"
exit 0
