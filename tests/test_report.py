"""Tests for report.py: rich terminal output and the standalone HTML report."""

from __future__ import annotations

import io
from datetime import date

from rich.console import Console

from holiday_tracker.engine.search import SearchResults, run_search
from holiday_tracker.models import DateRule, Money, SearchSpec, StayQuote
from holiday_tracker.providers.fixtures import FixturesFlightProvider, FixturesStayProvider
from holiday_tracker.report import city_label, print_results, render_html_report, write_html_report


def test_city_label_formats_snake_case_ids():
    assert city_label("palma_de_mallorca") == "Palma De Mallorca"
    assert city_label("barcelona") == "Barcelona"


def _spec(budget: float) -> SearchSpec:
    return SearchSpec(
        origins=["LHR"],
        destination="barcelona",
        date_rule=DateRule(
            window_start=date(2027, 3, 1),
            window_end=date(2027, 5, 31),
            nights_min=3,
            nights_max=3,
        ),
        budget=Money.from_major(budget, "GBP"),
        party_size=2,
    )


def _console() -> Console:
    return Console(file=io.StringIO(), width=120)


class TestPrintResults:
    def test_feasible_case_mentions_destination_and_fits_message(self):
        spec = _spec(2000)
        results = run_search(spec, FixturesFlightProvider(), FixturesStayProvider())
        console = _console()
        print_results(console, spec, results)
        output = console.file.getvalue()
        assert "Found a holiday that fits" in output
        assert "Barcelona" in output

    def test_near_miss_case_shows_relaxation_suggestions(self):
        spec = _spec(1)
        results = run_search(spec, FixturesFlightProvider(), FixturesStayProvider())
        console = _console()
        print_results(console, spec, results)
        output = console.file.getvalue()
        assert "Nothing fits" in output
        assert "Cheapest near-misses" in output

    def test_empty_case_reports_no_results(self):
        spec = _spec(2000)
        spec.stay_filters.min_rating = 9.99  # eliminates every fixture stay
        results = run_search(spec, FixturesFlightProvider(), FixturesStayProvider())
        console = _console()
        print_results(console, spec, results)
        output = console.file.getvalue()
        assert "No results at all" in output


class TestRenderHtmlReport:
    def test_feasible_case_is_valid_looking_html_with_destination(self):
        spec = _spec(2000)
        results = run_search(spec, FixturesFlightProvider(), FixturesStayProvider())
        html = render_html_report(spec, results)
        assert "<!doctype html>" in html.lower()
        assert "Barcelona" in html
        assert "Found a holiday that fits" in html

    def test_near_miss_case_includes_relaxation_text(self):
        spec = _spec(1)
        results = run_search(spec, FixturesFlightProvider(), FixturesStayProvider())
        html = render_html_report(spec, results)
        assert "Nothing fits" in html

    def test_html_escapes_untrusted_looking_content(self):
        # destination_city_id and other rendered fields come from our own
        # controlled catalog, but escaping should still hold if that ever
        # changes -- exercise it via a hand-built SearchResults.
        spec = _spec(500)
        stay = StayQuote(
            city_id="barcelona",
            check_in=date(2027, 3, 4),
            check_out=date(2027, 3, 7),
            nightly_rate=Money.from_major(50, "GBP"),
            property_type="<script>alert(1)</script>",
            rating=8.0,
            distance_km=1.0,
            free_cancellation=True,
            observed_at="2026-01-01T00:00:00Z",
            source="fixtures",
        )
        from holiday_tracker.catalog.loader import load_spend
        from holiday_tracker.engine.pricing import build_package

        package = build_package(
            spec=spec,
            origin="LHR",
            destination_city_id="barcelona",
            depart_date=date(2027, 3, 4),
            return_date=date(2027, 3, 7),
            flight_price_per_person=Money.from_major(50, "GBP"),
            stay=stay,
            daily_spend=load_spend()["barcelona"],
        )
        results = SearchResults(spec=spec, packages=[package])
        html = render_html_report(spec, results)
        assert "<script>" not in html
        assert "&lt;script&gt;" in html


class TestWriteHtmlReport:
    def test_writes_file_to_disk(self, tmp_path):
        spec = _spec(2000)
        results = run_search(spec, FixturesFlightProvider(), FixturesStayProvider())
        out_path = tmp_path / "report.html"
        write_html_report(out_path, spec, results)
        assert out_path.exists()
        content = out_path.read_text(encoding="utf-8")
        assert "<!doctype html>" in content.lower()
