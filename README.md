# Patient Responsibility Memo Agent

This Python script automates the process of calculating and posting patient responsibility memos to AdvancedMD based on insurance information and eligibility checks.

## Features

- Fetches updated patients from AdvancedMD API (last 24 hours)
- Filters patients with insurance and appointments
- Runs eligibility checks for each insurance
- Integrates with Zapier webhooks for service line lookup
- Calculates patient responsibility based on insurance type and copay data
- Posts memos to AdvancedMD patient records

## Setup

1. Install dependencies:
```bash
pip install -r requirements.txt
```

2. Configure the Zapier webhook URL in `config.py`:
```python
ZAPIER_WEBHOOK_URL = "https://hooks.zapier.com/hooks/catch/YOUR_HOOK_ID/YOUR_HOOK_ID/"
```

3. Run the script:
```bash
python patient_responsibility_agent.py
```

## Configuration

Edit `config.py` to modify:
- AdvancedMD API credentials and endpoints
- Zapier webhook URL
- Processing parameters (hours back, wait times, etc.)
- Medicaid insurance indicators

## Patient Responsibility Calculation Rules

1. **Medicaid Insurance**: Always $0
2. **Non-Medicaid with Copay**: Uses the copay dollar amount
3. **Non-Medicaid without Copay but with Coinsurance**: Coinsurance percentage × $400

## Logging

The script logs all activities to:
- Console output
- `patient_responsibility.log` file

Log levels include DEBUG, INFO, WARNING, and ERROR for comprehensive monitoring.

## Workflow

1. **Authentication**: Logs into AdvancedMD API
2. **Patient Retrieval**: Gets patients updated in last 24 hours with insurance
3. **Appointment Filtering**: Keeps only patients with appointments
4. **Processing Loop**: For each patient:
   - Runs eligibility checks
   - Sends data to Zapier webhook
   - Calculates responsibility
   - Posts memo to AdvancedMD

## Error Handling

- Robust error handling for API failures
- Continues processing other patients if one fails
- Comprehensive logging for troubleshooting
- Graceful handling of missing data

## API Integration

### AdvancedMD APIs Used:
- Authentication (login)
- getUpdatedPatients
- getAppointmentByPatientId
- Eligibility check (submit and response)
- Memo posting

### Zapier Integration:
- Sends patient name via webhook
- Receives service line response
- Handles timeout and error cases
