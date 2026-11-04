# Settings

| File | What it sets |
|---|---|
| `report.json` | Water cost per m³ for the money figures (hidden while it is `null`), currency, default report length |
| `acoustic.json` | Acoustic noise loggers: how many (km of pipe per logger) and how far one hears a leak |
| `priority.json` | Repair-priority weights (water saved, households, ease), household water use, how pipe suspects are ranked, what makes a repair hard |

If a file is missing or broken, the backend logs a warning and uses built-in defaults (the same values as shipped here).
Values marked as assumptions should be replaced with the utility's real figures.

## people.json

Staff accounts, crew logins (staff id + PIN) and crews. Accounts are copied into the database (hashed) on the very first start only. Missing or broken file: built-in values are used and a warning is logged.

## network.json

How the live network behaves: leaks per day (whole network), size mix (large ones are bursts), the most unfixed leaks at once, how often the detector scans, the pause after an alert closed with no leak found, the burst thresholds, and which direct leak signs an alert needs.

## public.json

Limits for public reports: reports per phone per hour and per day, and how long the same photo is refused (`same_photo_max_diff`: how close two photo fingerprints must be to count as the same photo; copies and resized versions are caught, new photos of the same leak are not). `zone_snap_m`: a spot just outside every zone outline still belongs to the nearest zone when one of its pipes is this close (default 400 m).

## acoustic.json

Acoustic noise loggers: one logger for every `km_of_pipe_per_logger` km of pipe in each zone, spread evenly, and how far one hears a leak (`hearing_range_m`). The layout is worked out from the network at start-up and is the same every time. Changing these numbers moves the loggers.
