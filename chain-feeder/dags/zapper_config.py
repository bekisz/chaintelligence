import os
from dotenv import load_dotenv

# Load .env if present
load_dotenv()

# Zapper Credentials
AUTH_HEADER = os.getenv('ZAPPER_AUTH_HEADER')
ENDPOINT = os.getenv('ZAPPER_ENDPOINT', 'https://public.zapper.xyz/graphql')

# Support multiple addresses as comma-separated string
target_addr_str = os.getenv('TARGET_ADDRESS', '')
TARGET_ADDRESSES = [a.strip() for a in target_addr_str.split(',') if a.strip()]


