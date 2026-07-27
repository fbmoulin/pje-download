#!/usr/bin/env bash
# Verifica as regras de INFRAESTRUTURA do .gitleaks.toml nas DUAS direcoes.
#
# POR QUE ESTE ARQUIVO EXISTE
# ---------------------------
# Uma regra de gate so tem duas maneiras de estar errada, e elas sao opostas:
# ela pode nao pegar o que deveria (falso negativo, o gate vira teatro) ou pegar
# o que nao deveria (falso positivo, e ai alguem allowlista de mais para
# destravar o trabalho, o que produz o primeiro caso). Medir so um lado nao
# distingue "regra boa" de "regra desligada".
#
# Essa licao veio do PR #38: o hook pre-push acusava PII sem ter varrido nada, e
# o unico sinal era a AUSENCIA de achado impresso. O fix so foi aceito depois de
# provar que branch limpa passa E que CPF valido continua bloqueado.
#
# As fixtures sao SINTETICAS e ficam inline neste arquivo, nao em disco, para
# que nenhum dado parecido com PII more na arvore. O IP usado (45.33.32.156) e
# publico e roteavel — de proposito, porque uma faixa de documentacao esta
# allowlistada e provaria a regra errada. Ele nao pertence a nenhum host deste
# projeto.
#
# ⚠️ Este arquivo esta no `paths` do allowlist global do .gitleaks.toml, senao
# ele acusaria a si mesmo. Mesma justificativa das outras duas entradas la.
#
# Uso: bash tools/verify_gitleaks_rules.sh
# Saida: exit 0 se as duas direcoes passarem, 1 caso contrario.

set -uo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
CONFIG="$REPO_ROOT/.gitleaks.toml"
GITLEAKS="${GITLEAKS_BIN:-$HOME/.local/bin/gitleaks}"
command -v gitleaks >/dev/null 2>&1 && GITLEAKS="$(command -v gitleaks)"

# FALHA FECHADA. Um verificador de guarda que "pula" quando a ferramenta falta
# reporta verde sem ter verificado nada — exatamente o modo de falha que ele
# existe para detectar.
if [ ! -x "$GITLEAKS" ]; then
  echo "🔴 gitleaks nao encontrado em '$GITLEAKS'. Instale ou aponte GITLEAKS_BIN." >&2
  echo "   Este script NAO pula: um guarda nao verificado e um guarda ausente." >&2
  exit 1
fi

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
FAILED=0

scan() {  # scan <arquivo> -> imprime os RuleIDs encontrados, um por linha
  "$GITLEAKS" dir "$1" --config "$CONFIG" --no-banner --redact \
    --report-format json --report-path "$WORK/out.json" >/dev/null 2>&1
  python3 -c "
import json,sys
try: d=json.load(open('$WORK/out.json'))
except Exception: d=[]
print('\n'.join(sorted({f['RuleID'] for f in d})))
"
}

check() {  # check <nome> <deve-disparar: yes|no> <regra> <conteudo>
  local nome="$1" espera="$2" regra="$3" conteudo="$4"
  local dir="$WORK/case"
  rm -rf "$dir"; mkdir -p "$dir"
  printf '%s\n' "$conteudo" > "$dir/sample.txt"
  local hits; hits="$(scan "$dir")"
  local achou="no"
  printf '%s\n' "$hits" | grep -qx "$regra" && achou="yes"
  if [ "$achou" = "$espera" ]; then
    printf '  ✅ %-46s (%s: esperado %s)\n' "$nome" "$regra" "$espera"
  else
    printf '  ❌ %-46s (%s: esperado %s, obtido %s)\n' "$nome" "$regra" "$espera" "$achou"
    FAILED=1
  fi
}

echo "Direcao 1 — a regra PEGA o que deveria:"
check "IP publico de host" yes infra-public-ipv4 "deploy target is 45.33.32.156 port 8007"
check "e-mail .jus.br"     yes infra-jusbr-email "contato: fulano.silva@tjes.jus.br"
check "convite ssh -i (host nomeado)" yes infra-ssh-invite \
  "ssh -i ~/.ssh/pje_deploy deploy@vps.exemplo.com.br"
check "convite ssh -i (host por IP)"  yes infra-ssh-invite \
  "ssh -i ~/.ssh/pje_deploy deploy@45.33.32.156"

echo "Direcao 2 — a regra NAO pega o que e legitimo:"
check "RFC1918 (rede interna docker)" no infra-public-ipv4 "REDIS_URL=redis://10.0.0.5:6379"
check "loopback"                      no infra-public-ipv4 "ports: 127.0.0.1:8007:8007"
check "wildcard bind"                 no infra-public-ipv4 "HEALTH_BIND_HOST=0.0.0.0"
check "versao de 4 partes (User-Agent)" no infra-public-ipv4 \
  'user_agent = "Mozilla/5.0 Chrome/120.0.0.0 Safari/537.36"'
check "faixa de documentacao RFC 5737" no infra-public-ipv4 "exemplo: 203.0.113.10"
check "IP morto mantido de proposito"  no infra-public-ipv4 \
  "o VPS antigo era 2.24.126.161 (Boston, desativado)"
check "URL .jus.br sem @"              no infra-jusbr-email "PJE_BASE_URL=https://pje.tjes.jus.br/pje"
check "ssh -i com host TEMPLATE"       no infra-ssh-invite \
  'ssh -i ~/.ssh/deploy_key "${VPS_USER}@${VPS_HOST}"'

if [ "$FAILED" -eq 0 ]; then
  echo ""
  echo "✅ 12/12 — as regras de infraestrutura pegam o alvo e poupam o legitimo."
  exit 0
fi
echo ""
echo "🔴 alguma regra saiu do lugar. NAO relaxe o regex antes de entender qual direcao quebrou." >&2
exit 1
