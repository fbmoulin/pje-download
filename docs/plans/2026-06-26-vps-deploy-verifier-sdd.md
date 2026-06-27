# Implementação: Deploy em VPS Genérico + Formalização do Spec Verifier (SDD)

> **Para Hermes:** Use `subagent-driven-development` para implementar este plano task-by-task.

**Goal:**  
Modernizar o fluxo de deploy do PJe Download para suportar qualquer VPS (abandonando o IP morto) e transformar a ferramenta `verify_spec.py` em uma feature estável e testada, seguindo o método Spec-Driven Development.

**Architecture:**  
- Fase 1: Refatorar `deploy.yml` para ser genérico (via secrets `VPS_HOST`), adicionar validação forte de secrets obrigatórios, melhorar smoke tests e documentar o novo modelo.
- Fase 2: Evoluir `tools/verify_spec.py` com testes, integração no CI, mais checks de qualidade e documentação oficial.

**Tech Stack:**  
Python 3.12, GitHub Actions, Docker Compose, Ruff, Pytest, Markdown specs.

**Scope:**  
- Apenas os arquivos de deploy e a ferramenta de verificação de specs.
- Não inclui migração física de VPS nem configuração de novos secrets.

---

## USER VALIDATION GATE (obrigatório)

**Preferência do usuário:** "validar primeiro, depois prossiga"

Este plano **só deve ser executado** após aprovação explícita de Felipe.  
Nenhuma tarefa deve ser iniciada antes do checkpoint de validação humana.

---

## Pesquisa & Contexto (já realizado)

- O VPS `191.252.204.250` está inativo (timeouts em `/healthz` e `/api/status`).
- O modelo atual (`deploy.yml`) faz `rsync` + `docker compose --profile worker`.
- O modelo documentado em `ops/monitoring/stack/DEPLOY.md` usa OpenClaw + Tailscale (mais moderno).
- Hetzner Cloud foi identificado como a melhor opção de custo-benefício para projetos com Docker + Playwright + Redis.
- A ferramenta `verify_spec.py` existe mas não tem testes nem integração no CI.

---

## Fase 1: Modernização do Deploy para VPS Genérico

### Task 1.1: Adicionar validação de secrets obrigatórios no `deploy.yml`

**Objective:** Garantir que o workflow falhe cedo se secrets críticos estiverem ausentes.

**Files:**
- Modify: `.github/workflows/deploy.yml:58-74`

**Step 1: Write failing test**

```yaml
# (Não é possível testar YAML de workflow diretamente com pytest)
# Validação será manual + smoke test no CI
```

**Step 2: Run test to verify failure**

```bash
# Simulação manual: remover secret temporariamente e rodar workflow (apenas documentar)
```

**Step 3: Write minimal implementation**

Adicionar bloco de validação logo após o `checkout`:

```yaml
- name: Validate required secrets
  run: |
    : "${{ secrets.VPS_SSH_KEY:?VPS_SSH_KEY is required }}"
    : "${{ secrets.VPS_HOST:?VPS_HOST is required }}"
    : "${{ secrets.VPS_USER:?VPS_USER is required }}"
    : "${{ secrets.MNI_USERNAME:?MNI_USERNAME is required }}"
    : "${{ secrets.MNI_PASSWORD:?MNI_PASSWORD is required }}"
    : "${{ secrets.REDIS_PASSWORD:?REDIS_PASSWORD is required (must not be default) }}"
    : "${{ secrets.DASHBOARD_API_KEY:?DASHBOARD_API_KEY is required }}"
  shell: bash
```

**Step 4: Run test to verify pass**

- Rodar workflow manualmente após adicionar os secrets.
- Verificar que o job falha imediatamente se algum secret estiver faltando.

**Step 5: Commit**

```bash
git add .github/workflows/deploy.yml
git commit -m "ci: adicionar validação de secrets obrigatórios no deploy"
```

---

### Task 1.2: Tornar o deploy genérico (remover referência ao IP antigo)

**Objective:** Eliminar qualquer menção hardcoded ao IP `191.252.204.250`.

**Files:**
- Modify: `README.md:464-466`
- Modify: `.github/workflows/deploy.yml`

**Step 1: Write failing test**

```bash
grep -r "191.252.204.250" . --include="*.md" --include="*.yml"
# Deve retornar resultados
```

**Step 2: Run test to verify failure**

```bash
# Executar grep acima → deve encontrar referências
```

**Step 3: Write minimal implementation**

- Remover tabela de "Produção (VPS)" do README ou marcar como obsoleta.
- Manter apenas referências genéricas via `${{ secrets.VPS_HOST }}`.

**Step 4: Run test to verify pass**

```bash
grep -r "191.252.204.250" . --include="*.md" --include="*.yml" || echo "Nenhuma referência hardcoded encontrada"
```

**Step 5: Commit**

```bash
git add README.md .github/workflows/deploy.yml
git commit -m "docs: remover IP hardcoded do VPS antigo"
```

---

### Task 1.3: Adicionar smoke test de autenticação MNI no deploy

**Objective:** Validar que as credenciais MNI estão funcionando após o deploy.

**Files:**
- Modify: `.github/workflows/deploy.yml` (após o smoke test de fila)

**Step 1: Write failing test**

```bash
# Teste manual via ssh no VPS após deploy
curl -s http://localhost:8007/health | jq '.mni'
```

**Step 2: Run test to verify failure**

```bash
# Executar no VPS e confirmar que retorna erro de autenticação quando credenciais estão erradas
```

**Step 3: Write minimal implementation**

Adicionar passo após o smoke test de fila:

```yaml
- name: Validate MNI credentials
  uses: appleboy/ssh-action@v1.2.5
  with:
    host: ${{ secrets.VPS_HOST }}
    username: ${{ secrets.VPS_USER }}
    key: ${{ secrets.VPS_SSH_KEY }}
    script: |
      docker compose exec -T dashboard python -c "
      from mni_client import MniClient
      client = MniClient()
      print('MNI client initialized successfully')
      "
```

**Step 4: Run test to verify pass**

- Fazer deploy com credenciais válidas → deve passar.
- Fazer deploy com credenciais inválidas → deve falhar.

**Step 5: Commit**

```bash
git add .github/workflows/deploy.yml
git commit -m "ci: adicionar validação de credenciais MNI no deploy"
```

---

## Fase 2: Evolução da Ferramenta `verify_spec.py`

### Task 2.1: Adicionar testes unitários para `verify_spec.py`

**Objective:** Criar suíte de testes para a ferramenta de verificação de specs.

**Files:**
- Create: `tests/test_verify_spec.py`

**Step 1: Write failing test**

```python
# tests/test_verify_spec.py
import pytest
from tools.verify_spec import validate_spec
from pathlib import Path

def test_validate_spec_passes_on_good_spec():
    spec = Path("docs/specs/sdd-pje-download.md")
    result = validate_spec(spec)
    assert result.passed == result.total
    assert len(result.failures) == 0
```

**Step 2: Run test to verify failure**

```bash
pytest tests/test_verify_spec.py -v
# Deve falhar porque o módulo ainda não exporta validate_spec
```

**Step 3: Write minimal implementation**

- Refatorar `tools/verify_spec.py` para exportar a função `validate_spec`.
- Manter CLI como entrada principal.

**Step 4: Run test to verify pass**

```bash
pytest tests/test_verify_spec.py::test_validate_spec_passes_on_good_spec -v
# Deve passar
```

**Step 5: Commit**

```bash
git add tests/test_verify_spec.py tools/verify_spec.py
git commit -m "test: adicionar testes para verify_spec.py"
```

---

### Task 2.2: Integrar `verify_spec.py` no CI

**Objective:** Executar verificação de specs automaticamente em todo push/PR.

**Files:**
- Modify: `.github/workflows/ci.yml`

**Step 1: Write failing test**

```bash
# Adicionar passo no job de lint
- name: Verify specs
  run: python tools/verify_spec.py docs/specs/*.md
```

**Step 2: Run test to verify failure**

```bash
# Rodar o workflow e verificar que falha se specs estiverem malformadas
```

**Step 3: Write minimal implementation**

Adicionar novo job ou passo no `ci.yml`:

```yaml
- name: Verify Markdown specs
  run: python tools/verify_spec.py docs/specs/*.md
  if: always()
```

**Step 4: Run test to verify pass**

- Fazer push com spec válida → CI passa.
- Fazer push com spec inválida → CI falha.

**Step 5: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: integrar verify_spec no pipeline de CI"
```

---

### Task 2.3: Expandir checks de qualidade do verifier

**Objective:** Adicionar verificações mais avançadas (frontmatter, seções obrigatórias, etc.).

**Files:**
- Modify: `tools/verify_spec.py`

**Step 1: Write failing test**

```python
def test_detects_missing_user_validation_gate():
    # spec sem a seção → deve falhar
```

**Step 2: Run test to verify failure**

**Step 3: Write minimal implementation**

Adicionar novos checks na lista `STRUCTURAL_CHECKS`:

```python
("Contains 'USER VALIDATION GATE' section", lambda c: "USER VALIDATION GATE" in c),
("Mentions subagent-driven-development", lambda c: "subagent-driven-development" in c),
```

**Step 4: Run test to verify pass**

**Step 5: Commit**

```bash
git add tools/verify_spec.py tests/test_verify_spec.py
git commit -m "feat: expandir checks de qualidade do spec verifier"
```

---

## Paralelismo Permitido

As seguintes tarefas podem ser executadas em paralelo (não compartilham arquivos):

- **Task 1.1** e **Task 1.2** (modificam seções diferentes do `deploy.yml`)
- **Task 2.1** e **Task 2.2** (testes e integração CI são independentes)

**NÃO paralelizar:**
- Task 1.3 com qualquer outra (depende do deploy funcional)
- Task 2.3 com Task 2.1 (depende da refatoração do módulo)

---

## Gate de Aprovação Final

Após completar todas as tarefas:

1. Rodar `ruff check` + `pytest`
2. Executar `python tools/verify_spec.py docs/specs/*.md`
3. Validar manualmente o workflow de deploy (via `workflow_dispatch`)
4. Apresentar resumo para o usuário antes de merge

---

**Status do plano:** Pronto para revisão e aprovação do usuário.

**Data:** 2026-06-26
**Método:** Spec-Driven Development + Skills Hermes