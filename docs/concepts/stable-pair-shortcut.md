# Stable-pair Shortcuts

There are cases where there is a signification volume between two strongly correlated assets on a DEX with interim stage where the asset is a volitile asset.

As example let'se we want to see how dex users swap from USDC to DAI, and we may see that the all the swap goes like USDC-WETH-DAI. This is suboptimal from the liquidity provider's point of view, as they are exposed to the volatility of WETH and need cover lot more tick ranges to provide liquidity for the swap.

In such cases the obvious solution is to provide liquidity for the USDC/DAI pool directly. This is a stablecoin pool, so the liquidity providers are not exposed to the volatility of WETH and only need to cover a narrow range of ticks around the current price.
