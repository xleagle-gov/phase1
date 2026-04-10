"""
OpenAI API service for vendor lead generation.
Centralizes all OpenAI API calls and response parsing.
"""
import openai
import sys
import os

# Add parent directory to path to import from project root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import (
    OPENAI_API_KEY, 
    OPENAI_MODEL, 
    OPENAI_REASONING_EFFORT, 
    OPENAI_SYSTEM_MESSAGE, 
    PROMPT_FILE
)
from parse_response import parse_openai_response, extract_email_info_regex


def load_prompt_template() -> str:
    """
    Load the prompt template from file.
    
    Returns:
        str: The prompt template content
        
    Raises:
        FileNotFoundError: If prompt file doesn't exist
    """
    with open(PROMPT_FILE, "r", encoding="utf-8") as f:
        return f.read()


def build_prompt(solicitation_text: str, source: str = "SAM.GOV") -> str:
    """
    Build the full prompt for vendor lead generation.
    
    Args:
        solicitation_text: The extracted text from the solicitation
        source: Source identifier (SAM.GOV or ESBD)
    
    Returns:
        str: The complete prompt to send to OpenAI
    """
    template = load_prompt_template()
    return f"""
{template}

================================================================================
SOLICITATION DETAILS FROM {source}:
================================================================================

{solicitation_text}

================================================================================
END OF SOLICITATION DETAILS
================================================================================

Based on the above solicitation details, please find local vendors and create the email as specified in the prompt instructions.
"""


def call_openai_api(prompt: str, system_message: str = None):
    """
    Call OpenAI API with web search capability.
    
    Args:
        prompt: The user prompt to send
        system_message: Optional custom system message (uses default if not provided)
    
    Returns:
        tuple: (response object, extracted response text)
    """
    client = openai.OpenAI(api_key=OPENAI_API_KEY)
    
    print(f"Calling OpenAI {OPENAI_MODEL} with responses API...")
    
    response = client.responses.create(
        model=OPENAI_MODEL,
        tools=[{"type": "web_search"}],
        input=[
            {"role": "system", "content": system_message or OPENAI_SYSTEM_MESSAGE},
            {"role": "user", "content": prompt}
        ],
        reasoning={"effort": OPENAI_REASONING_EFFORT},
        stream=False
    )
    
    return response, _extract_response_text(response)


def _extract_response_text(response) -> str:
    """
    Extract text from various OpenAI response formats.
    Handles both the responses API and the chat completions API.
    
    Args:
        response: The OpenAI API response object
        
    Returns:
        str: The extracted text content
    """
    # Responses API format (client.responses.create)
    if hasattr(response, 'output_text') and response.output_text:
        return response.output_text
    # Responses API: iterate output items for text
    if hasattr(response, 'output') and response.output:
        for item in response.output:
            if hasattr(item, 'content') and item.content:
                for block in item.content:
                    if hasattr(block, 'text') and block.text:
                        return block.text
    # Chat completions format
    if hasattr(response, 'choices') and response.choices:
        choice = response.choices[0]
        if hasattr(choice, 'message') and hasattr(choice.message, 'content'):
            return choice.message.content
        return choice.content if hasattr(choice, 'content') else str(choice)
    # Generic fallbacks
    if hasattr(response, 'text') and hasattr(response.text, 'content'):
        return response.text.content
    elif hasattr(response, 'content'):
        return response.content
    elif hasattr(response, 'text'):
        return str(response.text)
    return str(response)


def extract_email_info(response, gpt_response: str) -> dict:
    """
    Extract email, subject, body from AI response using regex parsing.
    
    Args:
        response: The raw OpenAI response object
        gpt_response: The extracted text from the response
        
    Returns:
        dict: Contains 'emails', 'subject', and 'body' keys
    """
    print("EXTRACTING EMAIL INFORMATION FROM AI RESPONSE")

    # Try the already-extracted clean text first (from _extract_response_text)
    if gpt_response and len(gpt_response) > 100:
        result = extract_email_info_regex(gpt_response)
        if result.get("emails") != "Not found" or result.get("subject") != "Not found":
            return result
        print("Regex on extracted text returned 'Not found', trying parse_openai_response fallback...")

    # Fallback: try parsing the raw response object string
    readable_content = parse_openai_response(str(response))
    if readable_content and readable_content != "Could not extract readable content from the response.":
        return extract_email_info_regex(readable_content)

    print("WARNING: All parsing methods failed to extract email info")
    return {"emails": "Not found", "subject": "Not found", "body": "Not found"}


def generate_vendor_leads(solicitation_text: str, source: str = "SAM.GOV", subject_suffix: str = "") -> dict:
    """
    Main function to generate vendor leads from solicitation text.
    This is the primary function to call from other modules.
    
    Args:
        solicitation_text: The extracted text from the solicitation
        source: Source identifier (SAM.GOV or ESBD)
        subject_suffix: Optional suffix to add to subject (e.g., " k2", "- texasLocal")
    
    Returns:
        dict: Contains 'emails', 'subject', 'body' keys
              On error, values will contain error message
    """
    try:
        # Load and build prompt
        print(f"Step 1: Reading prompt from {PROMPT_FILE}...")
        try:
            prompt = build_prompt(solicitation_text, source)
            print(f"SUCCESS: Built prompt ({len(prompt)} characters)")
        except Exception as e:
            print(f"ERROR: Could not read prompt file: {e}")
            error_msg = f"ERROR: Could not read prompt file: {e}"
            return {"emails": error_msg, "subject": error_msg, "body": error_msg}
        
        # Call OpenAI API
        print("Step 2: Calling OpenAI API...")
        response, gpt_response = call_openai_api(prompt)
        
        # Extract email info
        print("Step 3: Extracting email information...")
        email_info = extract_email_info(response, gpt_response)
        
        # Add suffix to subject if provided
        subject = email_info["subject"]
        if subject_suffix and subject and subject != "Not found":
            subject = subject + subject_suffix
            print(f"✅ Added suffix '{subject_suffix}' to subject")
        
        result = {
            "emails": email_info["emails"],
            "subject": subject,
            "body": email_info["body"]
        }
        
        print(f"✅ Successfully generated vendor leads")
        return result
        
    except Exception as e:
        print(f"ERROR: Failed to generate vendor leads: {e}")
        error_msg = f"ERROR: OpenAI API failed: {e}"
        return {"emails": error_msg, "subject": error_msg, "body": error_msg}
