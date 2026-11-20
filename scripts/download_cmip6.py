#!/usr/bin/env python3
"""CMIP6 downloader for the CDS projections-cmip6 dataset.

Supports selecting variables (--variables) and a lat/lon box (--box).

IMPORTANT:
- Check model and experiment identifiers against the live CDS dataset form.
- CDS availability differs by model, experiment, variable and temporal resolution.
- This script records failures and continues to the next requested job.
"""
import argparse
import csv
import json
import logging
import shutil
import zipfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent  # project folder (contains config/, scripts/)
CONFIG = ROOT / "config/cmip6_config.json"
LOG_DIR = ROOT / "logs"
META_DIR = ROOT / "metadata"
DATASET = "projections-cmip6"

INVENTORY_FIELDS = ["date", "model", "experiment", "resolution", "variable",
                    "area_NWSE", "start_year", "end_year", "status", "output"]

LOG_DIR.mkdir(parents=True, exist_ok=True)
META_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "cmip6_download.log"),
        logging.StreamHandler()
    ],
)
with CONFIG.open() as f:
    cfg = json.load(f)


def inventory(record):
    path = META_DIR / "download_inventory.csv"
    # If an inventory from an older version (different columns) exists, keep it aside.
    if path.exists():
        with path.open(newline="") as f:
            header = next(csv.reader(f), [])
        if header != INVENTORY_FIELDS:
            path.replace(META_DIR / f"download_inventory_old_{datetime.now():%Y%m%d%H%M%S}.csv")
    exists = path.exists()
    with path.open("a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=INVENTORY_FIELDS)
        if not exists:
            writer.writeheader()
        writer.writerow(record)


def fmt(v):
    """Compact number for filenames: 2.0 -> 2, -5.5 -> m5.5."""
    s = f"{v:g}"
    return s.replace("-", "m")


def box_to_area(box):
    """--box MINLAT MAXLAT MINLON MAXLON  ->  CDS area [North, West, South, East]."""
    minlat, maxlat, minlon, maxlon = box
    return [maxlat, minlon, minlat, maxlon]


def area_tag(area):
    north, west, south, east = area
    return f"lat{fmt(south)}to{fmt(north)}_lon{fmt(west)}to{fmt(east)}"


def validate_box(parser, box):
    minlat, maxlat, minlon, maxlon = box
    if not (-90 <= minlat < maxlat <= 90):
        parser.error("--box: need -90 <= MINLAT < MAXLAT <= 90")
    if not (-180 <= minlon < maxlon <= 360):
        parser.error("--box: need -180 <= MINLON < MAXLON <= 360 "
                     "(boxes crossing the dateline are not supported; "
                     "use 0..360 longitudes for such a region)")


def download_one(client, model, experiment, resolution, var_key, area):
    var = cfg["variables"][var_key]
    start, end = cfg["experiments"][experiment]
    folder = (ROOT / cfg["output_dir"]) / resolution / experiment
    folder.mkdir(parents=True, exist_ok=True)
    # Variable and box are part of the filename so different requests never collide.
    stem = f"{model}_{var_key}_{area_tag(area)}_{start}0101_{end}1231"
    target = folder / f"{stem}.nc"
    archive_path = folder / f"{stem}.zip"

    if target.exists() and target.stat().st_size > 0:
        logging.info("SKIP existing file: %s", target)
        return

    request = {
        "temporal_resolution": resolution,
        "experiment": experiment,
        "variable": var["cds_name"],
        "model": model,
        "year": [str(y) for y in range(start, end + 1)],
        "month": cfg["months"],
        "area": area,
    }

    base = {
        "model": model, "experiment": experiment, "resolution": resolution,
        "variable": var["cds_name"], "area_NWSE": str(area),
        "start_year": start, "end_year": end,
    }
    try:
        logging.info("Submitting %s | %s | %s | %s | area=%s",
                     model, experiment, resolution, var["cds_name"], area)
        client.retrieve(DATASET, request, str(archive_path))

        if zipfile.is_zipfile(archive_path):
            with zipfile.ZipFile(archive_path) as zf:
                members = [m for m in zf.infolist()
                           if m.filename.lower().endswith(".nc") and not m.is_dir()]
                if not members:
                    raise RuntimeError("CDS archive contained no .nc files")
                for i, member in enumerate(members, 1):
                    out = target if len(members) == 1 else folder / f"{stem}_part{i:02d}.nc"
                    with zf.open(member) as src, out.open("wb") as dst:
                        shutil.copyfileobj(src, dst)
                    logging.info("Saved %s", out)
            archive_path.unlink(missing_ok=True)
        else:
            # A direct response may be NetCDF; preserve the requested filename.
            archive_path.replace(target)

        inventory({**base, "date": datetime.now().isoformat(timespec="seconds"),
                   "status": "SUCCESS", "output": str(target)})
    except Exception as exc:
        logging.exception("FAILED %s / %s / %s / %s",
                          model, experiment, resolution, var["cds_name"])
        archive_path.unlink(missing_ok=True)
        inventory({**base, "date": datetime.now().isoformat(timespec="seconds"),
                   "status": f"FAILED: {exc}", "output": ""})


def main():
    var_keys = list(cfg["variables"])
    parser = argparse.ArgumentParser(
        description="CMIP6 downloader (CDS projections-cmip6)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Variable short names:\n" + "\n".join(
            f"  {k:8s} {v['label']}  ({v['cds_name']})"
            for k, v in cfg["variables"].items()),
    )
    parser.add_argument("--models", nargs="+", help="CDS model identifiers")
    parser.add_argument("--all-models", action="store_true")
    parser.add_argument("--experiments", nargs="+", default=["historical"])
    parser.add_argument("--resolutions", nargs="+", default=["daily"])
    parser.add_argument("--variables", nargs="+", metavar="VAR",
                        help="Variables to download: short name (e.g. tas, pr) or full CDS "
                             f"name. Choices: {', '.join(var_keys)}. "
                             f"Default: {' '.join(cfg['default_variables'])}")
    parser.add_argument("--all-variables", action="store_true",
                        help="Download all configured variables")
    parser.add_argument("--list-variables", action="store_true",
                        help="List available variables and exit")
    parser.add_argument("--box", nargs=4, type=float,
                        metavar=("MINLAT", "MAXLAT", "MINLON", "MAXLON"),
                        help="Bounding box to download. Default: the 'area' in the config "
                             "file (N, W, S, E).")
    args = parser.parse_args()

    if args.list_variables:
        for k, v in cfg["variables"].items():
            print(f"{k:8s} {v['cds_name']:48s} {v['label']}")
        return

    # Resolve variables (accept short names or full CDS names, case-insensitive).
    by_cds = {v["cds_name"]: k for k, v in cfg["variables"].items()}
    if args.all_variables:
        variables = var_keys
    else:
        variables = []
        for name in (args.variables or cfg["default_variables"]):
            key = name.lower()
            key = key if key in cfg["variables"] else by_cds.get(key)
            if key is None:
                parser.error(f"Unknown variable: {name} (use --list-variables)")
            if key not in variables:
                variables.append(key)

    # Resolve area: --box overrides the config default.
    if args.box:
        validate_box(parser, args.box)
        area = box_to_area(args.box)
    else:
        area = cfg["area"]

    models = cfg["models"] if args.all_models else (args.models or ["cesm2_fv2"])
    for m in models:
        if m not in cfg["models"]:
            parser.error(f"Model not listed in config: {m}")
    for e in args.experiments:
        if e not in cfg["experiments"]:
            parser.error(f"Experiment not listed in config: {e}")
    for r in args.resolutions:
        if r not in cfg["temporal_resolutions"]:
            parser.error(f"Resolution not enabled in config: {r}")

    # Import only after argument validation so --help works without cdsapi.
    import cdsapi
    client = cdsapi.Client()
    for resolution in args.resolutions:
        for experiment in args.experiments:
            for model in models:
                for var_key in variables:
                    download_one(client, model, experiment, resolution, var_key, area)


if __name__ == "__main__":
    main()
