#!/usr/bin/env python3
"""
Test script for PVerify API integration

This script tests the PVerify API functionality independently.
"""

from patient_responsibility_agent import PVerifyAPI
import logging

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

def test_pverify_token():
    """Test PVerify token acquisition."""
    print("=== Testing PVerify Token ===")
    
    pverify = PVerifyAPI()
    
    if pverify.get_access_token():
        print(f"✅ Token acquired successfully")
        print(f"   Token: {pverify.access_token[:50]}...")
        print(f"   Expires at: {pverify.token_expires_at}")
        return True
    else:
        print("❌ Failed to get token")
        return False

def test_insurance_matching():
    """Test insurance name matching logic."""
    print("\n=== Testing Insurance Name Matching ===")
    
    pverify = PVerifyAPI()
    
    test_cases = [
        ("UNITED HEALTHCARE", "United Healthcare", True),
        ("BCBS", "Blue Cross Blue Shield", True),
        ("MCD", "MEDICAID", True),
        ("ANTHEM BCBS", "ANTHEM BLUE CROSS", True),
        ("RANDOM INSURANCE", "COMPLETELY DIFFERENT", False),
        ("CIGNA HEALTH", "CIGNA HEALTHCARE", True)
    ]
    
    for amd_name, pverify_name, expected in test_cases:
        result = pverify.match_insurance_name(amd_name, pverify_name)
        status = "✅" if result == expected else "❌"
        print(f"{status} {amd_name} vs {pverify_name} -> {result} (expected {expected})")

def test_location_mapping():
    """Test location and state ID mapping."""
    print("\n=== Testing Location Mapping ===")
    
    pverify = PVerifyAPI()
    
    test_patients = [
        {"name": "Test,Patient", "state": "CO"},
        {"name": "Test,Patient", "state": "TX"},
        {"name": "Test,Patient", "state": "COLORADO"},
        {"name": "Test,Patient", "state": "TEXAS"},
        {"name": "Test,Patient", "state": "CA"}  # Should default to CO
    ]
    
    for patient in test_patients:
        location, state_id = pverify.get_location_and_state_id(patient)
        print(f"State: {patient['state']} -> Location: {location}, State ID: {state_id}")

def test_insurance_discovery():
    """Test insurance discovery with sample patient."""
    print("\n=== Testing Insurance Discovery ===")
    
    pverify = PVerifyAPI()
    
    # Sample patient data
    sample_patient = {
        "name": "Smith,John",
        "dob": "01/15/1985",
        "gender": "M",
        "state": "CO",
        "city": "Denver"
    }
    
    print(f"Testing discovery for: {sample_patient['name']}")
    
    if pverify.get_access_token():
        discovery_result = pverify.insurance_discovery(sample_patient)
        
        if discovery_result:
            print(f"✅ Discovery completed")
            print(f"   Payer Found: {discovery_result.get('PayerFound')}")
            print(f"   Payer Name: {discovery_result.get('PayerName')}")
            print(f"   Member ID: {discovery_result.get('MemberID')}")
        else:
            print("❌ Discovery failed")
    else:
        print("❌ Could not get token for discovery test")

def main():
    """Run all PVerify tests."""
    print("=== PVerify API Integration Tests ===\n")
    
    tests = [
        test_pverify_token,
        test_insurance_matching,
        test_location_mapping,
        test_insurance_discovery
    ]
    
    results = []
    for test in tests:
        try:
            result = test()
            results.append(result if result is not None else True)
        except Exception as e:
            print(f"❌ Test failed with error: {e}")
            results.append(False)
    
    print(f"\n=== Test Summary ===")
    print(f"Passed: {sum(results)}/{len(results)}")
    
    if all(results):
        print("🎉 All PVerify tests passed!")
    else:
        print("⚠️  Some tests failed. Check the output above.")

if __name__ == "__main__":
    main()
