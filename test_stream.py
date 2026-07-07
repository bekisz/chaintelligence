import urllib.request
url = "http://localhost:8000/api/routes/analyze?start_token=USDC&end_token=WETH&days=1&network=Ethereum"
req = urllib.request.Request(url)
req.add_header("Authorization", "Basic YWRtaW46Y2hhaW50ZWxsaWdlbmNlNzc=")
with urllib.request.urlopen(req) as f:
    for line in f:
        print(line.decode('utf-8').strip())
