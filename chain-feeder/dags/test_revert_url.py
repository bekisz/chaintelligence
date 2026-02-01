import requests
import logging

URLS = [
    "https://api.thegraph.com/subgraphs/name/revert-finance/uniswap-v4-mainnet",
    "https://api.thegraph.com/subgraphs/name/revert-finance/uniswap-v4-ethereum",
    "https://api.thegraph.com/subgraphs/name/revert-finance/v4-mainnet",
    "https://api.thegraph.com/subgraphs/name/uniswap/uniswap-v4",
    # Try decentralized gateway with ID of Revert? (Don't know ID).
]

def test():
    for url in URLS:
        print(f"Testing {url} ...")
        try:
            resp = requests.post(url, json={"query": "{ positions(first: 1) { id } }"}, timeout=5)
            print(f"Status: {resp.status_code}")
            if resp.status_code == 200:
                print("Response:", resp.text[:200])
                if "errors" not in resp.text:
                    print("SUCCESS! This URL works.")
                    # Try introspection of Position fields here
                    introspection = requests.post(url, json={"query": "{ Position: __type(name: \"Position\") { fields { name } } }"})
                    print("Fields:", introspection.json())
                    break
            else:
                print("Failed.")
        except Exception as e:
            print(f"Error: {e}")

if __name__ == "__main__":
    test()
