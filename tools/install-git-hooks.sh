#!/usr/bin/env bash
# Instala os hooks versionados de tools/git-hooks/ em .git/hooks/.
#
# POR QUE ESTE SCRIPT EXISTE
# --------------------------
# .git/hooks/ NAO e versionado: um hook que vive so ali desaparece no proximo
# clone, em outra maquina, ou se alguem apagar o diretorio — e some em silencio,
# sem nenhum sinal de que uma protecao deixou de existir. Manter o hook em
# tools/git-hooks/ (versionado) + este instalador torna a protecao visivel no
# repositorio e reinstalavel em um comando.
#
# Rode depois de todo clone novo:
#     bash tools/install-git-hooks.sh

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
ORIGEM="$REPO_ROOT/tools/git-hooks"
DESTINO="$REPO_ROOT/.git/hooks"

[ -d "$ORIGEM" ] || { echo "🔴 $ORIGEM nao existe"; exit 1; }
mkdir -p "$DESTINO"

for hook in "$ORIGEM"/*; do
  nome="$(basename "$hook")"
  cp "$hook" "$DESTINO/$nome"
  chmod +x "$DESTINO/$nome"
  echo "✅ instalado: $nome"
done

echo ""
echo "Verificando pre-requisitos do pre-push:"
GITLEAKS="${GITLEAKS_BIN:-$HOME/.local/bin/gitleaks}"
if [ -x "$GITLEAKS" ]; then
  echo "  ✅ gitleaks: $("$GITLEAKS" version)"
else
  echo "  🔴 gitleaks AUSENTE em $GITLEAKS"
  echo "     O hook falha FECHADO: sem scanner, todo push sera bloqueado."
  echo "     Instale o binario do release em ~/.local/bin/ ou aponte GITLEAKS_BIN."
fi
python3 -c 'import sys' 2>/dev/null && echo "  ✅ python3 disponivel (validador de CPF/CNPJ)"
