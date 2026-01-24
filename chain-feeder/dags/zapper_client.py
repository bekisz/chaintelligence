import requests
import json
import logging
from zapper_config import ENDPOINT, AUTH_HEADER, TARGET_ADDRESS

logger = logging.getLogger(__name__)

QUERY = """
query PortfolioApps($addresses: [Address!]!) {
    portfolioV2(addresses: $addresses) {
        appBalances {
            byAddress: byAccount(first: 50) {
                edges {
                    node {
                        appGroupBalances {
                            edges {
                                node {
                                    app { slug displayName }
                                    network { name }
                                    balanceUSD
                                    positionBalances {
                                        edges {
                                            node {
                                                __typename
                                                ... on AppTokenPositionBalance {
                                                    displayProps { label images }
                                                    balanceUSD
                                                    tokens { symbol balance balanceUSD price }
                                                }
                                                ... on ContractPositionBalance {
                                                    balanceUSD
                                                    displayProps { label images }
                                                    tokens {
                                                        metaType
                                                        token { symbol balance balanceUSD price decimals }
                                                    }
                                                }
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}
"""

def fetch_zapper_data():
    """Calculates and returns a list of position dictionaries."""
    variables = {"addresses": [TARGET_ADDRESS]}
    headers = {
        "Content-Type": "application/json",
        "Authorization": AUTH_HEADER,
        "Accept": "application/json",
        "Accept-Encoding": "deflate, gzip"
    }

    try:
        response = requests.post(ENDPOINT, json={"query": QUERY, "variables": variables}, headers=headers)
        response.raise_for_status()
        data = response.json()
        
        if "errors" in data:
            logger.error(f"GraphQL Errors: {data['errors']}")
            return []

        accounts = data.get("data", {}).get("portfolioV2", {}).get("appBalances", {}).get("byAddress", {}).get("edges", [])
        if not accounts:
            logger.info("No application data found.")
            return []

        extracted_positions = []

        for acc in accounts:
            app_groups = acc["node"]["appGroupBalances"]["edges"]
            for group in app_groups:
                node = group["node"]
                app_name = node["app"]["displayName"]
                network = node["network"]["name"]
                
                # Iterate positions
                positions = node.get("positionBalances", {}).get("edges", [])
                for pos in positions:
                    p_node = pos["node"]
                    label = p_node.get("displayProps", {}).get("label") or "Position"
                    balance_usd = p_node.get("balanceUSD", 0)
                    type_name = p_node.get("__typename")

                    # Structure for DB
                    position_record = {
                        "address": TARGET_ADDRESS,
                        "protocol": app_name,
                        "network": network,
                        "position_label": label,
                        "balance_usd": balance_usd,
                        "assets": [],
                        "unclaimed": []
                    }

                    # Parse tokens
                    if type_name == "ContractPositionBalance" and p_node.get("tokens"):
                        for wrapper in p_node["tokens"]:
                            t = wrapper.get("token")
                            if not t: continue
                            
                            token_data = {
                                "symbol": t.get("symbol"),
                                "balance": t.get("balance"),
                                "balanceUSD": t.get("balanceUSD"),
                                "price": t.get("price")
                            }
                            
                            meta_type = (wrapper.get("metaType") or "supplied").lower()
                            if meta_type in ["claimable", "reward"]:
                                position_record["unclaimed"].append(token_data)
                            else:
                                position_record["assets"].append(token_data)

                    elif type_name == "AppTokenPositionBalance" and p_node.get("tokens"):
                        for t in p_node["tokens"]:
                            token_data = {
                                "symbol": t.get("symbol"),
                                "balance": t.get("balance"),
                                "balanceUSD": t.get("balanceUSD"),
                                "price": t.get("price")
                            }
                            position_record["assets"].append(token_data)
                    
                    extracted_positions.append(position_record)

        return extracted_positions

    except Exception as e:
        logger.exception(f"Request failed: {e}")
        raise

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    results = fetch_zapper_data()
    print(json.dumps(results, indent=2))
