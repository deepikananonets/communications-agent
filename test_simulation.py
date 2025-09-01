#!/usr/bin/env python3
"""
Test script to demonstrate the simulated Zapier webhook flow

This script shows how the webhook simulation works with hardcoded "Spravato" response.
"""

from patient_responsibility_agent import ZapierWebhook
import logging

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

def test_webhook_simulation():
    """Test the webhook simulation with hardcoded response."""
    print("=== Testing Zapier Webhook Simulation ===\n")
    
    # You can use any URL here - even a fake one since we're simulating
    webhook_url = "https://hooks.zapier.com/hooks/catch/SIMULATION/TEST/"
    
    # Create webhook instance
    zapier = ZapierWebhook(webhook_url)
    
    # Test with sample patient names
    test_patients = [
        "Smith,John",
        "Johnson,Mary", 
        "Williams,Robert",
        "Brown,Patricia"
    ]
    
    print("Testing webhook simulation for multiple patients:\n")
    
    for patient_name in test_patients:
        print(f"Patient: {patient_name}")
        service_line = zapier.send_patient_data(patient_name)
        print(f"  → Service Line: {service_line}")
        print()
    
    print("✅ All webhook simulations completed!")
    print("📋 Note: All patients received 'Spravato' as the service line")

if __name__ == "__main__":
    test_webhook_simulation()
