def parse_fee_rate(fee_str: str):
    try:
        f_clean = fee_str.split('|')[0].replace('%', '').strip()
        if f_clean == 'Dynamic':
            return 0.0002
        val = float(f_clean)
        if val >= 5:
            return val / 1000000.0
        return val / 100.0
    except Exception:
        return None

def calc():
    tvl_val = 884270
    vol_val = 570000000
    period_days = 30.0
    fee_tier = '0.0007%|Uniswap V4|ethereum'
    
    fee_rate = parse_fee_rate(fee_tier)
    fees_earned = vol_val * fee_rate
    apr_val = (fees_earned / tvl_val) * (365.0 / period_days)
    print(f"fee_rate: {fee_rate}")
    print(f"fees_earned: {fees_earned}")
    print(f"apr_val: {apr_val}")

calc()
