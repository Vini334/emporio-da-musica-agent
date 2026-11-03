# Empório da Música — customer service agent

Artefact AI Engineer technical challenge. Deadline: Wednesday 2026-11-04; no commits after the delivery.

- **Spec** (`docs/SPEC.md`): read it before implementing or changing anything. It is the source of truth for the business rules (BR-nn), the tool contracts, the module layout and the phase status. Mark a phase as ✅ in §15 when it is done.
- **Decisions** (`docs/design-decisions.md`): the reasons behind the choices and the assumptions about ambiguities; check it before changing a decision.

## Conventions

- Business rules live in `src/emporio/rules/` as pure functions with tests. Tools only translate I/O.
- `data/raw/` is never edited; everything derived comes from `python -m emporio.data.prepare`.
- Code, README, docs, generated reports and commit messages in English. The agent's prompt, tool outputs and customer conversations in Brazilian Portuguese.
- Small commits per phase (Conventional Commits), no attribution trailer; push when a phase is done.
- Before committing: `uv run ruff check . && uv run ruff format . && uv run pytest`.
