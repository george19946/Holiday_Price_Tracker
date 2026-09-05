# Holiday Price Tracker

A **budget-first** holiday planner. Instead of picking a destination and watching the
budget slip, you state your constraints — where you're willing to fly from, where you
want to go (a city or a region), your date rules ("any Thursday–Sunday in 2027"), your
budget, and what you will and won't accept in accommodation — and the tool searches for
holidays that fit, or tells you exactly what to relax to make one fit, or tracks the
search over time and emails you the moment prices drop into budget.

> **Status:** under active development. See the build plan in this repo's history /
> `.github` for phase-by-phase progress. Interfaces below describe the target CLI and
> may not all be implemented yet — each phase's commit updates this section.

## How it works

The all-in cost of a trip is:

```
total = flights (party) + accommodation (party, room-split) + estimated daily spend (party)
```

Flight and accommodation prices come from the free [Travelpayouts](https://www.travelpayouts.com/)
data APIs (Aviasales flight-price calendars + Hotellook cached hotel prices). Because
these are **cached, indicative prices** rather than live bookable fares, every result
shown includes when the price was last observed and a link to verify it before booking.
Daily spend is estimated from a bundled, editable cost-of-living dataset
(`holiday_tracker/catalog/spend.yaml`), scaled by a `thrifty` / `normal` / `comfortable`
style multiplier.

The search itself runs in two stages to stay within free-tier rate limits: a wide,
cheap sweep of flight prices across every candidate month, followed by real hotel
pricing for only the most promising candidates.

## Setup

1. Python 3.11+.
2. `pip install -e ".[dev]"`
3. Free sign-up at [travelpayouts.com](https://www.travelpayouts.com/) to get an API
   token, then `export TRAVELPAYOUTS_TOKEN=...` (or put it in `holiday-track init`'s
   config file once that command is implemented).
4. `holiday-track init` — fetches the static city/hotel catalogues and checks your token.

## Usage

```bash
# Interactive — asks for origin, destination, dates, budget, party size, spend
# style, and accommodation filters as a quick list.
holiday-track search

# Non-interactive, e.g. for scripts or CI:
holiday-track search \
  --from LON,MAN --to "western europe" \
  --window 2027-01-01:2027-12-31 --depart-dow thu --return-dow sun \
  --nights 3-4 --blackout 2027-12-20:2027-01-03 \
  --budget 500 --party 2 --style normal \
  --no-hostels --min-rating 7.5 --max-centre-km 3 --free-cancellation

# Persist a search as a watch, and re-price it later (this is what the
# scheduled GitHub Action runs daily):
holiday-track watch add --from-last
holiday-track watch run
holiday-track watch list

# Price history and trend for a watch:
holiday-track report <watch-id>
```

### Output

If a package fits your budget and filters, it's shown as the top result. If nothing
fits, you get the cheapest near-misses, each annotated with the single cheapest change
that would close the gap — e.g. *"£540 — £40 over budget. Fly Friday instead of
Thursday: saves £60. ✅ closes the gap."*

### Tracking / alerts

`holiday-track watch add` persists a search's constraints to a local SQLite database.
A scheduled GitHub Actions workflow (`.github/workflows/track.yml`) re-runs active
watches daily, appends observations to `data/history/`, and emails you the moment a
watch's best package first comes in at or under budget (with a cooldown so one volatile
fare doesn't email you repeatedly).

## Data sources and honest limitations

- **Prices are indicative, not bookable.** They come from Travelpayouts' cached fare
  and hotel-price data, not a live booking API — there is currently no free live-price
  API for independent developers (Amadeus's free self-service tier shut down in mid-2026).
  Always verify via the provided link before booking.
- **Hotel coverage is uneven**, especially for smaller cities — a stay estimate may fall
  back to a city-level median, and is labelled as such.
- **Daily-spend figures are estimates** from a hand-maintained dataset
  (`holiday_tracker/catalog/spend.yaml`), not a live cost-of-living feed. Correct them
  there if you know better for a given city.

## Development

```bash
pip install -e ".[dev]"
ruff check .
pytest --cov=holiday_tracker
```

Tests run entirely offline against a deterministic fixtures provider — no API token or
network access is required to develop or run CI.
