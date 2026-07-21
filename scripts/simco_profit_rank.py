#!/usr/bin/env python3
import csv
import json
import math
import re
import socket
import subprocess
import time
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BASE = "https://www.simcompanies.com"
SIMCOTOOLS = "https://api.simcotools.com"
REALM = 0
QUALITY = 0
TRANSPORT_ID = 13
ADMIN_OVERHEAD_PERCENT = 20.0
EXCHANGE_FEE_PERCENT = 4.0
CONTRACT_INPUT_DISCOUNT_PERCENT = 3.0
socket.setdefaulttimeout(30)


def get_json(url):
    return json.loads(get_text(url))


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
    resources = data.get("resources", [])
    if not resources:
        raise RuntimeError("Simco Tools resources API returned no resources.")
    return {int(r["id"]): r for r in resources}


def load_names(simcotools_resources):
    try:
        resources = list(simcotools_resources.values())
        names = {int(r["id"]): r["name"] for r in resources}
        for r in resources:
            for in_id, inp in r.get("inputs", {}).items():
                names[int(in_id)] = inp.get("name", names.get(int(in_id), title_name(str(in_id))))
        if names:
            return names
    except Exception:
        pass

    bundle = Path("work_bundle.js")
    if bundle.exists():
        text = bundle.read_text(errors="replace")
    else:
        html = get_text(f"{BASE}/encyclopedia/resources/")
        m = re.search(r'src="([^"]*index-[^"]+\.js)"', html)
        if not m:
            return {}
        url = m.group(1)
        if url.startswith("/"):
            url = BASE + url
        text = get_text(url)
    names = {}
    for name, rid in re.findall(r"t\[t\.([A-Z0-9_]+)=(\d+)\]=", text):
        names[int(rid)] = title_name(name)
    return names


def q0_lowest_prices(resource_ids):
    data = get_json(f"{SIMCOTOOLS}/v1/realms/{REALM}/market/prices")
    raw_prices = data.get("prices", data)
    prices = {}
    timestamps = {}

    # Simco Tools currently returns a nested resource/quality price map.
    # Keep the parser permissive because its public API is still evolving.
    if isinstance(raw_prices, dict):
        for rid_s, by_quality in raw_prices.items():
            try:
                rid = int(rid_s)
            except (TypeError, ValueError):
                continue
            q = None
            if isinstance(by_quality, dict):
                q = by_quality.get(str(QUALITY)) or by_quality.get(QUALITY)
            elif isinstance(by_quality, list):
                q = next((x for x in by_quality if int(x.get("quality", -1)) == QUALITY), None)
            if isinstance(q, dict):
                price = q.get("price") or q.get("currentPrice") or q.get("lastPrice")
                timestamp = q.get("datetime") or q.get("date") or q.get("updatedAt") or q.get("lastUpdated")
            else:
                price = q
                timestamp = ""
            if price is not None:
                prices[rid] = float(price)
                timestamps[rid] = timestamp or ""
    elif isinstance(raw_prices, list):
        for item in raw_prices:
            try:
                rid = int(item.get("resourceId", item.get("resource", item.get("kind"))))
                quality = int(item.get("quality", QUALITY))
            except (TypeError, ValueError):
                continue
            if quality != QUALITY:
                continue
            price = item.get("price") or item.get("currentPrice") or item.get("lastPrice")
            if price is not None:
                prices[rid] = float(price)
                timestamps[rid] = item.get("datetime") or item.get("date") or item.get("updatedAt") or item.get("lastUpdated") or ""

    missing = sorted(set(resource_ids) - set(prices))
    if not missing:
        return prices, timestamps

    print(f"Simco Tools batch prices missing {len(missing)} ids; falling back for missing ids only.")
    fallback_prices = {}
    fallback_timestamps = {}
    for idx, rid in enumerate(missing, 1):
        orders = get_json(f"{BASE}/api/v3/market/{REALM}/{rid}/")
        q0_orders = [o for o in orders if int(o.get("quality", -1)) == QUALITY]
        if q0_orders:
            best = min(q0_orders, key=lambda o: float(o["price"]))
            fallback_prices[rid] = float(best["price"])
            fallback_timestamps[rid] = best.get("posted") or best.get("datetimeDecayUpdated")
        # Keep this polite: endpoint is public, but avoid a tight loop.
        if idx % 10 == 0:
            time.sleep(0.5)
        else:
            time.sleep(0.1)
    prices.update(fallback_prices)
    timestamps.update(fallback_timestamps)
    return prices, timestamps


def main():
    print("Loading Sim Companies constants...", flush=True)
    resources = {int(k): v for k, v in get_json(f"{BASE}/api/v2/constants/resources/").items()}
    buildings = get_json(f"{BASE}/api/v2/constants/buildings/")
    core = get_json(f"{BASE}/api/v2/constants/core/")
    print("Loading Simco Tools adjusted production data...", flush=True)
    simcotools_resources = load_simcotools_resources()
    names = load_names(simcotools_resources)

    avg_salary = float(core["AVERAGE_SALARY"])
    salary_mid = float(core["SALARY_MID"][str(REALM)])

    production_ids = {
        rid
        for rid, r in resources.items()
        if r.get("isExchangeTradable")
        and r.get("producedAt") is not None
        and r.get("producedPerHourRaw", 0) > 0
        and not r.get("isResearch")
    }
    input_ids = set(production_ids)
    for rid in production_ids:
        input_ids.update(int(k) for k in resources[rid].get("producedFrom", {}).keys())
    input_ids.add(TRANSPORT_ID)

    print("Loading Q0 market prices...", flush=True)
    prices, timestamps = q0_lowest_prices(input_ids)
    transport_price = prices.get(TRANSPORT_ID)
    if transport_price is None:
        raise RuntimeError("No Q0 transportation exchange price found.")

    print("Calculating profit rankings...", flush=True)
    rows = []
    skipped = []
    for rid in sorted(production_ids):
        r = resources[rid]
        building_code = r["producedAt"]
        b = buildings.get(str(building_code)) or buildings.get(building_code)
        if not b:
            skipped.append((rid, "missing building"))
            continue

        sell_price = prices.get(rid)
        if sell_price is None:
            skipped.append((rid, "missing Q0 sell price"))
            continue

        missing_inputs = []
        material_unit_cost_exchange_inputs = 0.0
        material_unit_cost_contract_inputs = 0.0
        input_parts = []
        for in_id_s, qty in r.get("producedFrom", {}).items():
            in_id = int(in_id_s)
            p = prices.get(in_id)
            if p is None:
                missing_inputs.append(in_id)
                continue
            contract_input_price = p * (1.0 - CONTRACT_INPUT_DISCOUNT_PERCENT / 100.0)
            material_unit_cost_exchange_inputs += float(qty) * p
            material_unit_cost_contract_inputs += float(qty) * contract_input_price
            input_parts.append(
                f"{names.get(in_id, in_id)} x {qty} @ exchange {p:.4f} / contract {contract_input_price:.4f}"
            )
        if missing_inputs:
            skipped.append((rid, f"missing input prices {missing_inputs}"))
            continue

        salary_modifier = float(b["salaryModifier"])
        simcotools_resource = simcotools_resources.get(rid, {})
        hourly_output = float(
            simcotools_resource.get(
                "producedAnHour",
                float(r["producedPerHourRaw"]) * math.pow(avg_salary / salary_mid, salary_modifier),
            )
        )
        worker_hourly_cost = float(simcotools_resource.get("wages", avg_salary * salary_modifier))
        worker_unit_cost = worker_hourly_cost / hourly_output if hourly_output else 0.0

        # Public encyclopedia constants do not include a company-independent admin-overhead rate.
        # Keep it explicit in the output so the assumption is visible.
        admin_unit_cost = worker_unit_cost * ADMIN_OVERHEAD_PERCENT / 100.0

        freight_unit_cost_exchange = float(r.get("transportation", 0.0)) * transport_price
        production_season = r.get("productionSeason")
        retail_season = r.get("retailSeason")
        is_seasonal = production_season is not None or retail_season is not None
        exchange_net_unit_revenue = sell_price * (1.0 - EXCHANGE_FEE_PERCENT / 100.0)
        unit_cost_base_exchange_inputs = material_unit_cost_exchange_inputs + worker_unit_cost + admin_unit_cost
        unit_cost_base_contract_inputs = material_unit_cost_contract_inputs + worker_unit_cost + admin_unit_cost
        unit_cost_contract_inputs_exchange_sale = unit_cost_base_contract_inputs + freight_unit_cost_exchange
        unit_cost_exchange_with_freight = unit_cost_base_exchange_inputs + freight_unit_cost_exchange
        unit_profit_contract_inputs_exchange_sale = exchange_net_unit_revenue - unit_cost_contract_inputs_exchange_sale
        unit_profit_exchange_with_freight_after_fee = exchange_net_unit_revenue - unit_cost_exchange_with_freight

        rows.append(
            {
                "resource_id": rid,
                "resource": names.get(rid, f"Resource {rid}"),
                "building": building_code,
                "exchange_q0_price": sell_price,
                "price_timestamp": timestamps.get(rid, ""),
                "hourly_output_lvl1": hourly_output,
                "material_unit_cost": material_unit_cost_exchange_inputs,
                "material_unit_cost_exchange_inputs": material_unit_cost_exchange_inputs,
                "contract_input_discount_percent": CONTRACT_INPUT_DISCOUNT_PERCENT,
                "material_unit_cost_contract_inputs": material_unit_cost_contract_inputs,
                "worker_unit_cost": worker_unit_cost,
                "admin_overhead_percent_assumed": ADMIN_OVERHEAD_PERCENT,
                "admin_unit_cost_assumed": admin_unit_cost,
                "exchange_fee_percent": EXCHANGE_FEE_PERCENT,
                "exchange_net_unit_revenue_after_fee": exchange_net_unit_revenue,
                "transport_units": float(r.get("transportation", 0.0)),
                "transport_q0_price": transport_price,
                "freight_unit_cost_exchange_full": freight_unit_cost_exchange,
                "production_season": production_season or "",
                "retail_season": retail_season or "",
                "is_seasonal": is_seasonal,
                "unit_cost_base_excluding_freight": unit_cost_base_exchange_inputs,
                "unit_cost_base_exchange_inputs_excluding_freight": unit_cost_base_exchange_inputs,
                "unit_cost_base_contract_inputs_excluding_freight": unit_cost_base_contract_inputs,
                "unit_cost_contract_inputs_exchange_sale": unit_cost_contract_inputs_exchange_sale,
                "unit_cost_exchange_with_freight": unit_cost_exchange_with_freight,
                "unit_profit_contract_inputs_exchange_sale": unit_profit_contract_inputs_exchange_sale,
                "unit_profit_exchange_with_freight_after_fee": unit_profit_exchange_with_freight_after_fee,
                "hourly_profit_contract_inputs_exchange_sale": unit_profit_contract_inputs_exchange_sale * hourly_output,
                "hourly_profit_exchange_with_freight_after_fee": unit_profit_exchange_with_freight_after_fee
                * hourly_output,
                "unit_cost_contract_no_freight": unit_cost_contract_inputs_exchange_sale,
                "unit_profit_contract_no_freight": unit_profit_contract_inputs_exchange_sale,
                "hourly_profit_contract_no_freight": unit_profit_contract_inputs_exchange_sale * hourly_output,
                "unit_profit_exchange_with_freight": unit_profit_exchange_with_freight_after_fee,
                "hourly_profit_exchange_with_freight": unit_profit_exchange_with_freight_after_fee * hourly_output,
                "inputs": "; ".join(input_parts),
            }
        )

    out_dir = PROJECT_ROOT / "outputs"
    print(f"Writing CSV outputs to {out_dir}...", flush=True)
    out_dir.mkdir(exist_ok=True)
    fields = list(rows[0].keys()) if rows else []
    output_specs = [
        ("simco_profit_rank_contract_no_freight.csv", "hourly_profit_contract_inputs_exchange_sale", rows),
        ("simco_profit_rank_exchange_with_freight.csv", "hourly_profit_exchange_with_freight_after_fee", rows),
        (
            "simco_profit_rank_contract_no_freight_no_seasonal.csv",
            "hourly_profit_contract_inputs_exchange_sale",
            [row for row in rows if not row["is_seasonal"]],
        ),
        (
            "simco_profit_rank_exchange_with_freight_no_seasonal.csv",
            "hourly_profit_exchange_with_freight_after_fee",
            [row for row in rows if not row["is_seasonal"]],
        ),
    ]
    for filename, key, output_rows in output_specs:
        with (out_dir / filename).open("w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            for row in sorted(output_rows, key=lambda x: x[key], reverse=True):
                writer.writerow(row)

    non_seasonal_rows = [row for row in rows if not row["is_seasonal"]]
    summary = {
        "realm": REALM,
        "quality": QUALITY,
        "resources_ranked": len(rows),
        "non_seasonal_resources_ranked": len(non_seasonal_rows),
        "resources_skipped": skipped,
        "transport_q0_price": transport_price,
        "exchange_fee_percent": EXCHANGE_FEE_PERCENT,
        "contract_input_discount_percent": CONTRACT_INPUT_DISCOUNT_PERCENT,
        "top_contract_inputs_exchange_sale": sorted(
            rows, key=lambda x: x["hourly_profit_contract_inputs_exchange_sale"], reverse=True
        )[:20],
        "top_exchange_with_freight_after_fee": sorted(
            rows, key=lambda x: x["hourly_profit_exchange_with_freight_after_fee"], reverse=True
        )[:20],
        "top_contract_inputs_exchange_sale_no_seasonal": sorted(
            non_seasonal_rows, key=lambda x: x["hourly_profit_contract_inputs_exchange_sale"], reverse=True
        )[:20],
        "top_exchange_with_freight_after_fee_no_seasonal": sorted(
            non_seasonal_rows, key=lambda x: x["hourly_profit_exchange_with_freight_after_fee"], reverse=True
        )[:20],
    }
    with (out_dir / "simco_profit_rank_summary.json").open("w") as f:
        json.dump(summary, f, indent=2)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
