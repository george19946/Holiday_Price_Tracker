"""Rendering search results: rich terminal tables for interactive use, and
an optional self-contained HTML report for opening in a browser or sharing
without a terminal.

Every rendering here repeats the same disclosure the project plan requires:
prices are cached/indicative, not live-bookable, so each stay is shown with
when it was observed and a reminder to verify before booking.
"""

from __future__ import annotations

import html as html_module
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from holiday_tracker.engine.rank import Relaxation, describe_relaxation, suggest_relaxation
from holiday_tracker.engine.search import SearchResults
from holiday_tracker.models import Package, SearchSpec

_VERIFY_NOTE = "indicative price, verify before booking"


def city_label(city_id: str) -> str:
    return city_id.replace("_", " ").title()


def _stay_line(package: Package) -> str | None:
    stay = package.stay
    if stay is None:
        return None

    rating = f"rating {stay.rating}" if stay.rating is not None else "rating unknown"
    distance = (
        f"{stay.distance_km} km from centre" if stay.distance_km is not None else "distance unknown"
    )

    if stay.confidence == "city_median_estimate":
        provenance = (
            f"estimated from bundled catalog data as of {stay.observed_at:%Y-%m-%d} "
            "-- no live hotel price source is currently available, this is not an observed listing"
        )
    else:
        provenance = f"observed {stay.observed_at:%Y-%m-%d}, source: {stay.source} -- {_VERIFY_NOTE}"

    return f"{stay.property_type}, {rating}, {distance} ({provenance})"


def _package_table(packages: list[Package], title: str) -> Table:
    table = Table(title=title)
    table.add_column("Destination")
    table.add_column("Depart")
    table.add_column("Return")
    table.add_column("Nights", justify="right")
    table.add_column("Flights", justify="right")
    table.add_column("Stay", justify="right")
    table.add_column("Spend", justify="right")
    table.add_column("Total", justify="right", style="bold")
    for package in packages:
        table.add_row(
            city_label(package.destination_city_id),
            package.depart_date.isoformat(),
            package.return_date.isoformat(),
            str(package.nights),
            str(package.flights_cost),
            str(package.accommodation_cost),
            str(package.spend_cost),
            str(package.total_cost),
        )
    return table


def print_results(
    console: Console, spec: SearchSpec, results: SearchResults, near_miss_count: int = 5
) -> None:
    """Render a completed search run to `console`: the best fitting package,
    or the cheapest near-misses each annotated with the single cheapest
    change that would close the gap."""
    if results.feasible:
        best = results.feasible[0]
        console.print(Panel.fit(f"[bold green]Found a holiday that fits {spec.budget}[/]"))
        console.print(_package_table([best], "Best match"))
        stay_line = _stay_line(best)
        if stay_line:
            console.print(f"Stay: {stay_line}")
        if len(results.feasible) > 1:
            console.print(_package_table(results.feasible[1:5], "Other options within budget"))
        return

    if results.near_misses:
        console.print(
            Panel.fit(f"[bold yellow]Nothing fits {spec.budget} exactly -- cheapest alternative(s)[/]")
        )
        near = results.near_misses[:near_miss_count]
        console.print(_package_table(near, "Cheapest near-misses"))
        for package in near:
            relaxation = suggest_relaxation(package, results.packages, spec, results.raw_stays)
            if relaxation is not None:
                console.print(
                    f"  [{city_label(package.destination_city_id)}] "
                    f"{describe_relaxation(package, relaxation)}"
                )
        return

    console.print(
        "[red]No results at all.[/] Try loosening the stay filters, widening the date "
        "window, or a different destination."
    )


def _relaxation_for(
    package: Package, results: SearchResults, spec: SearchSpec
) -> Relaxation | None:
    return suggest_relaxation(package, results.packages, spec, results.raw_stays)


def render_html_report(
    spec: SearchSpec, results: SearchResults, near_miss_count: int = 5
) -> str:
    """A small, self-contained HTML report (no external assets, works
    offline) summarising one search run -- for opening in a browser or
    attaching somewhere a terminal isn't available."""
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M")
    rows: list[str] = []

    def row(package: Package, relaxation: Relaxation | None) -> str:
        stay_line = _stay_line(package) or "no stay data"
        relaxation_html = (
            f'<p class="relax">{html_module.escape(describe_relaxation(package, relaxation))}</p>'
            if relaxation is not None
            else ""
        )
        return f"""
        <tr>
          <td>{html_module.escape(city_label(package.destination_city_id))}</td>
          <td>{package.depart_date.isoformat()} &rarr; {package.return_date.isoformat()}
              ({package.nights} nights)</td>
          <td>{package.flights_cost}</td>
          <td>{package.accommodation_cost}</td>
          <td>{package.spend_cost}</td>
          <td class="total">{package.total_cost}</td>
        </tr>
        <tr class="detail">
          <td colspan="6">{html_module.escape(stay_line)}{relaxation_html}</td>
        </tr>
        """

    if results.feasible:
        heading = f"Found a holiday that fits {html_module.escape(str(spec.budget))}"
        status_class = "fits"
        for package in results.feasible[:5]:
            rows.append(row(package, None))
    elif results.near_misses:
        heading = f"Nothing fits {html_module.escape(str(spec.budget))} exactly -- cheapest alternative(s)"
        status_class = "near-miss"
        for package in results.near_misses[:near_miss_count]:
            rows.append(row(package, _relaxation_for(package, results, spec)))
    else:
        heading = "No results at all"
        status_class = "empty"

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Holiday Price Tracker report</title>
<style>
  body {{ font-family: -apple-system, Segoe UI, sans-serif; margin: 2rem; color: #1a1a1a; }}
  h1 {{ font-size: 1.3rem; }}
  h1.fits {{ color: #146c2e; }}
  h1.near-miss {{ color: #8a6d00; }}
  h1.empty {{ color: #a11; }}
  table {{ border-collapse: collapse; width: 100%; margin-top: 1rem; }}
  th, td {{ text-align: left; padding: 0.5rem 0.75rem; border-bottom: 1px solid #ddd; }}
  th {{ background: #f5f5f5; }}
  td.total {{ font-weight: bold; text-align: right; }}
  tr.detail td {{ color: #555; font-size: 0.9rem; border-bottom: 2px solid #eee; }}
  p.relax {{ margin: 0.25rem 0 0; }}
  footer {{ margin-top: 2rem; font-size: 0.8rem; color: #777; }}
</style>
</head>
<body>
  <h1 class="{status_class}">{heading}</h1>
  <table>
    <thead>
      <tr><th>Destination</th><th>Dates</th><th>Flights</th><th>Stay</th><th>Spend</th><th>Total</th></tr>
    </thead>
    <tbody>
      {"".join(rows) if rows else '<tr><td colspan="6">No candidates matched the given constraints.</td></tr>'}
    </tbody>
  </table>
  <footer>
    Generated {generated_at}. Flight and stay prices are cached/indicative
    (Travelpayouts data), not live-bookable -- verify before booking.
    Daily-spend figures are hand-maintained estimates, not a live feed.
  </footer>
</body>
</html>
"""


def write_html_report(
    path: str | Path, spec: SearchSpec, results: SearchResults, near_miss_count: int = 5
) -> None:
    Path(path).write_text(render_html_report(spec, results, near_miss_count), encoding="utf-8")
