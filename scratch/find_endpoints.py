with open("api/main.py") as f:
    for i, line in enumerate(f, 1):
        if line.strip().startswith("@app."):
            print(f"L{i}: {line.strip()}")
