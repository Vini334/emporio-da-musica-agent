"""Raw → processed data layer + data quality report (SPEC §16, DD-§5).

Run: ``python -m emporio.data.prepare [--as-of AAAA-MM-DD]``

- ``data/raw`` is never modified.
- Technical standardization only: types, enums, parsed specs, normalized search fields.
- Content is never rewritten or recalculated: suspicious records get a flag and the
  finding goes to ``docs/data_quality_report.md`` with the treatment adopted.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import pandas as pd
from pypdf import PdfReader

from emporio import clock, config
from emporio.data.quality import (
    BRAND_ALIASES,
    Finding,
    description_conflicts,
    mentions,
    run_checks,
    specs_schema,
)
from emporio.data.report import REPORT_PATH, write_report
from emporio.formatting import date_br
from emporio.rules.pricing import PIX_DISCOUNT_PERCENT, apply_discount, installment_allowed
from emporio.text import digits, normalize

RAW_FILES = {
    "categories": "categories.csv",
    "products": "products.csv",
    "promotions": "promotions.csv",
    "customers": "customers.csv",
    "orders": "orders.csv",
    "order_items": "order_items.csv",
}
POLICIES_PDF = "politicas_da_loja.pdf"

PRODUCT_STATUSES = frozenset({"active", "discontinued", "coming_soon"})
ORDER_STATUSES = frozenset({"pending", "confirmed", "shipped", "delivered", "cancelled"})
PAYMENT_PATTERN = re.compile(r"^(?:pix|debit|boleto|credit_(\d{1,2})x)$")
MONEY_TOLERANCE = 0.01


class DataError(ValueError):
    """Raw data that cannot be parsed into the processed schema."""


@dataclass
class PrepareResult:
    as_of: date
    tables: dict[str, list[dict]]
    policy_pages: list[str]
    findings: list[Finding] = field(default_factory=list)
    passed_checks: list[tuple[str, str]] = field(default_factory=list)  # (section, label)

    def finding(self, code: str) -> Finding | None:
        return next((f for f in self.findings if f.code == code), None)


# --------------------------------------------------------------------------- parsing


def _read_csv(path: Path) -> list[dict[str, str]]:
    frame = pd.read_csv(path, dtype=str, keep_default_na=False)
    return [
        {str(key).strip(): str(value).strip() for key, value in row.items()}
        for row in frame.to_dict("records")
    ]


def _int(row: dict[str, str], key: str, ctx: str) -> int:
    try:
        return int(row[key])
    except (KeyError, ValueError) as exc:
        raise DataError(f"{ctx}: {key}={row.get(key)!r} is not an integer") from exc


def _float(row: dict[str, str], key: str, ctx: str) -> float:
    try:
        return float(row[key])
    except (KeyError, ValueError) as exc:
        raise DataError(f"{ctx}: {key}={row.get(key)!r} is not a number") from exc


def _date(row: dict[str, str], key: str, ctx: str) -> date | None:
    value = row.get(key, "")
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise DataError(f"{ctx}: {key}={value!r} is not an ISO date") from exc


def _text(row: dict[str, str], key: str) -> str | None:
    return row.get(key) or None


# --------------------------------------------------------------------------- tables


def build_categories(rows: list[dict[str, str]]) -> list[dict]:
    categories = []
    for row in rows:
        ctx = f"categories[{row.get('category_id')}]"
        categories.append(
            {
                "category_id": _int(row, "category_id", ctx),
                "name": row["name"],
                "name_normalized": normalize(row["name"]),
                "description": row["description"],
            }
        )
    return categories


def build_products(rows: list[dict[str, str]], categories: list[dict]) -> list[dict]:
    category_names = {c["category_id"]: c["name"] for c in categories}
    products = []
    for row in rows:
        ctx = f"products[{row.get('product_id')}]"
        category_id = _int(row, "category_id", ctx)
        if category_id not in category_names:
            raise DataError(f"{ctx}: unknown category_id {category_id}")
        status = row["status"]
        if status not in PRODUCT_STATUSES:
            raise DataError(f"{ctx}: unknown status {status!r}")
        try:
            raw_specs = json.loads(row["specs"])
        except json.JSONDecodeError as exc:
            raise DataError(f"{ctx}: specs is not valid JSON") from exc
        if not isinstance(raw_specs, dict):
            raise DataError(f"{ctx}: specs must be a JSON object")
        specs = {str(k): str(v).strip() for k, v in raw_specs.items()}
        name = row["name"]
        brands = mentions(name, BRAND_ALIASES)
        conflicts = description_conflicts(name, row["description"], specs)
        category_name = category_names[category_id]
        products.append(
            {
                "product_id": _int(row, "product_id", ctx),
                "name": name,
                "name_normalized": normalize(name),
                "brand": brands[0] if brands else None,
                "category_id": category_id,
                "category_name": category_name,
                "price_brl": _float(row, "price_brl", ctx),
                "stock_quantity": _int(row, "stock_quantity", ctx),
                "status": status,
                "specs": specs,
                "specs_schema": specs_schema(specs),
                "description": row["description"],
                "description_conflict": bool(conflicts),
                "description_conflict_reasons": conflicts,
                "search_text": normalize(" ".join([name, category_name, *specs.values()])),
                "created_at": _date(row, "created_at", ctx),
                "_specs_raw": row["specs"],  # dropped before writing; used by checks
            }
        )
    return products


def build_promotions(rows: list[dict[str, str]]) -> list[dict]:
    promotions = []
    for row in rows:
        ctx = f"promotions[{row.get('promotion_id')}]"
        is_active = row["is_active"]
        if is_active not in {"0", "1"}:
            raise DataError(f"{ctx}: is_active must be 0 or 1, got {is_active!r}")
        discount = _float(row, "discount_percent", ctx)
        if not 0 < discount < 100:
            raise DataError(f"{ctx}: discount_percent out of range: {discount}")
        promotions.append(
            {
                "promotion_id": _int(row, "promotion_id", ctx),
                "product_id": _int(row, "product_id", ctx),
                "discount_percent": discount,
                "description": row["description"],
                "is_active": is_active == "1",
            }
        )
    return promotions


def build_customers(rows: list[dict[str, str]]) -> list[dict]:
    customers = []
    for row in rows:
        ctx = f"customers[{row.get('customer_id')}]"
        name = row["name"]
        customers.append(
            {
                "customer_id": _int(row, "customer_id", ctx),
                "name": name,
                "first_name": name.split()[0] if name else "",
                "phone": row["phone"],
                "phone_digits": digits(row["phone"]),
                "email": row["email"],
                "email_normalized": row["email"].strip().lower(),
                "city": row["city"],
            }
        )
    return customers


def build_order_items(rows: list[dict[str, str]], products: list[dict]) -> list[dict]:
    prices = {p["product_id"]: p["price_brl"] for p in products}
    items = []
    for row in rows:
        ctx = f"order_items[order={row.get('order_id')}, product={row.get('product_id')}]"
        product_id = _int(row, "product_id", ctx)
        quantity = _int(row, "quantity", ctx)
        if product_id not in prices:
            raise DataError(f"{ctx}: unknown product_id {product_id}")
        if quantity <= 0:
            raise DataError(f"{ctx}: quantity must be positive")
        items.append(
            {
                "order_id": _int(row, "order_id", ctx),
                "product_id": product_id,
                "quantity": quantity,
                # Current catalog price: the raw data has no historical unit price.
                "unit_price_brl": prices[product_id],
                "line_total_brl": round(prices[product_id] * quantity, 2),
            }
        )
    return items


def build_orders(
    rows: list[dict[str, str]],
    customers: list[dict],
    items: list[dict],
    promotions: list[dict],
) -> list[dict]:
    customer_ids = {c["customer_id"] for c in customers}
    active_promo_products = {p["product_id"] for p in promotions if p["is_active"]}
    items_by_order: dict[int, list[dict]] = defaultdict(list)
    for item in items:
        items_by_order[item["order_id"]].append(item)

    orders = []
    for row in rows:
        ctx = f"orders[{row.get('order_id')}]"
        order_id = _int(row, "order_id", ctx)
        customer_id = _int(row, "customer_id", ctx)
        if customer_id not in customer_ids:
            raise DataError(f"{ctx}: unknown customer_id {customer_id}")
        status = row["status"]
        if status not in ORDER_STATUSES:
            raise DataError(f"{ctx}: unknown status {status!r}")
        payment = row["payment_method"]
        match = PAYMENT_PATTERN.match(payment)
        if not match:
            raise DataError(f"{ctx}: unknown payment_method {payment!r}")
        installments = int(match.group(1)) if match.group(1) else None
        if installments is not None and not 1 <= installments <= 12:
            raise DataError(f"{ctx}: installments out of range: {installments}")
        order_date = _date(row, "order_date", ctx)
        if order_date is None:
            raise DataError(f"{ctx}: order_date is required")

        total = _float(row, "total_brl", ctx)
        order_items = items_by_order.get(order_id, [])
        items_total = round(sum(i["line_total_brl"] for i in order_items), 2)
        is_pix = payment == "pix"
        pix_expected = is_pix and not any(
            i["product_id"] in active_promo_products for i in order_items
        )
        matches_items = abs(total - items_total) < MONEY_TOLERANCE
        matches_pix = pix_expected and (
            abs(total - apply_discount(items_total, PIX_DISCOUNT_PERCENT)) < MONEY_TOLERANCE
        )
        orders.append(
            {
                "order_id": order_id,
                "customer_id": customer_id,
                "order_date": order_date,
                "status": status,
                "total_brl": total,
                "payment_method": payment,
                "payment_type": "credit" if installments else payment,
                "installments": installments,
                "tracking_code": _text(row, "tracking_code"),
                "estimated_delivery": _date(row, "estimated_delivery", ctx),
                "notes": _text(row, "notes"),
                "items_total_brl": items_total,
                "total_mismatch": bool(order_items) and not (matches_items or matches_pix),
                "pix_without_discount": pix_expected and matches_items,
                "installment_below_minimum": installments is not None
                and not installment_allowed(total, installments),
            }
        )
    return orders


# --------------------------------------------------------------------------- policies PDF


def extract_policy_pages(pdf_path: Path) -> list[str]:
    """Page texts with pypdf's word-per-line output collapsed into normal spacing."""
    pages = []
    for page in PdfReader(pdf_path).pages:
        text = page.extract_text() or ""
        text = re.sub(r"\s*\n\s*", " ", text)
        pages.append(re.sub(r" {2,}", " ", text).strip())
    return pages


# --------------------------------------------------------------------------- pipeline


def prepare(raw_dir: Path = config.RAW_DIR, as_of: date | None = None) -> PrepareResult:
    """Load the raw files, build the processed tables and run every quality check."""
    raw = {name: _read_csv(raw_dir / filename) for name, filename in RAW_FILES.items()}
    categories = build_categories(raw["categories"])
    products = build_products(raw["products"], categories)
    promotions = build_promotions(raw["promotions"])
    customers = build_customers(raw["customers"])
    order_items = build_order_items(raw["order_items"], products)
    orders = build_orders(raw["orders"], customers, order_items, promotions)
    tables = {
        "categories": categories,
        "products": products,
        "promotions": promotions,
        "customers": customers,
        "orders": orders,
        "order_items": order_items,
    }
    _check_unique_ids(tables)

    policy_pages = extract_policy_pages(raw_dir / POLICIES_PDF)
    result = PrepareResult(as_of=as_of or clock.today(), tables=tables, policy_pages=policy_pages)
    result.findings, result.passed_checks = run_checks(tables, " ".join(policy_pages), result.as_of)
    return result


def _check_unique_ids(tables: dict[str, list[dict]]) -> None:
    keys = {
        "categories": "category_id",
        "products": "product_id",
        "promotions": "promotion_id",
        "customers": "customer_id",
        "orders": "order_id",
    }
    for table, key in keys.items():
        ids = [row[key] for row in tables[table]]
        if len(ids) != len(set(ids)):
            raise DataError(f"{table}: duplicated {key}")


def write_processed(result: PrepareResult, out_dir: Path = config.PROCESSED_DIR) -> list[Path]:
    """Write one JSON file per table plus the extracted policy pages."""
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for name, rows in result.tables.items():
        clean = [{k: v for k, v in row.items() if not k.startswith("_")} for row in rows]
        written.append(_write_json(out_dir / f"{name}.json", clean))
    pages = [{"page": n, "text": text} for n, text in enumerate(result.policy_pages, start=1)]
    written.append(_write_json(out_dir / "policy_pages.json", pages))
    return written


def _write_json(path: Path, payload: object) -> Path:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=_json_default) + "\n",
        encoding="utf-8",
    )
    return path


def _json_default(value: object) -> str:
    if isinstance(value, date):
        return value.isoformat()
    raise TypeError(f"not JSON serializable: {type(value).__name__}")


# --------------------------------------------------------------------------- CLI

# The committed report is a deliverable: pin its reference date (the delivery analysis) so
# running this command on another day reproduces it byte for byte. Use --as-of to recompute.
REPORT_AS_OF = date(2026, 11, 2)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m emporio.data.prepare",
        description="Gera data/processed e docs/data_quality_report.md a partir de data/raw.",
    )
    parser.add_argument("--raw-dir", type=Path, default=config.RAW_DIR)
    parser.add_argument("--out-dir", type=Path, default=config.PROCESSED_DIR)
    parser.add_argument("--report", type=Path, default=REPORT_PATH)
    parser.add_argument(
        "--as-of",
        type=date.fromisoformat,
        default=REPORT_AS_OF,
        help=(
            "data de referência para atrasos e pedidos parados no relatório (AAAA-MM-DD); "
            f"padrão: {REPORT_AS_OF.isoformat()}, a data da análise entregue"
        ),
    )
    args = parser.parse_args(argv)

    result = prepare(args.raw_dir, as_of=args.as_of)
    written = write_processed(result, args.out_dir)
    report = write_report(result, args.report)
    print(f"{len(written)} arquivos em {args.out_dir}")
    print(f"{len(result.findings)} achados em {report} (referência {date_br(result.as_of)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
