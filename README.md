# Simco Profit Rankings

Public CSV exports for Sim Companies realm 0 profitability analysis.

## Files

- `public-view/simco_profit_rank_exchange_with_freight.csv`: product hourly profit, seasonal included, exchange sale with freight.
- `public-view/simco_profit_rank_exchange_with_freight_no_seasonal.csv`: product hourly profit, seasonal excluded, exchange sale with freight.
- `public-view/simco_profit_rank_contract_no_freight.csv`: product hourly profit, seasonal included, legacy filename; current model uses contract-priced inputs and exchange sale.
- `public-view/simco_profit_rank_contract_no_freight_no_seasonal.csv`: product hourly profit, seasonal excluded, legacy filename; current model uses contract-priced inputs and exchange sale.
- `public-view/simco_building_hourly_profit_rank_exchange_with_freight.csv`: building hourly profit, seasonal included, exchange model.
- `public-view/simco_building_hourly_profit_rank_exchange_with_freight_no_seasonal.csv`: building hourly profit, seasonal excluded, exchange model.
- `public-view/simco_building_hourly_profit_rank_contract_no_freight.csv`: building hourly profit, seasonal included, legacy filename; current model uses contract-priced inputs and exchange sale for production/research.
- `public-view/simco_building_hourly_profit_rank_contract_no_freight_no_seasonal.csv`: building hourly profit, seasonal excluded, legacy filename; current model uses contract-priced inputs and exchange sale for production/research.

The Python scripts used to regenerate the exports are in `scripts/`.

## Current Assumptions

- Realm: 0
- Building level: 1
- Quality: 0
- Admin overhead: 20%
- Exchange fee: 4%
- Contract input discount: 3%
- Building speed bonus: 12%
- Production building robot workforce discount: 3%

