#!/usr/bin/env python3
"""Basic inventory and structural validation of downloaded NetCDF files."""
from pathlib import Path
import pandas as pd
import xarray as xr

ROOT = Path("/home/user/DATA/cmip6/data")
REPORT = Path("/home/user/DATA/cmip6/metadata/netcdf_validation_report.csv")
records = []

for path in ROOT.rglob("*.nc"):
    row = {"file": str(path), "status": "VALID"}
    try:
        with xr.open_dataset(path, decode_times=True) as ds:
            time = next((x for x in ("time", "valid_time") if x in ds.coords), None)
            lat = next((x for x in ("lat", "latitude") if x in ds.coords), None)
            lon = next((x for x in ("lon", "longitude") if x in ds.coords), None)
            row.update({
                "variables": ",".join(ds.data_vars),
                "dimensions": str(dict(ds.sizes)),
                "time_start": str(ds[time].values[0]) if time and ds.sizes.get(time, 0) else "",
                "time_end": str(ds[time].values[-1]) if time and ds.sizes.get(time, 0) else "",
                "latitude_coordinate": lat or "",
                "longitude_coordinate": lon or "",
                "file_size_bytes": path.stat().st_size,
            })
    except Exception as exc:
        row["status"] = f"ERROR: {exc}"
    records.append(row)

REPORT.parent.mkdir(parents=True, exist_ok=True)
pd.DataFrame(records).to_csv(REPORT, index=False)
print(f"Validated {len(records)} files. Report: {REPORT}")
