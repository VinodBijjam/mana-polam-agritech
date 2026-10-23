#!/usr/bin/env python3
"""
Farm stress monitor: Sentinel-2 + Google Earth Engine (NDRE & MSAVI)

What it does
------------
1. Loads your farm boundary (GeoJSON or KML).
2. Splits it into a regular grid of zones (default 40 m x 40 m, UTM-aligned).
3. Builds 5-day Sentinel-2 L2A composites, masking clouds with Cloud Score+.
4. Computes NDRE (B8A, B5) and MSAVI (B8, B4) and averages them per zone.
5. Compares the latest usable composite with the one ~7 days earlier and
   flags zones whose indices dropped significantly.
6. Writes CSV + GeoJSON outputs (zone IDs, lat/lon of zone centroids).

Usage
-----
python farm_stress_monitor.py --boundary farm.geojson --project YOUR_GCP_PROJECT_ID
"""

import argparse
import json
import math
import os
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

import ee
import numpy as np
import pandas as pd

S2_COLLECTION = "COPERNICUS/S2_SR_HARMONIZED"
CLOUD_SCORE_PLUS = "GOOGLE/CLOUD_SCORE_PLUS/V1/S2_HARMONIZED"
SQM_PER_ACRE = 4046.8564224
STEP_DAYS = 5  # composite window length = Sentinel-2 revisit


# ----------------------------------------------------------------------------
# 1. Boundary loading (GeoJSON / KML)
# ----------------------------------------------------------------------------
def _clean_ring(ring):
    """Keep only [lon, lat]; drop altitude if present."""
    return [[float(p[0]), float(p[1])] for p in ring]


def _polys_from_geojson_geometry(geom):
    t = geom.get("type")
    if t == "Polygon":
        return [[_clean_ring(r) for r in geom["coordinates"]]]
    if t == "MultiPolygon":
        return [[_clean_ring(r) for r in poly] for poly in geom["coordinates"]]
    if t == "GeometryCollection":
        out = []
        for g in geom.get("geometries", []):
            out += _polys_from_geojson_geometry(g)
        return out
    return []


def load_boundary(path):
    """Return an ee.Geometry (Polygon/MultiPolygon) from a .geojson/.json/.kml file."""
    ext = os.path.splitext(path)[1].lower()
    polys = []

    if ext in (".geojson", ".json"):
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        kind = data.get("type")
        if kind == "FeatureCollection":
            geoms = [ft["geometry"] for ft in data["features"] if ft.get("geometry")]
        elif kind == "Feature":
            geoms = [data["geometry"]]
        else:
            geoms = [data]
        for g in geoms:
            polys += _polys_from_geojson_geometry(g)

    elif ext == ".kml":
        root = ET.parse(path).getroot()
        for poly in root.iter():
            if not poly.tag.endswith("Polygon"):
                continue
            rings = []
            for node in poly.iter():  # document order: outer ring first, then holes
                if node.tag.endswith("coordinates") and node.text:
                    pts = [p.split(",") for p in node.text.split()]
                    rings.append(_clean_ring(pts))
            if rings:
                polys.append(rings)
    else:
        sys.exit(f"Unsupported boundary format '{ext}'. Use .geojson, .json or .kml "
                 "(unzip .kmz first).")

    if not polys:
        sys.exit("No polygon found in the boundary file.")

    return ee.Geometry.MultiPolygon(polys, proj="EPSG:4326", geodesic=False)


# ----------------------------------------------------------------------------
# 2. Zones
# ----------------------------------------------------------------------------
def build_zones(farm, zone_size, edge_buffer, min_frac):
    """
    Grid the farm in its local UTM zone so cells line up with Sentinel-2 pixels.
    Returns (zones_geojson_dict, epsg_code). Each feature gets zone_id, lat, lon, area_m2.
    """
    area = farm.buffer(-edge_buffer) if edge_buffer > 0 else farm  # avoid mixed edge pixels

    lon = farm.centroid(1).coordinates().getInfo()[0]
    epsg = 32600 + int((lon + 180) // 6) + 1  # India is in the northern hemisphere
    proj = ee.Projection(f"EPSG:{epsg}")

    grid = area.coveringGrid(proj, zone_size)

    def clip_cell(cell):
        g = cell.geometry().intersection(area, ee.ErrorMargin(1))
        return ee.Feature(g).set("area_m2", g.area(1))

    zones = grid.map(clip_cell).filter(ee.Filter.gte("area_m2", min_frac * zone_size ** 2))

    def add_centroid(f):
        c = f.geometry().centroid(1).coordinates()
        return f.set({"lon": c.get(0), "lat": c.get(1)})

    info = zones.map(add_centroid).getInfo()
    feats = info["features"]
    if not feats:
        sys.exit("No zones were created. Check the boundary or reduce --zone-size / --edge-buffer.")

    # North-to-south, west-to-east IDs
    feats.sort(key=lambda f: (-round(f["properties"]["lat"], 4), f["properties"]["lon"]))
    for i, f in enumerate(feats, 1):
        f["properties"] = {
            "zone_id": f"Z{i:02d}",
            "lat": f["properties"]["lat"],
            "lon": f["properties"]["lon"],
            "area_m2": f["properties"]["area_m2"],
        }
    info["features"] = feats
    return info, epsg


# ----------------------------------------------------------------------------
# 3. Sentinel-2: cloud masking + indices
# ----------------------------------------------------------------------------
def build_index_collection(farm, first_start, last_end, cloud_threshold):
    s2 = (ee.ImageCollection(S2_COLLECTION)
          .filterBounds(farm)
          .filterDate(first_start, last_end)
          .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", 90)))  # cheap pre-filter only
    cs = (ee.ImageCollection(CLOUD_SCORE_PLUS)
          .filterBounds(farm)
          .filterDate(first_start, last_end))
    linked = s2.linkCollection(cs, ["cs_cdf"])

    def compute(img):
        # Cloud Score+ : cs_cdf close to 1 = clear, close to 0 = cloud/shadow
        img = img.updateMask(img.select("cs_cdf").gte(cloud_threshold))
        r = img.select(["B4", "B5", "B8", "B8A"]).divide(10000).clamp(0, 1)

        ndre = r.normalizedDifference(["B8A", "B5"]).rename("NDRE")
        msavi = r.expression(
            "(2*NIR + 1 - sqrt((2*NIR + 1)*(2*NIR + 1) - 8*(NIR - RED))) / 2",
            {"NIR": r.select("B8"), "RED": r.select("B4")},
        ).rename("MSAVI")

        return ndre.addBands(msavi).copyProperties(img, ["system:time_start"])

    return linked.map(compute)


def collect_zone_stats(indices, zones_fc, windows, scale):
    """Zone means of NDRE/MSAVI + valid-pixel fraction for every 5-day window."""
    # Fully masked placeholder so median() never fails on an empty (all-cloudy) window
    empty = (ee.Image.constant([0.0, 0.0]).toFloat()
             .rename(["NDRE", "MSAVI"])
             .updateMask(ee.Image.constant(0)))

    merged = None
    for start, end in windows:
        window_col = indices.filterDate(start, end)
        n_images = window_col.size()
        comp = window_col.merge(ee.ImageCollection([empty])).median()
        comp = comp.addBands(comp.select("NDRE").mask().rename("valid"))

        stats = comp.reduceRegions(
            collection=zones_fc, reducer=ee.Reducer.mean(), scale=scale, tileScale=2
        )

        def strip(f, s=start, e=end, n=n_images):
            return ee.Feature(None, f.toDictionary()).set(
                {"win_start": s, "win_end": e, "n_images": n}
            )

        stats = stats.map(strip)
        merged = stats if merged is None else merged.merge(stats)

    return merged.getInfo()


# ----------------------------------------------------------------------------
# 4. Change detection
# ----------------------------------------------------------------------------
def analyse(df, args):
    df["win_start"] = pd.to_datetime(df["win_start"])
    df["win_end"] = pd.to_datetime(df["win_end"])
    df["win_mid"] = df["win_start"] + (df["win_end"] - df["win_start"]) / 2
    df["valid"] = df["valid"].fillna(0.0)

    # Ignore zone values built from too few clear pixels
    bad = df["valid"] < args.min_zone_valid
    df.loc[bad, ["NDRE", "MSAVI"]] = np.nan

    farm_cov = df.groupby("win_start")["valid"].mean()
    usable = sorted(farm_cov[farm_cov >= args.min_farm_valid].index)
    if len(usable) < 2:
        sys.exit("Fewer than 2 usable cloud-free windows in the lookback period. "
                 "Increase --lookback-days or lower --min-farm-valid / --cloud-threshold.")

    cur = usable[-1]
    mid = {w: df.loc[df["win_start"] == w, "win_mid"].iloc[0] for w in usable}
    target = mid[cur] - timedelta(days=7)
    older = [w for w in usable if w < cur]
    base = min(older, key=lambda w: abs((mid[w] - target).total_seconds()))
    gap_days = (mid[cur] - mid[base]).days

    if gap_days > args.max_baseline_gap:
        sys.exit(f"Closest earlier clear window is {gap_days} days before the latest one "
                 f"(limit {args.max_baseline_gap}). Too cloudy to compare reliably.")

    age = (datetime.now(timezone.utc).date() - mid[cur].date()).days
    print(f"\nCurrent  window : {cur.date()} -> {(df.loc[df.win_start == cur, 'win_end'].iloc[0] - timedelta(days=1)).date()} "
          f"(farm clear coverage {farm_cov[cur]:.0%})")
    print(f"Baseline window : {base.date()} -> {(df.loc[df.win_start == base, 'win_end'].iloc[0] - timedelta(days=1)).date()} "
          f"(farm clear coverage {farm_cov[base]:.0%})")
    print(f"Gap between window centres: {gap_days} days")
    if age > 10:
        print(f"WARNING: latest usable imagery is ~{age} days old (cloud cover).")

    c = df[df["win_start"] == cur].set_index("zone_id")[["NDRE", "MSAVI", "lat", "lon", "area_m2"]]
    b = df[df["win_start"] == base].set_index("zone_id")[["NDRE", "MSAVI"]]
    res = c.join(b, lsuffix="_now", rsuffix="_prev")

    min_abs = {"NDRE": args.min_abs_ndre, "MSAVI": args.min_abs_msavi}
    for idx in ("NDRE", "MSAVI"):
        diff = res[f"{idx}_now"] - res[f"{idx}_prev"]
        # relative change, denominator floored so near-zero (bare soil) values don't explode
        pct = diff / res[f"{idx}_prev"].abs().clip(lower=0.1) * 100
        res[f"{idx}_change"] = diff
        res[f"{idx}_pct"] = pct
        res[f"{idx}_drop"] = (pct <= -args.drop_pct) & (diff <= -min_abs[idx])

    res["flagged"] = res["NDRE_drop"] | res["MSAVI_drop"]
    res["severity"] = np.where(res["NDRE_drop"] & res["MSAVI_drop"], "HIGH",
                               np.where(res["flagged"], "WATCH", "OK"))
    no_data = res["NDRE_pct"].isna() & res["MSAVI_pct"].isna()
    res.loc[no_data, "severity"] = "NO_DATA"

    med_ndre = res["NDRE_pct"].median()
    med_msavi = res["MSAVI_pct"].median()
    print(f"Farm-wide median change: NDRE {med_ndre:+.1f}%  |  MSAVI {med_msavi:+.1f}%")
    if min(med_ndre, med_msavi) <= -args.drop_pct / 2:
        print("WARNING: the whole farm dropped, not just patches. Likely causes: harvest, "
              "irrigation/rain cycle, crop senescence, or residual haze. Treat zone flags with caution.")

    return res, cur, base


# ----------------------------------------------------------------------------
# 5. Output
# ----------------------------------------------------------------------------
def _native(v):
    if isinstance(v, (float, np.floating)):
        return None if math.isnan(v) else round(float(v), 4)
    if isinstance(v, np.bool_):
        return bool(v)
    return v


def write_outputs(res, zones_info, ts_df, cur, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    tag = cur.strftime("%Y%m%d")

    ts_path = os.path.join(out_dir, "zone_timeseries.csv")
    ts_df.sort_values(["zone_id", "win_start"]).to_csv(ts_path, index=False)

    flagged = res[res["flagged"]].copy()
    flagged["worst_pct"] = flagged[["NDRE_pct", "MSAVI_pct"]].min(axis=1)
    flagged = flagged.sort_values("worst_pct")
    cols = ["severity", "lat", "lon", "NDRE_prev", "NDRE_now", "NDRE_pct",
            "MSAVI_prev", "MSAVI_now", "MSAVI_pct"]
    flagged_path = os.path.join(out_dir, f"flagged_zones_{tag}.csv")
    flagged[cols].round(4).to_csv(flagged_path)

    # GeoJSON of ALL zones with flag attributes (open in QGIS / geojson.io)
    feats = []
    for f in zones_info["features"]:
        zid = f["properties"]["zone_id"]
        props = {"zone_id": zid}
        if zid in res.index:
            row = res.loc[zid]
            for k in ["severity", "flagged", "NDRE_prev", "NDRE_now", "NDRE_pct",
                      "MSAVI_prev", "MSAVI_now", "MSAVI_pct", "lat", "lon"]:
                props[k] = _native(row[k])
        feats.append({"type": "Feature", "geometry": f["geometry"], "properties": props})
    geo_path = os.path.join(out_dir, f"zones_change_{tag}.geojson")
    with open(geo_path, "w", encoding="utf-8") as fh:
        json.dump({"type": "FeatureCollection", "features": feats}, fh)

    print(f"\n=== {len(flagged)} of {len(res)} zones flagged ===")
    if len(flagged):
        show = flagged[cols].copy()
        show[["lat", "lon"]] = show[["lat", "lon"]].round(6)
        print(show.round(3).to_string())
    else:
        print("No significant drops detected.")
    print(f"\nSaved:\n  {flagged_path}\n  {geo_path}\n  {ts_path}")


# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------
def parse_args():
    p = argparse.ArgumentParser(description="Sentinel-2 NDRE/MSAVI zone stress monitor (GEE)")
    p.add_argument("--boundary", required=True, help="Farm boundary (.geojson/.json/.kml)")
    p.add_argument("--project", default=os.environ.get("EE_PROJECT"),
                   help="Google Cloud project ID registered for Earth Engine (or set EE_PROJECT)")
    p.add_argument("--lookback-days", type=int, default=60)
    p.add_argument("--zone-size", type=int, default=40,
                   help="Zone side in metres. Keep >= 40 (red-edge bands are 20 m).")
    p.add_argument("--edge-buffer", type=float, default=10,
                   help="Shrink boundary by N metres to avoid mixed edge pixels")
    p.add_argument("--min-zone-frac", type=float, default=0.35,
                   help="Drop edge cells with less than this fraction inside the farm")
    p.add_argument("--cloud-threshold", type=float, default=0.65,
                   help="Cloud Score+ cs_cdf cut-off (higher = stricter)")
    p.add_argument("--drop-pct", type=float, default=10.0,
                   help="Relative drop (%%) needed to flag a zone")
    p.add_argument("--min-abs-ndre", type=float, default=0.03)
    p.add_argument("--min-abs-msavi", type=float, default=0.04)
    p.add_argument("--min-farm-valid", type=float, default=0.7,
                   help="Min. fraction of farm with clear pixels for a window to be usable")
    p.add_argument("--min-zone-valid", type=float, default=0.5,
                   help="Min. fraction of clear pixels inside a zone")
    p.add_argument("--max-baseline-gap", type=int, default=14)
    p.add_argument("--out-dir", default="output")
    return p.parse_args()


def init_ee(project):
    if not project:
        sys.exit("Provide --project (or set EE_PROJECT) with your Earth Engine Cloud project ID.")
    try:
        ee.Initialize(project=project)
    except Exception:
        ee.Authenticate()
        ee.Initialize(project=project)


def main():
    args = parse_args()
    init_ee(args.project)

    farm = load_boundary(args.boundary)
    acres = farm.area(1).getInfo() / SQM_PER_ACRE
    print(f"Boundary area: {acres:.1f} acres")
    if abs(acres - 30) > 10:
        print("NOTE: area differs a lot from 30 acres. Check the boundary file / CRS (must be lon/lat).")

    zones_info, epsg = build_zones(farm, args.zone_size, args.edge_buffer, args.min_zone_frac)
    n_zones = len(zones_info["features"])
    print(f"Zones: {n_zones} cells of {args.zone_size} m (UTM EPSG:{epsg})")

    n_windows = args.lookback_days // STEP_DAYS
    if n_zones * n_windows > 4500:
        sys.exit("Too many zone x window records for one request (getInfo limit 5000). "
                 "Use a larger --zone-size or a shorter --lookback-days.")

    today = datetime.now(timezone.utc).date()
    windows = []
    for i in range(n_windows):
        end = today + timedelta(days=1) - timedelta(days=STEP_DAYS * i)  # end is exclusive
        start = end - timedelta(days=STEP_DAYS)
        windows.append((start.isoformat(), end.isoformat()))
    windows.reverse()

    indices = build_index_collection(farm, windows[0][0], windows[-1][1], args.cloud_threshold)
    zones_fc = ee.FeatureCollection([ee.Feature(f) for f in zones_info["features"]])

    print(f"Fetching {n_windows} five-day composites from Earth Engine...")
    stats = collect_zone_stats(indices, zones_fc, windows, scale=10)
    df = pd.DataFrame([f["properties"] for f in stats["features"]])
    for col in ("NDRE", "MSAVI", "valid"):
        if col not in df:
            df[col] = np.nan

    res, cur, base = analyse(df, args)
    write_outputs(res, zones_info, df, cur, args.out_dir)


if __name__ == "__main__":
    main()