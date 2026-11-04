# DloWatch evaluation

Run 2026-11-02 21:12, ruleset wl-1.3, 352 simulated scenarios (8 of each kind per zone, 8 zones), seed 7, 123 s.
Noise: water use +-5%, pressure sensors +-1 mm (same as the training data). Simulated with EPANET on the illustrative North Mauritius network, not real CWA data.

## Does it find leaks?

| Scenario | Alerts raised |
|---|---|
| Slow leaks | 62/64 (97%) |
| · started under 1 day ago | 7/7 (100%) |
| · started 1 to 2 days ago | 20/20 (100%) |
| · started over 2 days ago | 35/37 (95%) |
| Bursts | 64/64 (100%), Critical 100% |

Night flow only counts after 2 high nights (persistence rule). Young slow leaks can still be raised earlier by the water balance, pressure drops and the model.

## Does it raise false alarms?

| Scenario | False alarms |
|---|---|
| Normal day (no leak) | 1/64 (2%) |
| A faulty meter (no leak) | 4/64 (6%) |
| · spikes | 0/11 |
| · stuck | 0/9 |
| · drift | 1/7 |
| · gaps | 1/10 |
| · reset | 1/15 |
| · offline | 1/12 |

## Does it find the right place?

| | With the model | Without the model (rules only) |
|---|---|---|
| Leaks alerted | 126/128 | 52/64 |
| Typical distance off (median) | 396 m | 944 m |
| 8 in 10 within | 670 m | 1840 m |
| Leak pipe inside the search area | 90% | 60% |
| Leak in the first block to check | 21% | 6% |
| Leak in the top 3 blocks | 44% | 19% |
| Leak in the top 5 blocks | 56% | 25% |
| Size estimate off by (median) | 1.7% | 12.0% |

## Public reports

- 3 reports from one phone grouped into an alert: 0/16 (should be 0)
- 3 reports from 3 phones grouped: 16/16, above Watch: 0 (should be 0)

## Limits (read before quoting these numbers)

- Everything is simulated on an illustrative network with assumed pipes and demand. Real networks are messier.
- The meter faults tested are the same kinds the cleaning step was built for. Real meters will fail in other ways too, so expect some false alarms in practice.
- Leaks are single emitters on one pipe. Several leaks at once, or leaks that grow over weeks, are not tested here (see the BattLeDIM test for that).
- The model was trained on the same simulated network, so location results are likely better than on a new real network.
