"""
Configuration file for Patient Responsibility Memo Agent
"""
import os

# AdvancedMD API Configuration
AMD_CONFIG = {
    'base_url': 'https://providerapi.advancedmd.com/processrequest/api-128/NANONETS-HEALTH/xmlrpc/processrequest.aspx',
    'api_base_url': 'https://providerapi.advancedmd.com/api/api-801/TEMP',
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
    'coinsurance_multiplier': 400,  # Amount to multiply coinsurance percentage by
    'memo_expiration_days': 3  # Number of days after which memos expire
}

# Default coinsurance rate (as a decimal) applied when eligibility data is unavailable
DEFAULT_COINSURANCE_RATE = float(os.getenv('DEFAULT_COINSURANCE_RATE', 0.1))

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

# SSH Configuration for database connection
SSH_CONFIG = {
    'use_ssh': os.getenv('USE_SSH', '1').strip().lower() in ("1","true","yes","on"),
    'bastion_host': os.getenv('FLEMING_SSH_HOST', ''),
    'bastion_port': int(os.getenv('FLEMING_SSH_PORT', '22')),
    'bastion_user': os.getenv('FLEMING_SSH_USER', ''),
    'private_key_path': os.getenv('SSH_PRIVATE_KEY_PATH', '/home/runner/.ssh/id_rsa')
}

# Agent ID for database logging
AGENT_ID = os.getenv('AGENT_ID')