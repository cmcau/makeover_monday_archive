# Large data files (stored compressed)

GitHub rejects any single file larger than **100 MB**. The data files below
exceed that, so instead of pushing the raw file we store a **compressed**
`.tar.gz` copy in the repo (CSVs typically shrink ~10x). If a compressed file is
still over the limit it's **split** into `<90 MB` parts named `.tar.gz.001`,
`.tar.gz.002`, … The raw, uncompressed originals stay in your local `output/`
folder but are git-ignored, so they aren't pushed.

| Raw size | Week | File |
|------|------|------|
| 1701 MB | 2018 W26 | `TFL Cycle Hire 2017.csv` |
| 826 MB | 2021 W16 | `US Monthly Air Passengers.csv` |
| 674 MB | 2023 W23 | `Melbourne Pedestrians.csv` |
| 567 MB | 2019 W11 | `philly real estate.csv` |
| 266 MB | 2019 W11 | `philly real estate.hyper` |
| 239 MB | 2019 W25 | `Airbnb Berlin.csv` |
| 180 MB | 2023 W06 | `foia-7afy2010-fy2019-asof-220930.csv` |
| 170 MB | 2023 W39 | `Hyperlocal_Temperature_Monitoring.csv` |
| 128 MB | 2019 W25 | `Airbnb Berlin.xlsx` |
| 112 MB | 2020 W12 | `Courses_Berkeley_2018-01-15.csv` |

> `.hyper` and `.xlsx` are already-compressed formats, so they shrink little and
> will likely be split into parts rather than fitting in one `.tar.gz`.

## How to create the compressed copies

```powershell
powershell -ExecutionPolicy Bypass -File .\compress_large_files.ps1
powershell -ExecutionPolicy Bypass -File .\push_in_batches.ps1
```

The first script compresses (and splits if needed) every oversized file and
adds the raw original to `.gitignore`. The second commits and pushes the new
`.tar.gz` / `.tar.gz.NNN` files a week at a time.

## How to restore the originals (e.g. after cloning)

```powershell
powershell -ExecutionPolicy Bypass -File .\restore_large_files.ps1
```

This reassembles any split parts and extracts every `.tar.gz` back to its
original file. (Manual equivalent for one split file: `copy /b name.tar.gz.001 + name.tar.gz.002 name.tar.gz`
then `tar -xzf name.tar.gz`.)
