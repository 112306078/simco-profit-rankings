#!/usr/bin/env python3
import csv
import json
import math
import subprocess
from collections import defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BASE = "https://www.simcompanies.com"
SIMCOTOOLS = "https://api.simcotools.com"
REALM = 0
MIN_POINTS = 2


def get_text(url):
    result = subprocess.run(
        ["curl", "-fsSL", "--max-time", "30", "-A", "Codex analysis", url],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


def get_json(url):
    return json.loads(get_text(url))


def title_name(enum_name):
    special = {
        "BFR": "BFR",
        "JUMBO_JET": "Jumbo Jet",
        "SUB_ORBITAL_ROCKET": "Sub-Orbital Rocket",
        "XMAS_CRACKERS": "Xmas Crackers",
        "XMAS_ORNAMENT": "Xmas Ornament",
        "ON_BOARD_COMPUTER": "On-Board Computer",
        "HIGH_GRADE_E_COMP": "High Grade E-Components",
    }
    if enum_name in special:
        return special[enum_name]
    return enum_name.replace("_", " ").title()


def load_names():
    names = {}
    try:
        data = get_json(f"{SIMCOTOOLS}/v1/realms/{REALM}/resources?disable_pagination=true")
        for resource in data.get("resources", []):
            rid = int(resource["id"])
            names[rid] = resource["name"]
            for in_id, inp in resource.get("inputs", {}).items():
                names[int(in_id)] = inp.get("name", names.get(int(in_id), title_name(str(in_id))))
    except Exception:
        pass

    if names:
        return names

    resources = get_json(f"{BASE}/api/v2/constants/resources/")
    for rid_s, resource in resources.items():
        rid = int(rid_s)
        enum_name = resource.get("db_letter") or resource.get("name") or str(rid)
        names[rid] = title_name(str(enum_name))
    return names


def linear_regression(points):
    xs = [float(q) for q, _ in points]
    ys = [math.log(float(price)) for _, price in points]
    n = len(points)
    x_mean = sum(xs) / n
    y_mean = sum(ys) / n
    ss_xx = sum((x - x_mean) ** 2 for x in xs)
    if ss_xx == 0:
        return None
    slope = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, ys)) / ss_xx
    intercept = y_mean - slope * x_mean
    fitted = [intercept + slope * x for x in xs]
    ss_res = sum((y - y_hat) ** 2 for y, y_hat in zip(ys, fitted))
    ss_tot = sum((y - y_mean) ** 2 for y in ys)
    r_squared = 1.0 if ss_tot == 0 else 1.0 - ss_res / ss_tot
    return slope, intercept, r_squared


def main():
    print("Loading names...", flush=True)
    names = load_names()
    print("Loading quality market prices...", flush=True)
    price_data = get_json(f"{SIMCOTOOLS}/v1/realms/{REALM}/market/prices")
    prices = price_data.get("prices", price_data)

    by_resource = defaultdict(dict)
    timestamps = defaultdict(dict)
    for item in prices:
        rid = int(item["resourceId"])
        quality = int(item["quality"])
        price = float(item["price"])
        if price <= 0:
            continue
        by_resource[rid][quality] = price
        timestamps[rid][quality] = item.get("datetime", "")

    rows = []
    skipped = []
    for rid in sorted(by_resource):
        q_prices = sorted(by_resource[rid].items())
        if len(q_prices) < MIN_POINTS:
            skipped.append({"resource_id": rid, "reason": f"only {len(q_prices)} quality price point(s)"})
            continue
        reg = linear_regression(q_prices)
        if reg is None:
            skipped.append({"resource_id": rid, "reason": "quality values have zero variance"})
            continue
        slope, intercept, r_squared = reg
        percent_per_quality = (math.exp(slope) - 1.0) * 100.0
        q_min = q_prices[0][0]
        q_max = q_prices[-1][0]
        p_min = q_prices[0][1]
        p_max = q_prices[-1][1]
        rows.append(
            {
                "resource_id": rid,
                "resource": names.get(rid, f"Resource {rid}"),
                "quality_points": len(q_prices),
                "quality_min": q_min,
                "quality_max": q_max,
                "price_at_quality_min": p_min,
                "price_at_quality_max": p_max,
                "log_price_slope_per_quality": slope,
                "approx_percent_increase_per_quality": percent_per_quality,
                "log_price_intercept": intercept,
                "r_squared": r_squared,
                "qualities_used": "|".join(str(q) for q, _ in q_prices),
                "prices_used": "|".join(str(price) for _, price in q_prices),
                "timestamps_used": "|".join(timestamps[rid].get(q, "") for q, _ in q_prices),
            }
        )

    rows.sort(key=lambda row: row["log_price_slope_per_quality"], reverse=True)
    out_dir = PROJECT_ROOT / "outputs"
    out_dir.mkdir(exist_ok=True)
    csv_path = out_dir / "simco_quality_price_slope_rank.csv"
    fields = list(rows[0].keys()) if rows else []
    with csv_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)

    summary = {
        "realm": REALM,
        "method": "ordinary least squares regression of ln(exchange_price) on quality",
        "resources_ranked": len(rows),
        "resources_skipped": skipped,
        "top_20_by_log_price_slope": rows[:20],
    }
    summary_path = out_dir / "simco_quality_price_slope_summary.json"
    with summary_path.open("w") as f:
        json.dump(summary, f, indent=2)

    print(f"Wrote {csv_path}")
    print(f"Wrote {summary_path}")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
