# LP Strategy Types

The LP Backtester supports three distinct strategy types to simulate different liquidity provider behaviors. Parameters are always visible in the UI but will be disabled if they do not apply to the selected type.

## 1. None
**Description**: A static liquidity position with no rebalancing logic. The position remains at the initial price boundaries until the end of the simulation.

- **Trigger**: None.
- **Action**: None.
- **Input Parameters**:
    - **LP Range (Min/Max %)**: The price boundaries relative to the entry price.

---

## 2. Time-delayed Rebalancing
**Description**: Rebalances the position once the price has spent a consecutive number of days outside of the defined **Rebalance Range**.

- **Trigger**: Price stays below `Rebalance Min %` or above `Rebalance Max %` for `Delay` consecutive days.
- **Action**: The entire capital (principal + fees) is consolidated and redeployed centered on the **current market price**. The new LP and Rebalance boundaries are recalculated relative to this new center.
- **Input Parameters**:
    - **LP Range (Min/Max %)**: Boundaries for earning fees.
    - **Rebalance Range (Min/Max %)**: Boundaries that trigger a reset.
    - **Delay (Days)**: Consecutive days out-of-range required to trigger.

---

## 3. Settled Rebalancing
**Description**: A more conservative strategy that only rebalances once the market has "settled" into a new price range after a breach.

- **Trigger**: 
    1. The price must be outside the `Rebalance Range` for at least `Delay` consecutive days.
    2. During those `Delay` days, the price volatility must remain within the `Rebalance Range` width. Specifically, the high/low prices of the settlement period must be within the rebalance boundaries relative to the geometric average of prices during that period.
- **Action**: The position is redeployed centered on the **Geometric Average Price** of the settlement period. This avoids "chasing" the peak of a volatile move and targets a more stable entry point.
- **Input Parameters**:
    - **LP Range (Min/Max %)**: Boundaries for earning fees.
    - **Rebalance Range (Min/Max %)**: Defines the stability threshold for settlement.
    - **Delay (Days)**: The duration of the settlement period (must be > 1).

### Settlement Formula
The new center price $P_{new}$ is calculated as:
$$P_{new} = \exp\left(\frac{1}{N} \sum_{i=1}^N \ln(P_i)\right)$$
where $N$ is the Delay (Days).
