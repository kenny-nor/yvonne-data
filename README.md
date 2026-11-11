# Yvonne's Lightning Strikes: automatic data

This folder keeps the app's football data fresh by itself.

- `build_snapshot.py` downloads the free nflverse files (schedule, scores, betting lines, team and player stats,
  depth charts, injury reports, play-by-play) and builds `data/snapshot.json`.
- `.github/workflows/update-data.yml` runs it on a timer:
  - every day at 6:17 AM Eastern (picks up Monday-night results)
  - Wednesday through Saturday at 1:17 PM and 5:17 PM Eastern (injury reports)
  - Sunday at 10:17 AM Eastern (last update before the early games)
- `index.html` is a status page that shows when the data was last updated.

If anything goes wrong (a download fails, or the data looks incomplete or older than what's already published),
the update stops and the last good data stays online. Nothing broken is ever published.

To refresh right now: go to the **Actions** tab, pick **Update Yvonne data**, and tap **Run workflow**.

Next season: change `--season 2026` to `--season 2027` in `.github/workflows/update-data.yml`.

Data: nflverse (https://github.com/nflverse). Not affiliated with the NFL.
