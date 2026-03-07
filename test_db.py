import psycopg2

try:
    # This is the exact same connection code
    conn = psycopg2.connect(
        dbname='zeroaudit',
        user='audit_user',
        password='StrongPass123!',
        host='localhost',
        port='5432'
    )
    print("✅ Connection successful!")
    conn.close()
except Exception as e:
    print(f"❌ Connection failed: {e}")
    print(f"Error type: {type(e)}")