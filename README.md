# Guernsey fireworks map

Map of registered firework displays for The Quarry, served at https://the-quarry.github.io/fireworks/ and embedded in the "Fireworks - where and when" story.

## Data

`events.json` is rebuilt from https://www.gov.gg/fireworks by `scripts/update_events.py`. The GitHub Action in `.github/workflows/update-events.yml` runs it every two hours from October to December and commits any change. Run it by hand from the Actions tab ("Run workflow") or locally:

    pip install beautifulsoup4
    python scripts/update_events.py

gov.gg only lists forthcoming displays, so past displays are kept from the previous run. A future display that drops off the list is kept and marked `no_longer_listed`; if the same address reappears on another date it is marked as moved (`rescheduled_from`).

## Locations

Locations come from `scripts/geocache.json`, checked in this order: exact address, postcode (seeded from the 2025 map), then Photon (OpenStreetMap) by postcode, address and road. OSM holds very few Guernsey postcodes, so most new entries resolve to the road. To correct a pin, edit or add its entry in `geocache.json`; the key is the address lower-cased with punctuation removed, and the script never overwrites an existing entry.

## Embed

Paste the contents of `embed.html` into a Ghost HTML card. It passes `?night=` from the story's URL to the map, so a social post linking to `.../story-slug/?night=fri` opens the map on Friday. Accepted values: `thu`, `friday`, `tonight`, `all`, a day of the month (`7`) or a date (`2026-11-07`). Anything unrecognised falls back to the normal default.

`allow="geolocation"` lets "Displays near me" use the reader's location. Without it, readers tap the map instead. For testing, `?today=2026-11-05` on the map's own URL fakes the date.

`archive/2025.json` holds last year's final list for the year-on-year comparison.
