import time
import psycopg2
import sys
import os

DB_HOST = os.getenv("DB_HOST", "postgres-test")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME", "chaintelligence")
DB_USER = os.getenv("DB_USER", "airflow")
DB_PASS = os.getenv("DB_PASS", "airflow")

def get_connection():
    try:
        conn = psycopg2.connect(
            host=DB_HOST,
            port=DB_PORT,
            dbname=DB_NAME,
            user=DB_USER,
            password=DB_PASS
        )
        return conn
    except Exception as e:
        print(f"❌ Failed to connect to DB: {e}")
        return None

def verify_coin_families():
    print("🔍 Verifying Coin Families...")
    conn = get_connection()
    if not conn: return False
    
    try:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM coin_family")
        count = cur.fetchone()[0]
        print(f"   Found {count} coin families.")
        
        # Check specific family
        cur.execute("SELECT name FROM coin_family WHERE name = 'Stablecoins'")
        if cur.fetchone():
            print("   ✅ 'Stablecoins' family found.")
        else:
            print("   ❌ 'Stablecoins' family NOT found.")
            return False
            
        return count > 0
    finally:
        conn.close()

def verify_coin_mapping():
    print("🔍 Verifying Coin Mapping...")
    conn = get_connection()
    if not conn: return False
    
    try:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM coin WHERE cmc_id IS NOT NULL")
        count = cur.fetchone()[0]
        print(f"   Found {count} coins with CMC IDs.")
        return count > 0
    finally:
        conn.close()

def verify_coin_prices():
    print("🔍 Verifying Coin Prices...")
    conn = get_connection()
    if not conn: return False
    
    try:
        cur = conn.cursor()
        # Verify coin table populated
        cur.execute("SELECT COUNT(*) FROM coin")
        coin_count = cur.fetchone()[0]
        print(f"   Found {coin_count} coins in total.")
        
        # Verify prices updated (price IS NOT NULL)
        cur.execute("SELECT COUNT(*) FROM coin WHERE price IS NOT NULL")
        priced_count = cur.fetchone()[0]
        print(f"   Found {priced_count} coins with updated prices.")
        
        # Check specific coin (e.g. ETH)
        cur.execute("SELECT price, cmc_last_updated FROM coin WHERE symbol = 'ETH'")
        eth = cur.fetchone()
        if eth and eth[0]:
            print(f"   ✅ ETH Price: ${eth[0]} (Updated: {eth[1]})")
        else:
            print("   ⚠️  ETH price not found/updated (Expected if target was different)")
            
        return priced_count > 0
    finally:
        conn.close()

if __name__ == "__main__":
    print("🚀 Starting Verification...")
    
    verify_type = "all"
    if len(sys.argv) > 1:
        verify_type = sys.argv[1]
    
    families_ok = True
    prices_ok = True
    
    if verify_type in ["all", "families"]:
        families_ok = verify_coin_families()
        
    if verify_type in ["all", "prices"]:
        prices_ok = verify_coin_prices()
    
    if families_ok and prices_ok:
        print("\n✅ VERIFICATION PASSED")
        sys.exit(0)
    else:
        print("\n❌ VERIFICATION FAILED")
        sys.exit(1)
