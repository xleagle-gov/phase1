"""
Centralized configuration for government contracts processing.
All API keys, constants, and settings are defined here.
"""
import os

# =============================================================================
# API KEYS
# Note: In production, use environment variables instead of hardcoded values
# =============================================================================
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY', '')
SAM_GOV_API_KEY = os.getenv('SAM_GOV_API_KEY', '')

# =============================================================================
# BOUNCER EMAIL VERIFICATION
# =============================================================================
BOUNCER_API_KEY = os.getenv('BOUNCER_API_KEY', '')

# =============================================================================
# GOOGLE DRIVE
# =============================================================================
DRIVE_PARENT_FOLDER_ID = "1lfRQ8kUL7RwR1tx9QHEY8h_P4qrAk9LN"
GOOGLE_SERVICE_ACCOUNT_FILE = "key.json"
ENABLE_DRIVE_UPLOAD = True  # Toggle to enable/disable Google Drive file uploads

# =============================================================================
# GOOGLE SHEETS
# =============================================================================
SPREADSHEET_NAME = "Quote Request"
SAM_GOV_WORKSHEET = "SAM.GOV"
LOCAL_CONTRACTS_WORKSHEET = "localContracts"

# =============================================================================
# CACHE SETTINGS
# =============================================================================
TEXT_CACHE_DIR = "text_cache"
CONTRACT_CACHE_DIR = "simple_cache"
CACHE_EXPIRY_HOURS = 24

# =============================================================================
# CONTRACT FILTERING
# =============================================================================
ALLOWED_CONTRACT_TYPES = [
    "Combined Synopsis/Solicitation",
    "Solicitation"
]

ALLOWED_SETASIDE_TYPES = [
    "Total Small Business Set-Aside (FAR 19.5)",
    "Partial Small Business Set-Aside (FAR 19.5)",
    "Veteran Set Aside",
    "Service-Disabled Veteran-Owned Small Business Set Aside"
]

BLOCKED_EMAIL_DOMAINS = [
    "DibbsBSM@dla.mil",
    "NAVY.MIL",
    "DLA.MIL"
]

MIN_DAYS_UNTIL_DEADLINE = 5

# =============================================================================
# API ENDPOINTS
# =============================================================================
SAM_GOV_API_URL = "https://api.sam.gov/opportunities/v2/search"
ESBD_BASE_URL = "https://www.txsmartbuy.gov/esbd"

# =============================================================================
# EAST TEXAS COUNTY FILTERING
# Contracts in these counties are duplicated to a separate worksheet
# =============================================================================
EAST_TX_COUNTIES = [
    "Tyler", "Angelina", "Polk", "Jasper", "Hardin",
    "San Augustine", "Jefferson", "Nacogdoches", "Sabine",
]
EAST_TX_WORKSHEET = "eastTX_localContracts"

# =============================================================================
# OPENAI SETTINGS
# =============================================================================
OPENAI_MODEL = "gpt-5"
OPENAI_REASONING_EFFORT = "medium"
OPENAI_SYSTEM_MESSAGE = """You are an expert at finding local vendors for government contracts. Use web search to find real, verified vendor information including company names, emails, and phone numbers. Prioritize local small/medium businesses within the specified geographic area. Use your reasoning capabilities and web search to provide comprehensive, accurate results."""

# =============================================================================
# PROMPT FILE
# =============================================================================
PROMPT_FILE = "promptv3.txt"
