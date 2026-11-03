# BattLeDIM L-Town test (rules only)

L-Town is the public benchmark network of the Battle of the Leakage Detection and Isolation Methods (BattLeDIM 2020): 782 junctions, 905 pipes, 33 pressure sensors, a year of realistic 5-minute demand, and an official list of leaks. Settings were tuned on the 2018 year and then tested once on 2019.

Scored nights: 308 (from 27 Feb 2019, both rulesets on the same nights). Leaks that start in that period: 16 (6 sudden, 10 slowly growing).

## 2019 test

| | wl-1.2 | wl-1.3 (tuned on 2018) |
|---|---|---|
| Found within 3 days | 2/16 | 5/16 |
| Found within 7 days | 3/16 | 7/16 |
| Found within 30 days | 11/16 | 13/16 |
| Median days to find | 10.7 | 5.6 |
| False alarms (whole town) | 9 (0.89 a month) | 7 (0.69 a month) |
| Sensor with the biggest drop, distance to the leak (median) | 595 m | 1275 m |
| Leak within 1200 m of that sensor | 73% | 46% |

## Leaks, wl-1.3

| Pipe | Type | Start | Peak size (m³/h) | Result | Days to find |
|---|---|---|---|---|---|
| p257 | incipient | 2019-01-01 | 6.9 | already leaking on 1 Jan, nothing to compare with |  |
| p427 | incipient | 2019-01-01 | 5.1 | already leaking on 1 Jan, nothing to compare with |  |
| p810 | incipient | 2019-01-01 | 6.9 | already leaking on 1 Jan, nothing to compare with |  |
| p654 | incipient | 2019-01-01 | 5.5 | already leaking on 1 Jan, nothing to compare with |  |
| p523 | abrupt | 2019-01-15 | 28.4 | before scoring starts |  |
| p827 | abrupt | 2019-01-24 | 26.5 | before scoring starts |  |
| p280 | abrupt | 2019-02-10 | 5.3 | before scoring starts |  |
| p653 | incipient | 2019-03-03 | 18.3 | found | 5.6 |
| p710 | abrupt | 2019-03-24 | 5.6 | found | 0.6 |
| p514 | abrupt | 2019-04-02 | 15.6 | found | 0.3 |
| p331 | abrupt | 2019-04-20 | 10.9 | started while another alert was open |  |
| p193 | incipient | 2019-05-19 | 10.4 | found | 25.7 |
| p277 | incipient | 2019-05-30 | 7.4 | found | 14.3 |
| p142 | abrupt | 2019-06-12 | 27.0 | found | 1.3 |
| p680 | abrupt | 2019-07-10 | 5.4 | started while another alert was open |  |
| p586 | incipient | 2019-07-26 | 20.5 | found | 23.6 |
| p721 | incipient | 2019-08-02 | 13.2 | found | 17.0 |
| p800 | incipient | 2019-08-16 | 22.0 | found | 2.6 |
| p123 | incipient | 2019-09-13 | 9.2 | started while another alert was open |  |
| p455 | incipient | 2019-10-03 | 11.1 | found | 23.6 |
| p762 | incipient | 2019-10-09 | 15.7 | found | 17.7 |
| p426 | abrupt | 2019-10-25 | 13.6 | found | 1.6 |
| p879 | incipient | 2019-11-20 | 10.9 | found | 4.7 |

False alarm dates: 17 Mar, 29 Mar, 10 Jul, 22 Aug, 12 Nov, 30 Nov, 04 Dec.

## 2018 tuning (the only data used to choose wl-1.3)

| Usual night | Pressure wobble check | Found / 10 | Within 7 days | False alarms |
|---|---|---|---|---|
| last 7 nights | off | 6 | 5 | 19 |
| last 7 nights | 2x | 8 | 7 | 24 |
| last 7 nights | 3x | 8 | 7 | 22 |
| last 7 nights | 4x | 8 | 7 | 22 |
| last 7 nights | 5x | 8 | 7 | 22 |
| last 7 nights | 6x | 8 | 7 | 22 |
| same weekday, 4 weeks | off | 3 | 3 | 3 |
| same weekday, 4 weeks | 2x | 6 | 5 | 7 |
| same weekday, 4 weeks | 3x | 8 | 7 | 4 |
| same weekday, 4 weeks | 4x | 8 | 7 | 4 |
| same weekday, 4 weeks | 5x | 8 | 8 | 5 |
| same weekday, 4 weeks | 6x | 8 | 8 | 4 |

Chosen: same weekday over 4 weeks, and a drop must be 3 times the sensor's usual wobble (the smallest setting with the best 2018 result).

## What this shows

- Real-looking demand is harder than our Port Louis simulation: busy weekend nights and pressure that moves by hundreds of mm every night caused most false alarms with wl-1.2.
- Comparing with the same weekday and ignoring each sensor's normal wobble roughly doubled the leaks found within a week.
- Rules alone are weak at finding the place on L-Town: the sensor with the biggest drop is often over 1 km from the leak, and wl-1.3 made this worse (it ignores drops at sensors that wobble a lot, and some of those were the close ones). Finding the place is the job of the AI model (next section).

## Finding the place: AI model trained for L-Town

The town is small: about 2.7 km by 1.4 km (43 km of pipe). Distances below should be read against that.

- Trained on 12,000 leaks simulated on **L-TOWN_v2_Model.inp**, the utility's own model. The 2019 data came from the "real" network, which differs (demands, valve sizes), so this is like a utility using its imperfect model.
- Noise: each simulated leak was added to the real night-to-night wobble of a quiet 2018 night (all 33 sensors together). 2019 was only used for the final test.
- Features: night pressure drop at the 33 sensors vs the same weekday over 4 weeks, each sensor's share of the drop, and the rise in night inflow. Models: an area classifier (30 areas, about 181 m radius) and a best-guess point.
- On simulated leaks it is 113 m off (median), with the right area in the top 3 for 80%. Real-looking data is much harder:

| Year | Leaks | AI model, median off | Rules only, median off | AI within 500 m | Rules within 500 m | Right area in AI top 3 |
|---|---|---|---|---|---|---|
| 2018 (validation), alert night | 8 | 502 m | 885 m | 4/8 | 4/8 | 3/8 |
| 2018 (validation), 7 days after start | 10 | 167 m | 185 m | 8/10 | 7/10 | 7/10 |
| **2019 (test), alert night** | 13 | 803 m | 1249 m | 5/13 | 2/13 | 3/13 |
| 2019 (test), 7 days after start | 16 | 938 m | 1308 m | 4/16 | 3/16 | 2/16 |

"Rules only" = the sensor with the biggest drop. The model has the smaller median error in every row, but it is still often far off in 2019, where many slow leaks run at the same time, so the "usual" nights already include other leaks. The choice of area ranking (classifier rather than nearest areas to the point) was made on 2018.

Run it: `python battledim/train_ltown.py <folder>` (about 3 minutes, after the two simulate steps).

## Limits

- The official 2019 sensor files are on Zenodo, which could not be reached here. The data was rebuilt from the official network and leak list with `battledim/simulate_ltown.py` (EPANET 2.2, demand-driven, leaks as emitters; the official generator used WNTR, pressure-driven). Numbers will differ a little from the official files.
- No sensor noise was added beyond the official rounding (0.01 m, 0.01 m³/h).
- The whole town is treated as one zone with one inlet (the two reservoir pipes, corrected for the tank), so a leak that starts while another alert is open is not counted as found.
- An alert counts for a leak if it opens within 30 days of the leak starting. With about one false alarm every 6 weeks, some late matches may be luck, which is why the 3 and 7 day numbers are shown.
- This is not the official BattLeDIM score, which also rates the location of each leak.

## Run it

```
python battledim/simulate_ltown.py <folder> 2018     # about 1 minute each
python battledim/simulate_ltown.py <folder> 2019
python battledim/evaluate_ltown.py <folder> tune     # 2018 only
python battledim/evaluate_ltown.py <folder> 2019 wl-1.2
python battledim/evaluate_ltown.py <folder> 2019 wl-1.3
```
See battledim/README.md for the downloads.
