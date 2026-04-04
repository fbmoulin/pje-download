# pje-download: Deploy Fix + Frontend Redesign
**Date:** 2026-04-03 (afternoon session)
**Status:** COMPLETE — both shipped and live at 191.252.204.250:8007

## 1. Deploy Fix (commit ba4081c)

**Root cause:** `pje-redis` had `ports: ["6379:6379"]` but another kratos stack already bound port 6379.
**Fix:** Removed `ports` block from `pje-redis` in `docker-compose.yml` — Redis is internal-only via `pje-net`.

## 2. Frontend Redesign (commit c712b10)

**Aesthetic:** "Precision Judicial Ops" — Kratos orange preserved, dark mode elevated.

### Fonts loaded (Google Fonts, 1 link tag)
- Figtree 400-800 — UI text (replaces Inter)
- Oswald 600-700 — KPI numbers (4rem, condensed, editorial)
- DM Mono 400-500 — process IDs, clock

### New CSS tokens added to :root
```
--font-kpi, --glass-bg, --glass-border, --glass-shadow, --dot-color
```

### Key changes
- Body: dot-grid texture via `radial-gradient` 24px×24px at 2.8% opacity
- Cards + KPI tiles: glassmorphism `backdrop-filter: blur(10-12px)`, glass border
- KPI: 4rem Oswald, left-aligned, 3px orange gradient stripe (`.kpi__stripe`)
- Status badge: ripple `box-shadow` ring animation replaces opacity fade
- Progress bar: `::before` segment dividers + orange→amber→green gradient
- Pipeline: SVG `<line>+<polygon>` connectors + animated dot on active step
- Header: SVG K icon, terminal-pill clock, 80px orange `::after` underline flash
- Empty states: inline SVGs replace HTML entities
- Page load: staggered entrance on 5 sections via `[data-animate]` (0→320ms)
- JS: 2-line change in `renderPhase()` only — all IDs/classes preserved

## Commits this session
- ba4081c — fix: remove Redis host port binding
- c712b10 — feat: dashboard frontend redesign (Precision Judicial Ops)

## pje-download current state
- v1.3, 69 tests, deployed VPS :8007, CI/CD via GitHub Actions
- Gap analysis: all 13 gaps resolved
- Prometheus metrics live at /metrics
