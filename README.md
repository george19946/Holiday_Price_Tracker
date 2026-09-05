# Holiday Price Tracker

A **budget-first** holiday planner. Instead of picking a destination and watching the
budget slip, you state your constraints — where you're willing to fly from, where you
want to go (a city or a region), your date rules ("any Thursday–Sunday in 2027"), your
budget, and what you will and won't accept in accommodation — and the tool searches for
holidays that fit, or tells you exactly what to relax to make one fit, or tracks the
search over time and emails you the moment prices drop into budget.

## How it works

The all-in cost of a trip is:

```
total = flights (party) + accommodation (party, room-split) + estimated daily spend (party)
```

Flight prices come from the free [Travelpayouts](https://www.travelpayouts.com/) /
Aviasales data API — verified live (2026-09-05): it hands back whichever real,
already-priced round-trip fares Aviasales currently has cached for a route (a sparse,
rolling set, not a per-day grid for a month you ask for) plus a genuinely month-scoped
"cheapest fare this month" fallback. These are **cached, indicative prices**, not live
bookable fares, so every result includes when the price was observed, its source, and —
when the API provided one — a real booking link, with a reminder to verify before
booking. A tightly constrained date rule (an exact weekday pair and an exact nights
count) can legitimately match nothing, because the free data is genuinely that sparse —
that's correct behaviour, not a bug.

Accommodation is a different story: **Hotellook, the free hotel-price API this project
was built against, shut down permanently in October 2025**, and no free replacement
currently exists (see "Data sources and honest limitations" below). With a real token,
the tool still returns a complete, honestly-labelled total — real flights plus an
*estimated* nightly rate from a bundled per-city catalog — but the stay line is never a
real observed listing right now. Daily spend is estimated the same way, from a bundled,
editable cost-of-living dataset (`holiday_tracker/catalog/spend.yaml`), scaled by a
`thrifty` / `normal` / `comfortable` style multiplier.

The search runs in three stages to stay within free-tier rate limits: a wide, cheap
sweep of whatever fares a route's flight calendar currently has cached, a shortlist by
flight price alone, and stay pricing (live where available, estimated otherwise) for
only that shortlist. See `holiday_tracker/engine/search.py` for the full pipeline.

## Setup

1. Python 3.11+.
2. `pip install -e ".[dev]"`
3. Free sign-up at [travelpayouts.com](https://www.travelpayouts.com/) to get an API
   token, then `export TRAVELPAYOUTS_TOKEN=...`. Without it, every command still works
   against the offline `fixtures` provider (deterministic synthetic prices) — pass
   `--provider travelpayouts` once you have a token for real (but indicative) prices.

There's no separate `holiday-track init` setup step — the destination/spend catalogue
is bundled with the package (`holiday_tracker/catalog/*.yaml`), not fetched.

## Usage

```bash
# Interactive — asks for origin, destination, dates, budget, party size, spend
# style, and accommodation filters as a quick list.
holiday-track search

# Non-interactive, e.g. for scripts or CI. Defaults to --provider fixtures
# (offline, no token needed); pass --provider travelpayouts for real prices.
holiday-track search \
  --from LON,MAN --to "western europe" \
  --window 2027-01-01:2027-12-31 --depart-dow thu --return-dow sun \
  --nights 3-4 --blackout 2027-12-20:2027-01-03 \
  --budget 500 --party 2 --style normal \
  --no-hostels --min-rating 7.5 --max-centre-km 3 --free-cancellation \
  --provider travelpayouts --html-report report.html

# Persist a search as a watch, and re-price it later (this is what the
# scheduled GitHub Action runs daily):
holiday-track watch add --from-last
holiday-track watch run
holiday-track watch list

# Price history and trend for a watch:
holiday-track report <watch-id>
```

Run `holiday-track --help`, or `holiday-track <command> --help`, for the full flag
reference — every flag above (and a few more: `--month`, `--shortlist-size`,
`--near-miss-count`, `--occupancy`, `--currency`) is documented there.

### Output

If a package fits your budget and filters, it's shown as the top result. If nothing
fits, you get the cheapest near-misses, each annotated with the single cheapest change
that would close the gap — e.g. *"£540.00 — £40.00 over budget. Fly Fri instead of Thu:
saves £60.00. Closes the gap."*

### Tracking / alerts

`holiday-track watch add` persists a search's constraints to a local SQLite database
(default `~/.holiday-tracker/db.sqlite`; `--from-last` reuses your most recent `search`,
or pass the same flags `search` accepts, or neither for the interactive wizard).
`holiday-track watch run [watch-id]` re-prices one watch or every active one, records
the run, and appends a summary line to `data/history/<watch-id>.jsonl`, which is meant
to be committed so the repo accumulates a versioned price history over time.
`holiday-track report <watch-id>` shows a watch's run history and trend.

Email alerts fire the moment a watch's best package first comes in at or under budget
(deduplicated per exact package — same destination, dates, and rounded total — with a
72-hour cooldown, so a fare hovering at the boundary doesn't email you repeatedly; a
genuinely different or cheaper package still alerts immediately). To enable them, set:

| Variable | Purpose |
|---|---|
| `SMTP_HOST` | SMTP server hostname |
| `SMTP_PORT` | SMTP port (default `587`, STARTTLS) |
| `SMTP_USER` | SMTP username |
| `SMTP_PASS` | SMTP password |
| `ALERT_FROM` | From address (defaults to `SMTP_USER`) |
| `ALERT_TO` | Where to send alert emails |

Without these set, `watch run` still works — it just reports that a package fits budget
without emailing, rather than treating alerting as required. Pass `--no-alerts` to skip
the check entirely for a given run.

### Local state and configuration

| Env var | Overrides | Default |
|---|---|---|
| `TRAVELPAYOUTS_TOKEN` | The API token for `--provider travelpayouts` | *(required for that provider)* |
| `HOLIDAY_TRACKER_HOME` | Where the HTTP cache and last-search cache live | `~/.holiday-tracker/` |
| `HOLIDAY_TRACKER_DB` | The watch/run-history SQLite file | `~/.holiday-tracker/db.sqlite` |
| `HOLIDAY_TRACKER_HISTORY_DIR` | Where `watch run` appends `<watch-id>.jsonl` files | `data/history` (relative to CWD) |

Every `watch`/`report` command also accepts a `--db-path` flag for a one-off override
(mainly useful for scripting or tests).

## Running it on a schedule (GitHub Actions)

`.github/workflows/track.yml` re-prices your watches daily and commits the updated
`data/history/` files back to the repo. Because a GitHub-hosted runner is stateless
between runs and the watch database is deliberately *not* committed (it's local,
per-machine state — see above), the workflow uses `actions/cache` to persist
`~/.holiday-tracker/` across scheduled runs instead.

**Setup:**

1. Push this repo to GitHub (already done if you're reading this from there).
2. Add repository secrets under **Settings → Secrets and variables → Actions**:
   - `TRAVELPAYOUTS_TOKEN` — required for real prices; without it the workflow runs
     against the offline fixtures provider and logs a warning.
   - `SMTP_HOST`, `SMTP_USER`, `SMTP_PASS`, `ALERT_TO` (and optionally `SMTP_PORT`,
     `ALERT_FROM`) — for email alerts. Optional; the workflow runs fine without them.
3. Create your first watch by running the workflow manually: **Actions → Track
   watches → Run workflow**, filling in `add_from`, `add_to`, `add_window`,
   `add_nights`, `add_budget` (and optionally `add_depart_dow`, `add_return_dow`,
   `add_party`, `add_name`). This calls `holiday-track watch add` with those flags
   before re-pricing, and the resulting database persists via the cache for the next
   scheduled run. Leave `add_to` blank on later manual runs to just re-price existing
   watches on demand.
4. From then on, the workflow's daily `schedule` trigger re-prices every active watch
   automatically, commits `data/history/`, and emails you if a watch newly fits budget.

To track more than one holiday, run the workflow manually again with different
`add_*` inputs — each call adds another watch alongside the existing ones.

## Data sources and honest limitations

- **Flight prices are indicative, not bookable**, and verified live (2026-09-05,
  route LHR→BCN, see `holiday_tracker/providers/travelpayouts.py`'s module docstring
  for the full evidence trail). They're Aviasales' cached fares, not a live GDS quote —
  there is currently no free live flight-price API for independent developers (Amadeus's
  free self-service tier shut down in mid-2026). A tightly constrained date rule (an
  exact weekday pair and exact nights count) can legitimately return nothing at all,
  because the free calendar data is a sparse, opportunistic set, not an exhaustive grid —
  that's correct behaviour, not a bug. Always verify the booking link before paying.
- **There is currently no free live hotel-price API at all.** Hotellook — the service
  this project was originally built against — shut down permanently on 2025-10-15
  (confirmed live: every path on its API domain 404s via a CloudFront edge with no
  origin behind it, and Travelpayouts' own closure FAQ states no replacement hotel
  brand currently offers partners an API). RateHawk and Booking.com both have hotel
  APIs, but neither offers a self-serve token — both require manual partnership
  approval, and Booking.com's terms additionally prohibit AI-system use without their
  prior written approval. So a real (`--provider travelpayouts`) search still tries the
  live endpoints first, but falls back to an estimated nightly rate from the bundled
  catalog (`holiday_tracker/catalog/nightly_rate.yaml`) when they fail — which, right
  now, is always. That estimate is clearly labelled (`confidence: city_median_estimate`,
  "not an observed listing" in the report) and only prices, not property type, rating,
  distance, or cancellation policy — a stay filter that depends on one of those (min
  rating, max distance, free cancellation) correctly rejects the estimate rather than
  guessing, so a filtered real search can show fewer results, or none, while this
  outage lasts. See `holiday_tracker/providers/hotellook.py`'s module docstring for
  the full evidence trail.
- **Daily-spend figures are estimates** from a hand-maintained dataset
  (`holiday_tracker/catalog/spend.yaml`), not a live cost-of-living feed. Correct them
  there if you know better for a given city.
- **FX conversion is approximate**, via a small static reference table
  (`holiday_tracker/engine/pricing.py`), not a live rate feed.

## Development

```bash
pip install -e ".[dev]"
ruff check .
pytest --cov=holiday_tracker
```

Tests run entirely offline against a deterministic fixtures provider and an
autouse fixture that redirects all local state to a temp directory (see
`tests/conftest.py`) — no API token, network access, or risk to your real
`~/.holiday-tracker/` is involved in running the suite.

### Project layout

```
holiday_tracker/
  cli.py                # Typer app: search, watch add/list/rm/run, report, dates
  wizard.py              # interactive quick-list prompt flow
  report.py              # rich terminal tables + standalone HTML report
  models.py               # SearchSpec, DateRule, StayFilters, Money, Package, ...
  dates.py                 # DateRule -> concrete (depart, return) date pairs
  catalog/                  # bundled destination + cost-of-living data (YAML)
  providers/                 # FlightProvider/StayProvider: fixtures, Travelpayouts, Hotellook
  engine/                      # the three-stage search funnel, pricing, filters, ranking
  store/                        # SQLite watch/run history
  alerts/                        # fits-budget detection + SMTP email
```
