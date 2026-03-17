import requests
import os
import json

RPC_URL = "https://arb1.arbitrum.io/rpc"
POSITION_MANAGER = "0xbd216513d74c8cf14cf4747e6aaa6420ff64ee9e"
SEL_OWNER_OF = "0x6352211e"
TOKEN_ID = 111885

def call_rpc(to, data):
    payload = {"jsonrpc":"2.0", "method":"eth_call", "params":[{"to":to, "data":data}, "latest"], "id":1}
    resp = requests.post(RPC_URL, json=payload)
    return resp.json().get('result')

def check_owner():
    calldata = SEL_OWNER_OF + format(TOKEN_ID, '064x')
    print(f"Checking ownerOf({TOKEN_ID}) on {POSITION_MANAGER}")
    res = call_rpc(POSITION_MANAGER, calldata)
    print(f"Result: {res}")
    
    if res and res != "0x":
        print(f"Owner: 0x{res[26:]}")
        
    # Also try `balanceOf` just in case
    # SEL_BALANCE_OF = "0x70a08231"
    # calldata = SEL_BALANCE_OF + format(int("0xe34eb31bfd2afea4320b1ce0d1b8ae943afac425", 16), '064x')
    # res = call_rpc(POSITION_MANAGER, calldata)
    # print(f"BalanceOf user: {res}")

if __name__ == "__main__":
    check_owner()
