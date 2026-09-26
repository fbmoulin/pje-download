#!/usr/bin/env python3
"""Sonda do MVP: quanto tempo o app consegue "assumir" depois do login manual?

Mede duas coisas que decidem o desenho do MVP (login manual, sem MNI):
  1. se uma sessão salva num navegador VISÍVEL continua valendo num navegador
     INVISÍVEL (headless), que é como o app trabalharia depois;
  2. quanto tempo até o PJe/TJES pedir login de novo ou a verificação anti-robô
     da AWS ("Let's confirm you are human") voltar.

Só abre a página de login (logado, o PJe redireciona para dentro). Não baixa documento, não imprime valor de cookie.
Sessão salva em downloads/mvp/session.json (gitignored, permissão 600).

Uso:
    python tools/mvp_session_probe.py login          # abre o navegador; faça o login
    python tools/mvp_session_probe.py watch [min]    # testa a cada [min] minutos (padrão 5)
    python tools/mvp_session_probe.py watch 5 --headed
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import config  # noqa: E402

config.load_env()
SESSION = ROOT / "downloads" / "mvp" / "session.json"
# Mesma checagem do worker: logado, o PJe tira você da página de login.
LOGIN = config.PJE_BASE_URL + "/login.seam"


def _cookie_summary() -> None:
    state = json.loads(SESSION.read_text(encoding="utf-8"))
    now = time.time()
    print("Cookies salvos (só nome, domínio e validade):")
    for c in sorted(state.get("cookies", []), key=lambda c: c["name"]):
        exp = c.get("expires", -1)
        left = "sessão" if exp in (-1, None) else f"{(exp - now) / 60:.0f} min"
        print(f"  {c['name']:<28} {c['domain']:<28} {left}")


async def _check(headed: bool) -> str:
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=not headed)
        ctx = await browser.new_context(storage_state=str(SESSION))
        page = await ctx.new_page()
        try:
            await page.goto(LOGIN, wait_until="networkidle", timeout=60_000)
            html = (await page.content()).lower()
            host = page.url.split("/")[2]
            path = page.url.split("?")[0].split(host, 1)[1]
            if "awswaf" in html or "confirm you are human" in html:
                return "WAF (verificação anti-robô)"
            if "sso.cloud.pje.jus.br" in host or "login" in path.lower():
                return f"LOGIN pedido ({host}{path})"
            return f"OK ({path})"
        finally:
            await browser.close()


async def _main() -> int:
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "login":
        from pje_session import interactive_login

        ok = await interactive_login(session_file=SESSION)
        if ok:
            _cookie_summary()
            print("Agora rode: python tools/mvp_session_probe.py watch")
        return 0 if ok else 1

    if cmd == "watch":
        if not SESSION.exists():
            print(
                "Falta a sessão. Rode primeiro: python tools/mvp_session_probe.py login"
            )
            return 3
        minutes = (
            float(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2][0].isdigit() else 5
        )
        headed = "--headed" in sys.argv
        t0 = time.monotonic()
        print(
            f"Testando a cada {minutes:g} min ({'visível' if headed else 'invisível'}). Ctrl+C para parar."
        )
        while True:
            verdict = await _check(headed)
            elapsed = (time.monotonic() - t0) / 60
            print(
                f"{time.strftime('%H:%M')}  +{elapsed:5.1f} min  {verdict}", flush=True
            )
            if not verdict.startswith("OK"):
                print(
                    f"Resultado: a sessão durou cerca de {elapsed:.0f} min neste modo."
                )
                return 0
            await asyncio.sleep(minutes * 60)

    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
