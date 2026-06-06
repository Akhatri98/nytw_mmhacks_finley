import os
import sys
from typing import Optional
from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()

# Sendblue Credentials
SENDBLUE_API_KEY = os.getenv("SENDBLUE_API_KEY")
SENDBLUE_SECRET = os.getenv("SENDBLUE_SECRET")
SENDBLUE_WEBHOOK_SECRET = os.getenv("SENDBLUE_WEBHOOK_SECRET")
MY_DEDICATED_NUMBER = os.getenv("MY_DEDICATED_NUMBER")

SENDBLUE_HEADERS = {
    "sb-api-key-id": SENDBLUE_API_KEY,
    "sb-api-secret-key": SENDBLUE_SECRET,
    "Content-Type": "application/json",
}
BASE_URL = "https://api.sendblue.co/api"

# Execution Mode Flag
LOCAL_MODE: bool = (
    "--terminal" in sys.argv
    or os.getenv("LOCAL_MODE", "false").lower() == "true"
)

# Supabase Storage Singleton Pattern
_supabase: Optional[Client] = None

def get_db() -> Client:
    if _supabase is None:
        raise RuntimeError("Supabase client not initialized. Execute init_supabase() first.")
    return _supabase

def init_supabase() -> None:
    global _supabase
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_KEY")
    if not url or not key:
        raise EnvironmentError("SUPABASE_URL and SUPABASE_KEY must be set in your environment configuration.")
    _supabase = create_client(url, key)
    print("✅ Connected securely to Supabase Instance")