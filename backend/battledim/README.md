# Testing DloWatch on BattLeDIM L-Town

BattLeDIM 2020 is a public leak detection benchmark: https://github.com/KIOS-Research/BattLeDIM

## 1. Get the files (about 180 MB, not in this repo)

Put these in one folder, for example `C:\data\ltown`:

- `L-TOWN_v2_Real.inp`
  https://media.githubusercontent.com/media/KIOS-Research/BattLeDIM/master/Dataset%20Generator/L-TOWN_v2_Real.inp
- `dataset_configuration_evaluation.yalm` (2019 leaks) and `dataset_configuration_historical.yalm` (2018 leaks)
  from the `Dataset Generator` folder of the same repo

## 2. Rebuild the sensor data (about 1 minute per year)

```
cd backend
py -3.13 battledim/simulate_ltown.py C:\data\ltown 2018
py -3.13 battledim/simulate_ltown.py C:\data\ltown 2019
```

## 3. Tune on 2018, test on 2019

```
py -3.13 battledim/evaluate_ltown.py C:\data\ltown tune
py -3.13 battledim/evaluate_ltown.py C:\data\ltown 2019 wl-1.2
py -3.13 battledim/evaluate_ltown.py C:\data\ltown 2019 wl-1.3
```

## 4. Train the locator (about 3 minutes)

Also needs `L-TOWN_v2_Model.inp` (the utility's model) in the same folder:
https://media.githubusercontent.com/media/KIOS-Research/BattLeDIM/master/Dataset%20Generator/L-TOWN_v2_Model.inp

```
py -3.13 battledim/train_ltown.py C:\data\ltown
```

Results go to `evaluation/battledim_*.json`. The app shows them on the Water loss report page.
The write-up is `evaluation/battledim.md`.
