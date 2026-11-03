"""Data preparation: every expected finding of SPEC §16 must be detected and reported."""

from __future__ import annotations

import hashlib
import json
from datetime import date

import pytest

from emporio import config
from emporio.data.prepare import DataError, build_products, main, prepare, write_processed
from emporio.data.quality import description_conflicts
from emporio.data.report import render_report

AS_OF = date(2026, 11, 2)


@pytest.fixture(scope="module")
def result():
    return prepare(config.RAW_DIR, as_of=AS_OF)


def finding_ids(result, code: str) -> set[int]:
    finding = result.finding(code)
    assert finding is not None, f"finding {code!r} not detected"
    return set(finding.ids)


def by_id(result, table: str, key: str) -> dict[int, dict]:
    return {row[key]: row for row in result.tables[table]}


class TestProducts:
    def test_description_conflicts(self, result):
        expected = {135, 136, 137, 139, 140, 142, 144}
        assert finding_ids(result, "product.description_conflict") == expected
        flagged = {p["product_id"] for p in result.tables["products"] if p["description_conflict"]}
        assert flagged == expected

    def test_conflict_reasons_cover_counts(self, result):
        products = by_id(result, "products", "product_id")
        assert any("5 cordas" in r for r in products[137]["description_conflict_reasons"])
        assert any("88 teclas" in r for r in products[142]["description_conflict_reasons"])

    def test_consistent_descriptions_are_not_flagged(self, result):
        products = by_id(result, "products", "product_id")
        # Same brand in name and description (Music Man StingRay, Yamaha Stage Custom, Nord Stage).
        for product_id in (138, 141, 143, 145, 131):
            assert not products[product_id]["description_conflict"], product_id

    def test_pickup_brands_are_not_instrument_brands(self):
        assert (
            description_conflicts(
                "Martin D-28E Elétrico Aço Natural",
                "O lendário Martin D-28 com captação LR Baggs Anthem.",
                {"strings": "steel"},
            )
            == []
        )

    def test_status_findings(self, result):
        assert finding_ids(result, "product.discontinued") == {113, 136}
        assert finding_ids(result, "product.active_out_of_stock") == {96}
        coming_soon = result.finding("product.coming_soon")
        assert set(coming_soon.ids) == {130}
        assert set(coming_soon.related) == {3, 14}

    def test_specs_schemas(self, result):
        finding = result.finding("product.specs_schemas")
        assert finding.ids == ()  # no product outside the known schemas
        assert {row[0] for row in finding.table_rows} == {
            "acustico",
            "eletrico",
            "bateria",
            "teclado",
        }

    def test_name_matches_specs(self, result):
        assert result.finding("product.name_specs_mismatch") is None

    def test_brand_detection(self, result):
        products = by_id(result, "products", "product_id")
        assert products[139]["brand"] == "Yamaha"
        assert products[145]["brand"] == "Fender"
        assert products[135]["brand"] == "Music Man"

    def test_normalized_fields(self, result):
        product = by_id(result, "products", "product_id")[114]
        assert product["name_normalized"] == "yamaha cg162s nylon natural"
        assert product["specs"]["strings"] == "nylon"
        assert product["category_name"] == "Violões"


def test_empty_categories(result):
    finding = result.finding("category.empty")
    assert set(finding.ids) == {6, 7, 8}
    assert "cordas orquestrais" in finding.detail


class TestOrders:
    def test_total_mismatch(self, result):
        assert finding_ids(result, "order.total_mismatch") == {3, 20}
        orders = by_id(result, "orders", "order_id")
        assert orders[3]["total_brl"] == 3450 and orders[3]["items_total_brl"] == 3498
        assert orders[20]["total_brl"] == 1400 and orders[20]["items_total_brl"] == 1488

    def test_pix_without_discount(self, result):
        # SPEC §16 lists 1, 5, 10 and 13; 17 (cancelled) has the same problem.
        assert finding_ids(result, "order.pix_without_discount") == {1, 5, 10, 13, 17}

    def test_installment_below_minimum(self, result):
        assert finding_ids(result, "order.installment_below_minimum") == {9, 16, 18}

    def test_late_and_stale(self, result):
        assert finding_ids(result, "order.late") == {8}
        assert finding_ids(result, "order.stale") == set(range(9, 17))

    def test_late_depends_on_reference_date(self):
        earlier = prepare(config.RAW_DIR, as_of=date(2026, 2, 28))
        assert earlier.finding("order.late") is None  # order 8 is due on 28/02/2026 itself
        assert finding_ids(earlier, "order.stale") == {9}  # 8 days since 20/02; others newer

    def test_structural_integrity(self, result):
        assert result.finding("order.integrity") is None

    def test_payment_parsing(self, result):
        orders = by_id(result, "orders", "order_id")
        assert (orders[3]["payment_type"], orders[3]["installments"]) == ("credit", 12)
        assert (orders[1]["payment_type"], orders[1]["installments"]) == ("pix", None)


def test_duplicate_email(result):
    assert finding_ids(result, "customer.duplicate_email") == {11, 43}
    assert result.finding("customer.duplicate_phone") is None
    assert result.finding("customer.invalid_contact") is None


class TestPromotions:
    def test_active(self, result):
        finding = result.finding("promotion.active")
        assert set(finding.ids) == {12, 14, 19, 24}
        assert set(finding.related) == {127, 121, 90, 94}

    def test_multiple_per_product(self, result):
        finding = result.finding("promotion.multiple_per_product")
        assert set(finding.ids) == {93, 96, 99}
        assert "Todas inativas" in finding.detail


def test_manual_findings(result):
    whatsapp = result.finding("manual.multiple_whatsapp")
    assert "(67) 3321-4500" in whatsapp.detail and "(67) 3341-4444" in whatsapp.detail
    assert "empóriodamusica" in result.finding("manual.email_with_accent").detail
    installments = result.finding("manual.installment_conflict").detail
    assert all(value in installments for value in ("R$ 50,00", "R$ 80,00", "R$ 100,00"))
    assert "300" in result.finding("manual.catalog_size").detail
    assert result.finding("manual.shipping_threshold_gap") is not None
    assert result.finding("manual.black_friday") is not None


def test_policy_pages_are_respaced(result):
    assert len(result.policy_pages) == 8
    assert "Direito de Arrependimento" in result.policy_pages[3]


def test_report_lists_every_finding(result):
    report = render_report(result)
    for finding in result.findings:
        assert finding.title in report
    for snippet in (
        "135, 136, 137, 139, 140, 142, 144",
        "bruno.martins@jmail.com",
        "(67) 3321-4500",
        "mais de 300 instrumentos",
        "02/11/2026",
    ):
        assert snippet in report


def test_write_processed(result, tmp_path):
    write_processed(result, tmp_path)
    expected = {
        "categories",
        "products",
        "promotions",
        "customers",
        "orders",
        "order_items",
        "policy_pages",
    }
    assert {p.stem for p in tmp_path.glob("*.json")} == expected
    products = json.loads((tmp_path / "products.json").read_text(encoding="utf-8"))
    assert len(products) == 65
    assert all(not key.startswith("_") for key in products[0])
    assert isinstance(products[0]["specs"], dict)
    orders = json.loads((tmp_path / "orders.json").read_text(encoding="utf-8"))
    assert orders[0]["order_date"] == "2025-10-15"


def test_main_does_not_touch_raw(tmp_path):
    def digest() -> dict[str, str]:
        return {
            p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in config.RAW_DIR.iterdir()
        }

    before = digest()
    report = tmp_path / "report.md"
    exit_code = main(
        ["--out-dir", str(tmp_path / "processed"), "--report", str(report), "--as-of", "2026-11-02"]
    )
    assert exit_code == 0
    assert report.exists()
    assert digest() == before


def test_invalid_status_fails_loudly():
    categories = [{"category_id": 1, "name": "Guitarras"}]
    row = {
        "product_id": "1",
        "price_brl": "10",
        "name": "X",
        "category_id": "1",
        "description": "",
        "stock_quantity": "1",
        "status": "sold_out",
        "specs": "{}",
        "created_at": "2024-01-01",
    }
    with pytest.raises(DataError, match="status"):
        build_products([row], categories)


def test_cli_report_is_reproducible_on_any_day(tmp_path, monkeypatch):
    monkeypatch.setenv("EMPORIO_NOW", "2027-05-20T10:00:00")
    report = tmp_path / "report.md"
    main(["--out-dir", str(tmp_path / "processed"), "--report", str(report)])
    assert "02/11/2026" in report.read_text(encoding="utf-8")
    assert "20/05/2027" not in report.read_text(encoding="utf-8")
