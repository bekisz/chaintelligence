import asyncio
from api.main import parse_fee_rate, format_apr

vol_val = 570732175
tvl_val = 884270
period_days = 13.0
fee_tier = "0.0007%"

fee_rate = parse_fee_rate(fee_tier)
print(f"Fee rate: {fee_rate}")
fees_earned = vol_val * fee_rate
apr_val = (fees_earned / tvl_val) * (365.0 / period_days)
print(f"APR val: {apr_val}")
print(f"Formatted APR: {format_apr(apr_val)}")
