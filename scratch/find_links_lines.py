with open("api/main.py") as f:
    for i, line in enumerate(f, 1):
        if "build_pool_links" in line:
            print(f"L{i}: {line.strip()}")
