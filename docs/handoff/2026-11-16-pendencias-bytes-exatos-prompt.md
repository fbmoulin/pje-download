# Prompt de retomada (supersede qualquer outro prompt de retomada)

Cole o bloco abaixo numa sessão nova.

```
Leia primeiro /Users/felipemoulin/pje-download/docs/handoff/2026-11-16-pendencias-bytes-exatos.md, depois TODO.md (seção "Aberto agora") e a memória mni-tjes-omite-documentos.

Medir antes de confiar (todo número abaixo é pista datada de 2026-11-21):
- git status -sb; git log --oneline -5; gh run list --branch master --limit 5
- ssh pje-vps "curl -s localhost:8007/healthz" (build_sha esperado: feec21f, #126; o handoff cita 3aea984, que já está defasado)
- gh pr list (o #127, bump do playwright pelo Dependabot, estava aberto: é PR de terceiro, só mergear com sim do dono, depois de update-branch e de checar o Dockerfile/deps do worker)

Decisões fechadas (não reabrir):
- Escopo é só TJES.
- #124 provado em produção: 66/66 PDFs do 5007796 batem com o MD5 declarado; a divergência de páginas não é bug nosso.
- Os 5 documentos "não entregues" são HIPÓTESE (embutidos nos PDFs 94383092/93/95); só o dono confirma no PJe web.
- Autorizado: commits, PRs e merge quando o CI estiver verde (PR do agente). Não rodar lote nem reiniciar nada na VPS sem autorização.
- Rotação da REDIS_PASSWORD foi adiada pelo dono.

Abertos (só o dono): conferência dos 3 PDFs no PJe web; sudo snap remove tailscale; watchdog; rotacionar REDIS_PASSWORD/chave SSH/DASHBOARD_API_KEY; sink de auditoria; remover ops/monitoring/. Menores: current_batch do /healthz; imagem alpine na VPS; branches locais worktree-agent-* (6) sem apagar.

Pergunte ao dono se a conferência no PJe web já foi feita. Explique em português, para iniciante, com opções e recomendação.
```
