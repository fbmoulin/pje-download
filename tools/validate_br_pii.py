#!/usr/bin/env python3
"""Segunda etapa da varredura de PII brasileira: valida digito verificador.

POR QUE ESTE ARQUIVO EXISTE
---------------------------
O gitleaks casa regex, entropia e allowlist, mas NAO executa codigo. Isso e um
problema para CPF e CNPJ *sem pontuacao*: `\\b\\d{11}\\b` casa com qualquer
timestamp em milissegundos, numero de protocolo ou id de banco, e bloquear por
esse padrao seria ruido puro — e um guarda que grita lobo acaba desligado.

A saida e dividir em duas etapas:
  1. gitleaks pega as formas PONTUADAS (baixo falso positivo) — .gitleaks.toml
  2. este script pega as formas NUAS e confirma pelo digito verificador

Le um diff unificado no stdin (tipicamente `git diff <range>`) e olha apenas as
linhas ADICIONADAS. Sai com codigo 1 se encontrar CPF ou CNPJ valido.

Uso:
    git diff origin/master..HEAD | python3 tools/validate_br_pii.py
    python3 tools/validate_br_pii.py --self-test

O que este script NAO cobre: nome de pessoa. Nao ha regex viavel, e a decisao
registrada (com sign-off) e contra usar NER. Continua sendo o buraco residual.
"""

from __future__ import annotations

import re
import sys

# Candidatos NUS. As formas pontuadas ficam com o gitleaks.
RE_CPF_NU = re.compile(r"(?<!\d)\d{11}(?!\d)")
RE_CNPJ_NU = re.compile(r"(?<![A-Z0-9])[A-Z0-9]{12}\d{2}(?![A-Z0-9])")


def cpf_valido(cpf: str) -> bool:
    """Valida CPF por modulo 11.

    Rejeita sequencias de digitos repetidos: 111.111.111-11 e afins PASSAM no
    modulo 11 mas sao CPFs invalidos, e aparecem o tempo todo em dado de teste.
    Sem esse filtro o gate dispararia em fixture constantemente.
    """
    d = [int(c) for c in cpf if c.isdigit()]
    if len(d) != 11 or len(set(d)) == 1:
        return False
    for n in (9, 10):  # 1o DV usa 9 digitos, 2o usa 10
        soma = sum(d[i] * (n + 1 - i) for i in range(n))
        resto = soma % 11
        dv = 0 if resto < 2 else 11 - resto
        if d[n] != dv:
            return False
    return True


def cnpj_valido(cnpj: str) -> bool:
    """Valida CNPJ numerico OU alfanumerico (vigente a partir de 31/07/2026).

    No formato alfanumerico (IN RFB 2.229/2024) as 12 primeiras posicoes aceitam
    A-Z; os 2 DVs continuam numericos. O calculo e o modulo 11 de sempre, mas
    sobre o valor ASCII do caractere menos 48 — o que reduz digitos ao proprio
    valor e da as letras uma faixa acima, mantendo o algoritmo legado correto.
    """
    s = "".join(c for c in cnpj.upper() if c.isalnum())
    if len(s) != 14 or not s[12:].isdigit():
        return False
    if len(set(s[:12])) == 1:  # mesma armadilha do CPF: fixture repetida
        return False
    v = [ord(c) - 48 for c in s]
    for n in (12, 13):
        pesos = [((n - 1 - i) % 8) + 2 for i in range(n)]
        resto = sum(v[i] * pesos[i] for i in range(n)) % 11
        dv = 0 if resto < 2 else 11 - resto
        if v[n] != dv:
            return False
    return True


def achados_no_diff(diff: str) -> list[tuple[int, str, str]]:
    """Retorna (numero da linha no diff, tipo, valor) para cada PII confirmada."""
    achados: list[tuple[int, str, str]] = []
    for i, linha in enumerate(diff.splitlines(), 1):
        # Só linhas adicionadas. '+++' e cabecalho de arquivo, nao conteudo.
        if not linha.startswith("+") or linha.startswith("+++"):
            continue
        for m in RE_CPF_NU.finditer(linha):
            if cpf_valido(m.group()):
                achados.append((i, "CPF", m.group()))
        for m in RE_CNPJ_NU.finditer(linha):
            if cnpj_valido(m.group()):
                achados.append((i, "CNPJ", m.group()))
    return achados


def _mascara(valor: str) -> str:
    """Nunca imprime a PII inteira — o log do gate nao pode virar o vazamento."""
    return f"{valor[:3]}{'*' * (len(valor) - 5)}{valor[-2:]}"


def _self_test() -> int:
    casos_cpf = [
        ("52998224725", True, "CPF valido conhecido"),
        ("11144477735", True, "CPF valido conhecido"),
        ("11111111111", False, "digitos repetidos: passa no mod 11, e invalido"),
        ("12345678900", False, "DV errado"),
        ("1699999999999", False, "tamanho errado"),
        ("00000000000", False, "zeros"),
    ]
    casos_cnpj = [
        ("11222333000181", True, "CNPJ numerico valido"),
        ("11222333000180", False, "DV errado"),
        ("11111111111111", False, "repetido"),
    ]
    falhas = 0
    for valor, esperado, desc in casos_cpf:
        obtido = cpf_valido(valor)
        ok = obtido == esperado
        falhas += 0 if ok else 1
        print(
            f"{'OK  ' if ok else 'ERRO'} cpf_valido({_mascara(valor)}) = {obtido} — {desc}"
        )
    for valor, esperado, desc in casos_cnpj:
        obtido = cnpj_valido(valor)
        ok = obtido == esperado
        falhas += 0 if ok else 1
        print(
            f"{'OK  ' if ok else 'ERRO'} cnpj_valido({_mascara(valor)}) = {obtido} — {desc}"
        )

    # O teste que importa: o script tem de ACHAR num diff realista, e tem de
    # ficar quieto num diff limpo. Um validador que so diz "nao" sempre passaria
    # nos casos negativos acima sem servir para nada.
    diff_sujo = "+++ b/x.py\n+cliente = '52998224725'\n-antigo = '11144477735'\n"
    diff_limpo = "+++ b/x.py\n+timestamp = 1699999999999\n+total = 12345\n"
    n_sujo = len(achados_no_diff(diff_sujo))
    n_limpo = len(achados_no_diff(diff_limpo))
    ok_sujo, ok_limpo = n_sujo == 1, n_limpo == 0
    falhas += 0 if ok_sujo else 1
    falhas += 0 if ok_limpo else 1
    print(
        f"{'OK  ' if ok_sujo else 'ERRO'} diff com 1 CPF em linha adicionada -> {n_sujo} achado(s) (linha removida ignorada)"
    )
    print(
        f"{'OK  ' if ok_limpo else 'ERRO'} diff limpo (timestamp de 13 digitos) -> {n_limpo} achado(s)"
    )

    print("-" * 50)
    print("TODOS OS TESTES PASSARAM" if falhas == 0 else f"{falhas} FALHA(S)")
    return 0 if falhas == 0 else 1


def main() -> int:
    if "--self-test" in sys.argv:
        return _self_test()

    achados = achados_no_diff(sys.stdin.read())
    if not achados:
        return 0

    print("PII BRASILEIRA CONFIRMADA NO QUE ESTA SENDO ENVIADO:", file=sys.stderr)
    for linha, tipo, valor in achados:
        print(f"  linha {linha} do diff: {tipo} {_mascara(valor)}", file=sys.stderr)
    print(
        "\nDigito verificador confere — nao e coincidencia numerica.\n"
        "Remova ou mascare ANTES do commit: o historico do git preserva o dado\n"
        "mesmo que um commit posterior o apague.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
