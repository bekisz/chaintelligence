
import requests
import json
import base64

# Configuration
API_URL = "http://localhost:8000"
USERNAME = "admin"
PASSWORD = "chaintelligence77"
POSITION_KEY = "0xc36442b4a4522e871399cd717abdd847ab11fe88:1180235"

def verify_api():
    url = f"{API_URL}/api/lp/history?position_key={POSITION_KEY}"
    print(f"Requesting: {url}")
    
    try:
        resp = requests.get(url, auth=(USERNAME, PASSWORD))
        
        if resp.status_code == 200:
            history = resp.json()
            print(json.dumps(history, indent=2))
            
            # Check for CREATE event
            create_event = next((e for e in history if e['event_type'] == 'create'), None)
            if create_event:
                print("\n[VERIFICATION]")
                print(f"Create Event Found: {create_event['timestamp']}")
                print(f"Amounts: {create_event['amount0']} / {create_event['amount1']}")
                
                if create_event['amount0'] > 0 or create_event['amount1'] > 0:
                    print("SUCCESS: Create event has non-zero amounts (Merged correctly).")
                else:
                    print("FAILURE: Create event has zero amounts (Merge failed).")
            else:
                print("FAILURE: No Create event found.")
                
        else:
            print(f"Error: {resp.status_code} - {resp.text}")
            
    except Exception as e:
        print(f"Exception: {e}")

if __name__ == "__main__":
    verify_api()
