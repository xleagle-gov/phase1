"""Services package for government contracts processing."""
from .openai_service import (
    generate_vendor_leads,
    call_openai_api,
    build_prompt,
    load_prompt_template,
    extract_email_info
)

__all__ = [
    'generate_vendor_leads',
    'call_openai_api', 
    'build_prompt',
    'load_prompt_template',
    'extract_email_info'
]
