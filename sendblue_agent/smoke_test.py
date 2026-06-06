# smoke_test.py
import sys
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient

# Import your configured FastAPI app instance
from main import app

# Initialize the test client (automatically handles the startup/lifespan events)
client = TestClient(app)

def test_proactive_alert_endpoint():
    print("🏃 Testing: POST /alert (Proactive Alert Engine)...")
    
    payload = {
        "target_number": "+15558675309",
        "asset": "NVDA",
        "anomaly_type": "Volatility Spike",
        "current_price": 131.25,
        "reason": "Blackwell platform adoption structural shift"
    }
    
    # Mock out the async database layer to prevent the smoke test from hitting live network sockets
    with patch("interface.db_get_market_context", new_callable=AsyncMock) as mock_context:
        mock_context.return_value = "Mocked structural profile tracking data for NVDA."
        
        response = client.post("/alert", json=payload)
        
        assert response.status_code == 200
        assert response.json() == {"status": "alert_dispatched"}
        print("✅ POST /alert passed successfully!")


def test_webhook_unauthorized_sender():
    print("🏃 Testing: POST /webhook (Dropping Unauthorized Numbers)...")
    
    payload = {
        "number": "+19999999999",  # An unregistered phone number
        "content": "Analyze NVDA"
    }
    
    # 1. Mock signature check to bypass encryption parameters
    # 2. Mock database user check to return None (simulating an unauthorized caller)
    with patch("main.verify_sendblue_signature", return_value=True), \
         patch("main.db_get_authorized_user", new_callable=AsyncMock) as mock_auth:
        
        mock_auth.return_value = None  
        
        response = client.post("/webhook", json=payload)
        
        assert response.status_code == 200
        assert response.json() == {"status": "dropped"}
        print("✅ POST /webhook safe-drop rule passed successfully!")


def test_webhook_authorized_sender_routing():
    print("🏃 Testing: POST /webhook (Authorized User Flow Enqueue)...")
    
    payload = {
        "number": "+15558675309",
        "content": "Analyze NVDA"
    }
    
    # Mock signature verification and simulate an active authorized database record
    with patch("main.verify_sendblue_signature", return_value=True), \
         patch("main.db_get_authorized_user", new_callable=AsyncMock) as mock_auth, \
         patch("interface.set_typing_indicator", new_callable=AsyncMock), \
         patch("interface.db_is_asset_allowed", new_callable=AsyncMock) as mock_allowed, \
         patch("interface.db_get_market_context", new_callable=AsyncMock) as mock_context, \
         patch("interface.send_imessage", new_callable=AsyncMock) as mock_send:
        
        mock_auth.return_value = {"phone_number": "+15558675309", "max_trade_usd": 5000.0, "is_active": True}
        mock_allowed.return_value = True
        mock_context.return_value = "Mocked market trajectory report."
        
        response = client.post("/webhook", json=payload)
        
        assert response.status_code == 200
        assert response.json() == {"status": "enqueued"}
        print("✅ POST /webhook routing matrix passed successfully!")


if __name__ == "__main__":
    print("=" * 50)
    print("      LAUNCHING FINLEY AGENT APPLICATION SMOKE TEST ")
    print("=" * 50)
    
    # Overwrite config's initialization print if database creds aren't active globally
    with patch("config.create_client"):
        try:
            test_proactive_alert_endpoint()
            print("-" * 50)
            test_webhook_unauthorized_sender()
            print("-" * 50)
            test_webhook_authorized_sender_routing()
            
            print("\n🎉 ALL SMOKE TESTS PASSED! Your codebase architecture is operationally sound.")
            sys.exit(0)
        except AssertionError as e:
            print("\n❌ SMOKE TEST FAILURE: Internal application state variant did not meet contract assertions.")
            sys.exit(1)
        except Exception as e:
            print(f"\n❌ SYSTEM SMOKE CRASH: Unexpected exception occurred: {e}")
            sys.exit(1)