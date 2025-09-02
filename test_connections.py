#!/usr/bin/env python3
"""
Test script to validate API connections and configuration

This script tests:
1. AdvancedMD API authentication
2. Zapier webhook connectivity
3. Configuration validation
"""

import requests
import json
from patient_responsibility_agent import AdvancedMDAPI
from config import ZAPIER_WEBHOOK_URL, AMD_CONFIG
import logging

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def test_amd_authentication():
    """Test AdvancedMD API authentication."""
    print("Testing AdvancedMD Authentication...")
    
    amd_api = AdvancedMDAPI()
    
    if amd_api.authenticate():
        print("✅ AdvancedMD authentication successful")
        print(f"   Token: {amd_api.token[:20]}...")
        return True
    else:
        print("❌ AdvancedMD authentication failed")
        return False

def test_zapier_webhook():
    """Test Zapier webhook connectivity."""
    print("\nTesting Zapier Webhook...")
    
    if "XXXXXXX" in ZAPIER_WEBHOOK_URL:
        print("⚠️  Zapier webhook URL not configured (contains XXXXXXX)")
        return False
    
    try:
        test_payload = {"patient_name": "Test Patient", "test": True}
        response = requests.post(ZAPIER_WEBHOOK_URL, json=test_payload, timeout=10)
        
        if response.status_code == 200:
            print("✅ Zapier webhook reachable")
            try:
                result = response.json()
                print(f"   Response: {result}")
            except:
                print(f"   Response (text): {response.text}")
            return True
        else:
            print(f"❌ Zapier webhook returned status: {response.status_code}")
            return False
            
    except requests.exceptions.Timeout:
        print("❌ Zapier webhook timeout")
        return False
    except requests.exceptions.RequestException as e:
        print(f"❌ Zapier webhook error: {e}")
        return False

def test_configuration():
    """Test configuration values."""
    print("\nTesting Configuration...")
    
    required_amd_keys = ['base_url', 'api_base_url', 'username', 'password', 'office_code', 'app_name']
    missing_keys = [key for key in required_amd_keys if not AMD_CONFIG.get(key)]
    
    if missing_keys:
        print(f"❌ Missing AMD configuration keys: {missing_keys}")
        return False
    
    print("✅ AMD configuration complete")
    print(f"   Username: {AMD_CONFIG['username']}")
    print(f"   Office Code: {AMD_CONFIG['office_code']}")
    print(f"   App Name: {AMD_CONFIG['app_name']}")
    
    return True

def test_api_endpoints():
    """Test API endpoint reachability."""
    print("\nTesting API Endpoints...")
    
    # Test AdvancedMD base URL
    try:
        response = requests.get(AMD_CONFIG['base_url'].replace('/xmlrpc/processrequest.aspx', ''), timeout=5)
        print("✅ AdvancedMD base URL reachable")
    except:
        print("❌ AdvancedMD base URL not reachable")
        return False
    
    return True

def main():
    """Run all connection tests."""
    print("=== Patient Responsibility Agent - Connection Tests ===\n")
    
    tests = [
        test_configuration,
        test_api_endpoints,
        test_amd_authentication,
        test_zapier_webhook
    ]
    
    results = []
    for test in tests:
        try:
            result = test()
            results.append(result)
        except Exception as e:
            print(f"❌ Test failed with error: {e}")
            results.append(False)
    
    print(f"\n=== Test Summary ===")
    print(f"Passed: {sum(results)}/{len(results)}")
    
    if all(results):
        print("🎉 All tests passed! Ready to run the agent.")
    else:
        print("⚠️  Some tests failed. Please check configuration and connectivity.")
        
    return all(results)

if __name__ == "__main__":
    success = main()
    exit(0 if success else 1)
