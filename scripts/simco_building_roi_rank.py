#!/usr/bin/env python3
import csv
import json
import math
import subprocess
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BASE = "https://www.simcompanies.com"
SIMCOTOOLS = "https://api.simcotools.com"
REALM = 0
QUALITY = 0
TRANSPORT_ID = 13
CONSTRUCTION_UNITS_ID = 111
ADMIN_OVERHEAD_PERCENT = 20.0
EXCHANGE_FEE_PERCENT = 4.0
CONTRACT_INPUT_DISCOUNT_PERCENT = 3.0
ROBOT_WORKFORCE_DISCOUNT_PERCENT = 3.0
SPEED_BONUS_PERCENT = 12.0
SPEED_MULTIPLIER = 1.0 / (1.0 - SPEED_BONUS_PERCENT / 100.0)


def get_text(url):
    last_error = None
    for attempt in range(1, 7):
        try:
            result = subprocess.run(
                ["curl", "-fsSL", "--max-time", "45", "-A", "Codex analysis", url],
                check=True,
                capture_output=True,
                text=True,
            )
            return result.stdout
        except subprocess.CalledProcessError as exc:
            last_error = exc
            if attempt < 6:
                time.sleep(attempt * 2)
    raise last_error


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


def load_simcotools_resources():
    data = get_json(f"{SIMCOTOOLS}/v1/realms/{REALM}/resources?disable_pagination=true")
    return {int(r["id"]): r for r in data.get("resources", [])}


def load_names(simcotools_resources):
    names = {}
    for r in simcotools_resources.values():
        names[int(r["id"])] = r["name"]
        for in_id, inp in r.get("inputs", {}).items():
            names[int(in_id)] = inp.get("name", names.get(int(in_id), title_name(str(in_id))))
    return names


def load_building_names():
    data = get_json(f"{SIMCOTOOLS}/v1/realms/{REALM}/buildings?disable_pagination=true")
    return {str(b["id"]): b["name"] for b in data.get("buildings", [])}


def load_prices_by_quality():
    data = get_json(f"{SIMCOTOOLS}/v1/realms/{REALM}/market/prices")
    raw_prices = data.get("prices", data)
    prices = {}
    timestamps = {}
    for item in raw_prices:
        rid = int(item["resourceId"])
        q = int(item["quality"])
        prices[(rid, q)] = float(item["price"])
        timestamps[(rid, q)] = item.get("datetime", "")
    return prices, timestamps


def q_price(prices, rid, quality=QUALITY):
    return prices.get((int(rid), int(quality)))


def building_cost(building, construction_unit_price):
    return float(building.get("costUnits", 0.0)) * construction_unit_price


RETAIL_CURVE_BASE = 370.0
RETAIL_QUALITY_WEIGHT = 0.3
RETAIL_BUILDING_MULTIPLIERS = {"B": 2.28}


def retail_curve_price_at_modeled_margin(curve_base, modeled_cost, modeled_units, modeled_wages):
    return modeled_cost + (curve_base + modeled_wages) / modeled_units


def retail_curve_for_price(curve_base, modeled_curve_price, price, modeled_wages, modeled_cost):
    curve_scale = (modeled_wages + curve_base) / ((modeled_curve_price - modeled_cost) ** 2)
    return curve_base - ((price - modeled_curve_price) ** 2) * curve_scale


def retail_seconds_to_sell(
    building_id,
    retail_info,
    quantity,
    sales_speed_bonus_percent,
    price,
    quality,
    saturation,
    building_level=1.0,
    acceleration=1.0,
    weather_selling_speed_multiplier=None,
):
    demand_factor = min(max(2.0 - saturation, 0.0), 2.0)
    modeled_units_multiplier = max(0.9, demand_factor / 2.0 + 0.5)
    quality_factor = quality / 12.0
    modeled_units = float(retail_info.get("modeledUnitsSoldAnHour", 0.0)) * modeled_units_multiplier
    if modeled_units <= 0:
        return math.nan

    modeled_wages = float(retail_info.get("modeledStoreWages") or 0.0)
    modeled_cost = float(retail_info.get("modeledProductionCostPerUnit", 0.0))
    building_levels_needed = float(retail_info.get("buildingLevelsNeededPerUnitPerHour", 0.0))
    curve_base = (
        RETAIL_CURVE_BASE
        * (building_levels_needed * float(retail_info.get("modeledUnitsSoldAnHour", 0.0)) + 1.0)
        * RETAIL_BUILDING_MULTIPLIERS.get(str(building_id), 1.0)
        * (demand_factor / 2.0 * (1.0 + quality_factor * RETAIL_QUALITY_WEIGHT))
    )
    modeled_curve_price = retail_curve_price_at_modeled_margin(curve_base, modeled_cost, modeled_units, modeled_wages)
    curve_at_price = retail_curve_for_price(curve_base, modeled_curve_price, price, modeled_wages, modeled_cost)
    seconds = (quantity * ((price - modeled_cost) * 3600.0) - modeled_wages) / (curve_at_price + modeled_wages)
    if seconds <= 0:
        return math.nan
    seconds = seconds / acceleration / building_level
    seconds = seconds - seconds * sales_speed_bonus_percent / 100.0
    if weather_selling_speed_multiplier:
        seconds /= weather_selling_speed_multiplier
    return seconds


def retail_units_sold_an_hour(building_id, retail_info, price, quality, saturation, sales_speed_bonus_percent):
    seconds = retail_seconds_to_sell(
        building_id=building_id,
        retail_info=retail_info,
        quantity=100.0,
        sales_speed_bonus_percent=sales_speed_bonus_percent,
        price=price,
        quality=quality,
        saturation=saturation,
    )
    if not math.isfinite(seconds) or seconds <= 0:
        return 0.0
    return 100.0 * 3600.0 / seconds


SCENARIOS = {
    "exchange_with_freight": {
        "input_price_multiplier": 1.0,
        "sell_price_multiplier": 1.0 - EXCHANGE_FEE_PERCENT / 100.0,
        "include_freight": True,
        "exchange_fee_percent": EXCHANGE_FEE_PERCENT,
    },
    "contract_no_freight": {
        "input_price_multiplier": 1.0 - CONTRACT_INPUT_DISCOUNT_PERCENT / 100.0,
        "sell_price_multiplier": 1.0 - EXCHANGE_FEE_PERCENT / 100.0,
        "include_freight": True,
        "exchange_fee_percent": EXCHANGE_FEE_PERCENT,
    },
}


def calculate_production_candidates(
    resources, buildings, simcotools_resources, names, building_names, prices, timestamps, scenario_name, scenario
):
    transport_price = q_price(prices, TRANSPORT_ID)
    candidates = []
    skipped = []
    for rid, resource in sorted(resources.items()):
        if (
            not resource.get("isExchangeTradable")
            or resource.get("producedAt") is None
            or resource.get("producedPerHourRaw", 0) <= 0
        ):
            continue

        building_id = str(resource["producedAt"])
        building = buildings.get(building_id)
        sell_price = q_price(prices, rid)
        if not building or sell_price is None or transport_price is None:
            skipped.append({"resource_id": rid, "reason": "missing building, sell price, or transport price"})
            continue

        material_unit_cost = 0.0
        missing_inputs = []
        for in_id_s, qty in resource.get("producedFrom", {}).items():
            input_price = q_price(prices, int(in_id_s))
            if input_price is None:
                missing_inputs.append(int(in_id_s))
                continue
            material_unit_cost += float(qty) * input_price * scenario["input_price_multiplier"]
        if missing_inputs:
            skipped.append({"resource_id": rid, "reason": f"missing input prices {missing_inputs}"})
            continue

        simcotools_resource = simcotools_resources.get(rid, {})
        hourly_output_base = float(simcotools_resource.get("producedAnHour", resource["producedPerHourRaw"]))
        hourly_output_with_bonus = hourly_output_base * SPEED_MULTIPLIER
        worker_hourly_cost_base = float(simcotools_resource.get("wages", 0.0))
        building_category = "research" if resource.get("isResearch") else "production"
        robot_discount_multiplier = (
            1.0 - ROBOT_WORKFORCE_DISCOUNT_PERCENT / 100.0 if building_category == "production" else 1.0
        )
        worker_hourly_cost = worker_hourly_cost_base * robot_discount_multiplier
        admin_hourly_cost = worker_hourly_cost * ADMIN_OVERHEAD_PERCENT / 100.0
        freight_unit_cost = float(resource.get("transportation", 0.0)) * transport_price if scenario["include_freight"] else 0.0
        net_sell_price = sell_price * scenario["sell_price_multiplier"]
        hourly_revenue = net_sell_price * hourly_output_with_bonus
        hourly_material_cost = material_unit_cost * hourly_output_with_bonus
        hourly_freight_cost = freight_unit_cost * hourly_output_with_bonus
        hourly_profit = hourly_revenue - hourly_material_cost - hourly_freight_cost - worker_hourly_cost - admin_hourly_cost

        production_season = resource.get("productionSeason")
        retail_season = resource.get("retailSeason")
        candidates.append(
            {
                "building_id": building_id,
                "building": building_names.get(building_id, building_id),
                "building_category": building_category,
                "resource_id": rid,
                "resource": names.get(rid, f"Resource {rid}"),
                "quality": QUALITY,
                "scenario": scenario_name,
                "mode": f"{building_category}_{scenario_name}",
                "hourly_profit": hourly_profit,
                "hourly_revenue": hourly_revenue,
                "hourly_output_or_sales_with_bonus": hourly_output_with_bonus,
                "hourly_output_or_sales_base": hourly_output_base,
                "sell_price": sell_price,
                "net_sell_price_after_fee": net_sell_price,
                "source_or_material_unit_cost": material_unit_cost,
                "freight_unit_cost": freight_unit_cost,
                "worker_or_sales_wages_hourly": worker_hourly_cost,
                "worker_or_sales_wages_hourly_before_robot_discount": worker_hourly_cost_base,
                "robot_workforce_discount_percent": ROBOT_WORKFORCE_DISCOUNT_PERCENT
                if building_category == "production"
                else 0.0,
                "admin_hourly_cost": admin_hourly_cost,
                "is_seasonal_product": production_season is not None or retail_season is not None,
                "production_season": production_season or "",
                "retail_season": retail_season or "",
                "price_timestamp": timestamps.get((rid, QUALITY), ""),
            }
        )
    return candidates, skipped


def retail_resources_for_building(building_id):
    data = get_json(f"{SIMCOTOOLS}/v1/realms/{REALM}/resources?sold_at={building_id}&disable_pagination=true")
    return data.get("resources", [])


def calculate_retail_candidates(resources_constants, buildings, building_names, prices, timestamps, scenario_name, scenario):
    candidates = []
    skipped = []
    retail_building_ids = [
        str(bid)
        for bid, building in buildings.items()
        if building.get("category") in {"sales", "seasonal"}
    ]
    for building_id in sorted(retail_building_ids, key=lambda x: building_names.get(x, x)):
        try:
            resources = retail_resources_for_building(building_id)
        except Exception as exc:
            skipped.append({"building_id": building_id, "reason": f"failed loading retail resources: {exc}"})
            continue
        for resource in resources:
            rid = int(resource["id"])
            infos = resource.get("retailInfo", [])
            if not infos:
                skipped.append({"building_id": building_id, "resource_id": rid, "reason": "missing retailInfo"})
                continue
            for info in infos:
                quality = int(info.get("quality", QUALITY))
                source_price = q_price(prices, rid, quality)
                if source_price is None:
                    source_price = q_price(prices, rid, QUALITY)
                sell_price = info.get("averagePrice")
                if sell_price is None:
                    sell_price = q_price(prices, rid, quality) or q_price(prices, rid, QUALITY)
                if source_price is None or sell_price is None:
                    skipped.append(
                        {
                            "building_id": building_id,
                            "resource_id": rid,
                            "quality": quality,
                            "reason": "missing source price or retail sell price",
                        }
                    )
                    continue
                sell_price = float(sell_price)
                source_price = float(source_price) * scenario["input_price_multiplier"]
                saturation = float(info.get("saturation", 1.0))
                sales_base = retail_units_sold_an_hour(
                    building_id,
                    info,
                    sell_price,
                    quality,
                    saturation,
                    0.0,
                )
                sales_with_bonus = retail_units_sold_an_hour(
                    building_id,
                    info,
                    sell_price,
                    quality,
                    saturation,
                    SPEED_BONUS_PERCENT,
                )
                sales_wages = float(info.get("salesWages", 0.0))
                admin_hourly_cost = sales_wages * ADMIN_OVERHEAD_PERCENT / 100.0
                hourly_revenue = sell_price * sales_with_bonus
                hourly_source_cost = source_price * sales_with_bonus
                hourly_profit = hourly_revenue - hourly_source_cost - sales_wages - admin_hourly_cost
                resource_constants = resources_constants.get(rid, {})
                production_season = resource_constants.get("productionSeason")
                retail_season = resource_constants.get("retailSeason")
                candidates.append(
                    {
                        "building_id": building_id,
                        "building": building_names.get(building_id, building_id),
                        "building_category": "retail",
                        "resource_id": rid,
                        "resource": resource.get("name", f"Resource {rid}"),
                        "quality": quality,
                        "scenario": scenario_name,
                        "mode": f"retail_average_price_{scenario_name}",
                        "hourly_profit": hourly_profit,
                        "hourly_revenue": hourly_revenue,
                        "hourly_output_or_sales_with_bonus": sales_with_bonus,
                        "hourly_output_or_sales_base": sales_base,
                        "sell_price": sell_price,
                        "net_sell_price_after_fee": sell_price,
                        "source_or_material_unit_cost": source_price,
                        "freight_unit_cost": 0.0,
                        "worker_or_sales_wages_hourly": sales_wages,
                        "worker_or_sales_wages_hourly_before_robot_discount": sales_wages,
                        "robot_workforce_discount_percent": 0.0,
                        "admin_hourly_cost": admin_hourly_cost,
                        "is_seasonal_product": production_season is not None or retail_season is not None,
                        "production_season": production_season or "",
                        "retail_season": retail_season or "",
                        "price_timestamp": timestamps.get((rid, quality), timestamps.get((rid, QUALITY), "")),
                    }
                )
    return candidates, skipped


def rank_best_by_building(candidates):
    sorted_candidates = sorted(candidates, key=lambda r: r["hourly_profit"], reverse=True)
    best_by_building = {}
    for row in sorted_candidates:
        best_by_building.setdefault(row["building_id"], dict(row))
    building_rows = sorted(best_by_building.values(), key=lambda r: r["hourly_profit"], reverse=True)
    for idx, row in enumerate(building_rows, 1):
        row["rank"] = idx
    return building_rows, sorted_candidates


def write_rows(path, rows, rank_field=False):
    if not rows:
        return
    fields = ["rank"] + [k for k in rows[0].keys() if k != "rank"] if rank_field else list(rows[0].keys())
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main():
    print("Loading constants and Simco Tools data...", flush=True)
    resources = {int(k): v for k, v in get_json(f"{BASE}/api/v2/constants/resources/").items()}
    buildings = {str(k): v for k, v in get_json(f"{BASE}/api/v2/constants/buildings/").items()}
    simcotools_resources = load_simcotools_resources()
    names = load_names(simcotools_resources)
    building_names = load_building_names()
    prices, timestamps = load_prices_by_quality()

    construction_unit_price = q_price(prices, CONSTRUCTION_UNITS_ID)
    if construction_unit_price is None:
        raise RuntimeError("Missing Q0 Construction units price.")

    out_dir = PROJECT_ROOT / "outputs"
    out_dir.mkdir(exist_ok=True)

    summaries = {}
    for scenario_name, scenario in SCENARIOS.items():
        print(f"Calculating {scenario_name} production/research candidates...", flush=True)
        production_candidates, production_skipped = calculate_production_candidates(
            resources, buildings, simcotools_resources, names, building_names, prices, timestamps, scenario_name, scenario
        )
        print(f"Calculating {scenario_name} retail candidates...", flush=True)
        retail_candidates, retail_skipped = calculate_retail_candidates(
            resources, buildings, building_names, prices, timestamps, scenario_name, scenario
        )

        candidates = production_candidates + retail_candidates
        for row in candidates:
            b = buildings[str(row["building_id"])]
            cost = building_cost(b, construction_unit_price)
            row["building_cost_units"] = float(b.get("costUnits", 0.0))
            row["construction_units_q0_price"] = construction_unit_price
            row["building_cost_q0_construction_units"] = cost
            row["hourly_roi"] = row["hourly_profit"] / cost if cost else 0.0
            row["daily_roi_percent"] = row["hourly_roi"] * 24.0 * 100.0
            row["admin_overhead_percent"] = ADMIN_OVERHEAD_PERCENT
            row["speed_bonus_percent"] = SPEED_BONUS_PERCENT
            row["speed_multiplier"] = SPEED_MULTIPLIER
            row["exchange_fee_percent"] = scenario["exchange_fee_percent"] if row["building_category"] != "retail" else 0.0
            row["contract_input_discount_percent"] = (
                CONTRACT_INPUT_DISCOUNT_PERCENT if scenario_name == "contract_no_freight" else 0.0
            )

        building_rows, candidates = rank_best_by_building(candidates)
        no_seasonal_candidates = [
            row
            for row in candidates
            if not row["is_seasonal_product"] and buildings[str(row["building_id"])].get("category") != "seasonal"
        ]
        no_seasonal_building_rows, no_seasonal_candidates = rank_best_by_building(no_seasonal_candidates)

        building_csv = out_dir / f"simco_building_hourly_profit_rank_{scenario_name}.csv"
        candidates_csv = out_dir / f"simco_building_hourly_profit_candidates_{scenario_name}.csv"
        no_seasonal_building_csv = out_dir / f"simco_building_hourly_profit_rank_{scenario_name}_no_seasonal.csv"
        no_seasonal_candidates_csv = out_dir / f"simco_building_hourly_profit_candidates_{scenario_name}_no_seasonal.csv"
        write_rows(building_csv, building_rows, rank_field=True)
        write_rows(candidates_csv, candidates)
        write_rows(no_seasonal_building_csv, no_seasonal_building_rows, rank_field=True)
        write_rows(no_seasonal_candidates_csv, no_seasonal_candidates)

        summaries[scenario_name] = {
            "ranking_metric": "hourly_profit",
            "building_rank_csv": str(building_csv),
            "candidate_csv": str(candidates_csv),
            "no_seasonal_building_rank_csv": str(no_seasonal_building_csv),
            "no_seasonal_candidate_csv": str(no_seasonal_candidates_csv),
            "buildings_ranked": len(building_rows),
            "no_seasonal_buildings_ranked": len(no_seasonal_building_rows),
            "production_and_research_candidates": len(production_candidates),
            "retail_candidates": len(retail_candidates),
            "no_seasonal_candidates": len(no_seasonal_candidates),
            "production_skipped": production_skipped,
            "retail_skipped": retail_skipped,
            "top_20_buildings": building_rows[:20],
            "top_20_buildings_no_seasonal": no_seasonal_building_rows[:20],
        }
        print(f"Wrote {building_csv}")
        print(f"Wrote {candidates_csv}")
        print(f"Wrote {no_seasonal_building_csv}")
        print(f"Wrote {no_seasonal_candidates_csv}")

    summary = {
        "realm": REALM,
        "quality": QUALITY,
        "admin_overhead_percent": ADMIN_OVERHEAD_PERCENT,
        "robot_workforce_discount_percent_for_production_buildings": ROBOT_WORKFORCE_DISCOUNT_PERCENT,
        "contract_input_discount_percent": CONTRACT_INPUT_DISCOUNT_PERCENT,
        "speed_bonus_percent": SPEED_BONUS_PERCENT,
        "speed_multiplier": SPEED_MULTIPLIER,
        "construction_units_q0_price": construction_unit_price,
        "ranking_metric": "hourly_profit",
        "building_cost_model": "building costUnits * Construction units Q0 exchange price; included for reference only, not used for ranking",
        "exchange_with_freight_model": "production/research sells Q0 on exchange with 4% fee and full freight; materials at exchange; retail uses Simco Tools average retail price with exchange sourcing",
        "contract_no_freight_model": "legacy filename; production/research sells Q0 on exchange with 4% fee and full freight; materials at 3% contract discount; retail uses average retail price with 3% discounted sourcing",
        "scenario_summaries": summaries,
    }
    summary_path = out_dir / "simco_building_hourly_profit_summary.json"
    with summary_path.open("w") as f:
        json.dump(summary, f, indent=2)

    print(f"Wrote {summary_path}")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
