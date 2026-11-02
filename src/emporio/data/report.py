"""Markdown rendering of the data quality report (``docs/data_quality_report.md``)."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING

from emporio import config
from emporio.data.quality import SECTIONS, Finding, join_ints
from emporio.formatting import date_br

if TYPE_CHECKING:
    from emporio.data.prepare import PrepareResult

REPORT_PATH = config.DOCS_DIR / "data_quality_report.md"


def render_report(result: PrepareResult) -> str:
    tables = result.tables
    products = tables["products"]
    orders = tables["orders"]
    customers = tables["customers"]
    status_counts = _count(products, "status")
    order_status_counts = _count(orders, "status")
    cities = _count(customers, "city")
    used_categories = {p["category_id"] for p in products}
    empty_categories = sum(
        1 for c in tables["categories"] if c["category_id"] not in used_categories
    )

    lines = [
        "# Relatório de qualidade de dados",
        "",
        "> Gerado por `python -m emporio.data.prepare` (não editar à mão). Data de referência "
        f"para atrasos: **{date_br(result.as_of)}**.",
        "",
        "O bruto (`data/raw`) fica intocado. A camada `data/processed` só **padroniza** "
        "(tipos, enums, specs parseadas, campos normalizados para busca) e **marca** registros "
        "suspeitos; nenhum conteúdo é reescrito ou recalculado. Cada achado abaixo traz o "
        "tratamento adotado. O porquê das decisões está em "
        "[`design-decisions.md`](design-decisions.md) (§5 e §6).",
        "",
        "## Resumo",
        "",
        "| Tabela | Registros | Observação |",
        "|---|---|---|",
        f"| categorias | {len(tables['categories'])} | {empty_categories} sem produto |",
        f"| produtos | {len(products)} | {_fmt_counts(status_counts)} |",
        f"| promoções | {len(tables['promotions'])} | "
        f"{sum(1 for p in tables['promotions'] if p['is_active'])} ativas |",
        f"| clientes | {len(customers)} | {_fmt_counts(cities)} |",
        f"| pedidos | {len(orders)} | {_fmt_counts(order_status_counts)} |",
        f"| itens de pedido | {len(tables['order_items'])} | — |",
        f"| manual (PDF) | {len(result.policy_pages)} páginas | texto extraído com `pypdf` |",
        "",
        f"**{len(result.findings)} achados** · "
        + " · ".join(
            f"{severity}: {count}"
            for severity, count in sorted(_count_attr(result.findings, "severity").items())
        ),
        "",
        "Severidade: **conflito** = o dado contradiz outro dado ou o manual; **regra** = o "
        "histórico viola uma regra do manual; **atenção** = estado que exige tratamento "
        "especial no atendimento; **info** = contexto.",
        "",
        "## Achados",
    ]
    for section, title in SECTIONS.items():
        section_findings = [f for f in result.findings if f.section == section]
        if not section_findings:
            continue
        lines += ["", f"### {title}"]
        for finding in section_findings:
            lines += _render_finding(finding)

    lines += ["", "## Verificações sem divergência", ""]
    if result.passed_checks:
        lines += [f"- {SECTIONS[section]}: {label}" for section, label in result.passed_checks]
    else:
        lines.append("- (nenhuma)")

    lines += [
        "",
        "## Camada processada (`data/processed`)",
        "",
        "Um JSON por tabela (`ensure_ascii=False`, datas em ISO 8601). Campos acrescentados:",
        "",
        "- `products`: `name_normalized`, `brand`, `category_name`, `specs` (objeto), "
        "`specs_schema`, `description_conflict`, `description_conflict_reasons`, `search_text`.",
        "- `customers`: `first_name`, `phone_digits`, `email_normalized`.",
        "- `orders`: `payment_type`, `installments`, `items_total_brl`, `total_mismatch`, "
        "`pix_without_discount`, `installment_below_minimum`.",
        "- `order_items`: `unit_price_brl` e `line_total_brl` (preço **atual** do catálogo).",
        "- `promotions`: `is_active` como booleano.",
        "- `policy_pages.json`: texto de cada página do manual, com o espaçamento corrigido "
        "(o `pypdf` devolve uma palavra por linha).",
        "",
    ]
    return "\n".join(lines)


def _render_finding(finding: Finding) -> list[str]:
    lines = ["", f"#### {finding.title} · _{finding.severity}_", ""]
    if finding.ids:
        lines.append(f"- **Registros:** {join_ints(finding.ids)}")
    lines += [f"- **O que foi visto:** {finding.detail}", f"- **Tratamento:** {finding.treatment}"]
    if finding.table_rows:
        lines += [
            "",
            "| " + " | ".join(finding.table_header) + " |",
            "|" + "---|" * len(finding.table_header),
        ]
        lines += ["| " + " | ".join(_cell(c) for c in row) + " |" for row in finding.table_rows]
    return lines


def _cell(value: str) -> str:
    return value.replace("|", "\\|")


def _count(rows: Iterable[dict], key: str) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for row in rows:
        counts[str(row[key])] += 1
    return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))


def _count_attr(items: Iterable[object], attr: str) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for item in items:
        counts[getattr(item, attr)] += 1
    return dict(counts)


def _fmt_counts(counts: dict[str, int]) -> str:
    return ", ".join(f"{value} {key}" for key, value in counts.items())


def write_report(result: PrepareResult, path: Path = REPORT_PATH) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_report(result), encoding="utf-8")
    return path
