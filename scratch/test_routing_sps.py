import urllib.request
import json

def test_sps():
    url = "http://localhost:8000/api/sps/find?start_date=2024-01-01&end_date=2024-01-05&families=USD"
    req = urllib.request.Request(url)
    req.add_header("Authorization", "Basic YWRtaW46Y2hhaW50ZWxsaWdlbmNlNzc=")
    try:
        with urllib.request.urlopen(req) as f:
            print("SPS:", f.read().decode('utf-8')[:200])
    except Exception as e:
        print("SPS error:", e)

def test_routing():
    url = "http://localhost:8000/api/routes/analyze?start_token=USDT&end_token=USDC&days=4&network=Ethereum"
    req = urllib.request.Request(url)
    req.add_header("Authorization", "Basic YWRtaW46Y2hhaW50ZWxsaWdlbmNlNzc=")
    try:
        with urllib.request.urlopen(req) as f:
            for line in f:
                if b'"type": "result"' in line:
                    print("Routing:", line.decode('utf-8')[:200])
    except Exception as e:
        print("Routing error:", e)

test_sps()
test_routing()
