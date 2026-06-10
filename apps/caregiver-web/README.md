# apps/caregiver-web

Hearth caregiver status dashboard — the "Mom is okay" at-a-glance app. Built
with React + Vite (per the architecture invariant in `CLAUDE.md`).

**Status:** scaffold (BAT-20). Real status views land in a later issue.

## Privacy

The caregiver app shows **coarse status and activity blocks only** — never
cameras, microphones, or per-second sensor data. That constraint is the product.

## Develop

```bash
npm install
npm run dev      # local dev server
npm run build    # production build — this is the CI smoke gate
```
