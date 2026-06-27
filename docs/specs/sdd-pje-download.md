# Spec-Driven Development (SDD) no PJe Download

> **Para Hermes:** Use `subagent-driven-development` + `writing-plans` + `plan-quality-gate` para implementar features seguindo este fluxo.

**Objetivo:**  
Estabelecer um processo padronizado, defensivo e auditável para desenvolvimento de features no repositório `pje-download`, combinando Spec-Driven Development (SDD) com as skills do Hermes e execução paralela controlada via `delegate_task`.

---

## 1. Definição de SDD neste ecossistema

**Spec-Driven Development (SDD)** significa:

> Escrever uma **especificação executável e revisável** (spec + plano de implementação detalhado) **antes** de qualquer código ser gerado ou modificado. A spec serve como contrato entre o usuário e os agentes de IA. Somente após aprovação explícita do usuário a execução é autorizada.

Características deste SDD adaptado:

- Pesquisa e raciocínio explícitos antes da spec
- Plano granular com TDD embutido em cada tarefa
- Revisão de qualidade em duas etapas (spec compliance → code quality)
- Uso de subagentes frescos por tarefa (`delegate_task`)
- Paralelismo controlado apenas quando tarefas são verdadeiramente independentes
- Gate obrigatório de validação humana ("validar primeiro, depois prossiga")

---

## 2. Fluxo Completo SDD + Skills + Paralelismo

```mermaid
flowchart TD
    A[Trigger: usuário ou backlog] --> B[Research & Context Gathering]
    B --> C[Reasoning & Design]
    C --> D[Escrever Spec + Plano<br/>skill: writing-plans]
    D --> E[Plan Quality Gate<br/>skill: plan-quality-gate]
    E --> F{USER VALIDATION GATE<br/>(obrigatório)}
    F -->|Aprovado| G[Execução via Subagent-Driven Development]
    F -->|Rejeitado| C
    G --> H[Fase 1: Tarefas independentes<br/>delegate_task em batch paralelo]
    G --> I[Fase 2: Tarefas dependentes<br/>execução sequencial + 2-stage review]
    H & I --> J[Fase 3: Integração final<br/>revisão global]
    J --> K[Auditoria & Validação]
    K --> L[Commit + documentação]
```

### Fases de Execução (Skill: `subagent-driven-development`)

| Fase | Tipo de Tarefa | Paralelismo | Revisão |
|------|----------------|-------------|---------|
| **Fase 1** | Tarefas independentes (não tocam os mesmos arquivos) | `delegate_task(tasks=[...])` em batch | Spec compliance + Code quality (podem ser paralelas) |
| **Fase 2** | Tarefas dependentes ou que modificam o mesmo módulo | Sequencial | 2-stage review obrigatória por tarefa |
| **Fase 3** | Integração final | — | Revisão global de consistência |

**Regra de ouro do paralelismo:**
- Tarefas que **não tocam os mesmos arquivos** → podem ser despachadas em batch
- Tarefas que **dependem uma da outra** ou **modificam o mesmo módulo** → execução sequencial obrigatória
- Revisões (spec compliance + code quality) de uma mesma tarefa → podem ser paralelas (duas folhas)

---

## 3. Skills Envolvidas

| Skill | Papel no fluxo | Quando usar |
|-------|----------------|-------------|
| `writing-plans` | Gera spec + plano de implementação granular com TDD | Sempre antes de qualquer feature |
| `plan-quality-gate` | Valida estrutura do plano (evita "run pytest on .md") | Após `writing-plans`, antes de apresentar ao usuário |
| `subagent-driven-development` | Executa o plano via subagentes com 2-stage review | Após aprovação do usuário |
| `test-driven-development` | Enforça ciclo RED-GREEN-REFACTOR | Dentro de cada tarefa do plano |
| `requesting-code-review` | Revisão pré-commit (segurança, qualidade) | Opcional na Fase 3 |

---

## 4. Estrutura Obrigatória da Spec

Toda spec gerada deve seguir o template de `writing-plans` e incluir seção explícita de **USER VALIDATION GATE**.

### Elementos mínimos obrigatórios

1. **Header**
   - Goal (uma frase)
   - Architecture (2-3 frases)
   - Tech Stack
   - Scope (quando aplicável)

2. **USER VALIDATION GATE** (obrigatório)
   - Deve aparecer **antes** de qualquer Task
   - Deve referenciar explicitamente a preferência do usuário ("validar primeiro, depois prossiga")

3. **Tarefas bite-sized** (2–5 min cada)
   - Objective (uma frase)
   - Files (Create / Modify / Test com paths exatos)
   - Passos TDD completos:
     - Step 1: Write failing test
     - Step 2: Run test to verify failure
     - Step 3: Write minimal implementation
     - Step 4: Run test to verify pass
     - Step 5: Commit
   - Verification command (exato)
   - Commit message (convencional em português)

4. **Nota de paralelismo**
   - Listar explicitamente quais tarefas podem rodar em batch

5. **Referência à skill**
   - Mencionar `subagent-driven-development` quando o plano for complexo

---

## 5. Exemplo de Nota de Paralelismo (em specs)

```markdown
## Paralelismo Permitido

As seguintes tarefas podem ser despachadas em batch via `delegate_task(tasks=[...])`:

- Task 2 e Task 3 (não compartilham arquivos)
- Task 5.1 e Task 5.2 (módulos distintos)

**NÃO paralelizar:**
- Task 4 e Task 6 (dependem do resultado da Task 4)
- Task 7 (modifica `dashboard_api.py` que é tocado por Task 1)
```

---

## 6. Princípios e Restrições

### Princípios

- **Fresh subagent per task** — evita poluição de contexto
- **Two-stage review** — spec compliance primeiro, code quality depois
- **Never skip reviews** — mesmo em tarefas "simples"
- **Bite-sized tasks** — 2-5 minutos de trabalho focado
- **TDD obrigatório** — em toda tarefa que produz código
- **Frequent commits** — após cada tarefa

### Restrições

- **Nunca** despachar múltiplos implementadores para tarefas que tocam os mesmos arquivos
- **Nunca** pular o USER VALIDATION GATE
- **Nunca** aceitar `PASS` de spec reviewer quando o arquivo não foi encontrado
- **Sempre** verificar se o edit realmente aconteceu (grep/diff) antes de avançar
- **Sempre** usar `lazy import` para dependências pesadas/opcionais (playwright, litellm, docling, fitz, etc.)

---

## 7. Integração com o Projeto PJe Download

Este fluxo SDD se aplica especialmente a:

- Mudanças na coordenação dashboard ↔ worker (protocolo Redis, `_progress.json`, `_report.json`)
- Novas estratégias de download ou circuit breakers
- Integração com Kratos Case Pipeline
- Expansão de métricas Prometheus ou alertas
- Recuperação de batch ativo e resiliência de fila
- Migrações Alembic (ver `references/alembic-migration-chain-pitfalls.md`)

---

## 8. Referências

- `software-development/subagent-driven-development/SKILL.md`
- `software-development/writing-plans/SKILL.md`
- `software-development/plan-quality-gate/SKILL.md`
- `references/alembic-migration-chain-pitfalls.md`
- `references/gates-taxonomy.md`
- `references/context-budget-discipline.md`

---

**Status:** Especificação formal do método SDD para o repositório `pje-download`.

**Data:** 2026-06-26
**Autor:** Hermes (baseado em pesquisa + skills existentes)