# DloWatch evaluation

Run 2026-11-04 15:00, ruleset wl-1.5, 352 simulated scenarios (8 of each kind per zone, 8 zones), seed 7, 328 s.
Noise: water use +-5%, pressure sensors +-1 mm (same as the training data). Simulated with EPANET on the illustrative North Mauritius network, not real CWA data.

## Does it find leaks?

| Scenario | Alerts raised |
|---|---|
| Slow leaks | 61/64 (95%) |
| · started under 1 day ago | 7/7 (100%) |
| · started 1 to 2 days ago | 19/20 (95%) |
| · started over 2 days ago | 35/37 (95%) |
| Bursts | 64/64 (100%), Critical 100% |

Night flow only counts after 2 high nights (persistence rule). Young slow leaks can still be raised earlier by the water balance, pressure drops and the model.

## Does it raise false alarms?

| Scenario | False alarms |
|---|---|
| Normal day (no leak) | 2/64 (3%) |
| A faulty meter (no leak) | 4/64 (6%) |
| · spikes | 1/11 |
| · stuck | 1/9 |
| · drift | 0/7 |
| · gaps | 1/10 |
| · reset | 1/15 |
| · offline | 0/12 |

## Does it find the right place?

| | With the model | Without the model (rules only) |
|---|---|---|
| Leaks alerted | 125/128 | 55/64 |
| Typical distance off (median) | 308 m | 713 m |
| 8 in 10 within | 609 m | 1999 m |
| Leak pipe inside the search area | 93% | 62% |
| Leak in the first block to check | 27% | 14% |
| Leak in the top 3 blocks | 50% | 31% |
| Leak in the top 5 blocks | 66% | 36% |
| Size estimate off by (median) | 1.6% | 15.9% |

## Noise loggers: before and after

448 loggers, 49% of pipe length within 300 m of one. Each leak is run twice with the same random draws: scored with wl-1.3 (no loggers) and with wl-1.5.

| Leaks | n | Alerted | Median distance off | Leak in the first block | Leak in the top 3 blocks | Loggers heard it |
|---|---|---|---|---|---|---|
| All leaks | 128 | 98% → 98% | 384 → 308 m | 22% → 27% | 45% → 50% | 19% |
| Slow leaks running 2+ nights | 37 | 95% → 95% | 428 → 172 m | 17% → 34% | 43% → 63% | 51% |
| Slow leaks running 2+ nights, within hearing range of a logger | 23 | 91% → 91% | 384 → 167 m | 14% → 43% | 43% → 76% | 81% |
| Bursts (under 6 hours old, loggers have not recorded yet) | 64 | 100% → 100% | 316 → 316 m | 27% → 27% | 50% → 50% | 5% |
| Rules only (model switched off) | 64 | 80% → 86% | 994 → 713 m | 6% → 14% | 22% → 31% | 24% |

Loggers record once a night and a logger only counts after 2 loud nights, so they cannot help with bursts or leaks younger than 2 nights. About half the pipes are out of hearing range; leaks there are found as before.

## Public reports

- 3 reports from one phone grouped into an alert: 0/16 (should be 0)
- 3 reports from 3 phones grouped: 16/16, above Watch: 0 (should be 0)

## Limits (read before quoting these numbers)

- Everything is simulated on an illustrative network with assumed pipes and demand. Real networks are messier.
- The meter faults tested are the same kinds the cleaning step was built for. Real meters will fail in other ways too, so expect some false alarms in practice.
- Leaks are single emitters on one pipe. Several leaks at once, or leaks that grow over weeks, are not tested here (see the BattLeDIM test for that).
- The model was trained on the same simulated network, so location results are likely better than on a new real network.
- Logger noise is simulated with a simple model (leak hiss louder with more water, fading along the pipe). Real loggers depend on pipe material, depth and street noise, and the hearing range is an assumption to check with a supplier.
