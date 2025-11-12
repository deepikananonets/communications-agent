"""
Configuration file for Patient Responsibility Memo Agent
"""
import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# AdvancedMD API Configuration
AMD_CONFIG = {
    'base_url': 'https://providerapi.advancedmd.com/processrequest/api-128/NANONETS-HEALTH/xmlrpc/processrequest.aspx',
    'api_base_url': 'https://providerapi.advancedmd.com/api/api-128/NANONETS-HEALTH',
    'username': os.getenv('AMD_USERNAME'),
    'password': os.getenv('AMD_PASSWORD'),
    'office_code': int(os.getenv('AMD_OFFICE_CODE', 0)),
    'app_name': 'NANONETS-HEALTH'
}

# Zapier Webhook Configuration
ZAPIER_WEBHOOK_URL = os.getenv('ZAPIER_WEBHOOK_URL')

# Processing Configuration
PROCESSING_CONFIG = {
    'hours_back': 24,  # How many hours back to look for updated patients
    'eligibility_wait_time': 2,  # Seconds to wait between eligibility calls
    'coinsurance_multiplier': 400  # Amount to multiply coinsurance percentage by
}

# Medicaid Insurance Indicators
MEDICAID_INDICATORS = ['MCD', 'MEDICAID', 'HEALTH FIRST MEDICAID']

# PVerify API Configuration
PVERIFY_CONFIG = {
    'token_url': 'https://api.pverify.com/Token',
    'discovery_url': 'https://api.pverify.com/api/InsuranceDiscovery',
    'eligibility_url': 'https://api.pverify.com/API/EligibilityInquiry',
    'client_id': os.getenv('PVERIFY_CLIENT_ID'),
    'client_secret': os.getenv('PVERIFY_CLIENT_SECRET'),
    'provider_last_name': 'Bonnett',
    'provider_npi': '1427007327'
}

# State ID mapping for PVerify
STATE_IDS = {
    'CO': 8222,
    'TX': 7985
}

# Database Configuration for logging (matching reference structure)
DB_CONFIG = {
    'host': os.getenv('FLEMING_DB_HOST'),
    'port': int(os.getenv('FLEMING_DB_PORT', 5432)),
    'database': os.getenv('FLEMING_DB_NAME'),
    'username': os.getenv('FLEMING_DB_USER'),
    'password': os.getenv('FLEMING_DB_PASSWORD'),
    'sslmode': os.getenv('FLEMING_DB_SSLMODE', 'require')
}

# Agent ID for database logging
AGENT_ID = os.getenv('AGENT_ID')