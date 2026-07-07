# Test link generation for Arbitrum pools

def generate_links(network, pool_addr):
    network_lower = network.lower()
    # Uniswap mapping
    if 'eth' in network_lower:
        uni_network = 'ethereum'
    elif 'bnb' in network_lower:
        uni_network = 'bsc'
    elif 'arbitrum' in network_lower:
        uni_network = 'arbitrum'
    elif 'optimism' in network_lower:
        uni_network = 'optimism'
    elif 'polygon' in network_lower:
        uni_network = 'polygon'
    elif 'base' in network_lower:
        uni_network = 'base'
    else:
        uni_network = 'ethereum'
    uniswap_link = f"https://app.uniswap.org/explore/pools/{uni_network}/{pool_addr}"

    # PancakeSwap mapping
    if 'eth' in network_lower:
        p_chain = 'eth'
    elif 'arbitrum' in network_lower:
        p_chain = 'arbitrum'
    elif 'base' in network_lower:
        p_chain = 'base'
    else:
        p_chain = 'bsc'
    pancake_link = f"https://pancakeswap.finance/info/v3/pairs/{pool_addr}?chain={p_chain}"
    return uniswap_link, pancake_link

addr = "0x67D3E181E6dcC47f977c3A4b33Ac65454b87b997"
uni, pan = generate_links('Arbitrum', addr)
print('Uniswap link:', uni)
print('Pancake link:', pan)
