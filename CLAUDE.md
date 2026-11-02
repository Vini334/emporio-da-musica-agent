# Empório da Música — agente de atendimento

Desafio técnico AI Engineer (Artefact). Prazo: quarta, 04/11/2026; nenhum commit depois da entrega.

- **Spec** (`docs/SPEC.md`): leia antes de implementar ou alterar qualquer coisa. É a fonte de verdade das regras de negócio (BR-nn), dos contratos das tools, do layout de módulos e do status das fases. Marque a fase como ✅ na §15 ao concluí-la.
- **Decisões** (`docs/design-decisions.md`): o porquê das escolhas e as suposições sobre ambiguidades; consulte antes de mudar uma decisão.

## Convenções

- Regras de negócio ficam em `src/emporio/rules/` como funções puras com teste. Tools só traduzem I/O.
- `data/raw/` é intocável; tudo que for derivado sai de `python -m emporio.data.prepare`.
- Código em inglês; prompts, README, exemplos e docs em português.
- Commits pequenos por fase (Conventional Commits), push a cada fase concluída.
- Antes de commitar: `uv run ruff check . && uv run ruff format . && uv run pytest`.
