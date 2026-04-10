#!/usr/bin/env python3
"""
Script to parse and extract readable content from OpenAI API response objects.
"""

import re
from gemini import analyze_contract_text

def parse_openai_response(response_text):
    """
    Parse the OpenAI response object and extract the meaningful content.
    """
    
    # Extract the main text content from the ResponseOutputMessage
    text_match = re.search(r"text='([^']*(?:'[^']*)*)'", response_text, re.DOTALL)
    
    if text_match:
        # Get the raw text and clean it up
        raw_text = text_match.group(1)
        
        # Decode escape sequences
        cleaned_text = raw_text.replace('\\xa0', ' ')  # Non-breaking space
        cleaned_text = cleaned_text.replace('\\n', '\n')  # Newlines
        cleaned_text = cleaned_text.replace('\\"', '"')   # Escaped quotes
        cleaned_text = cleaned_text.replace("\\'", "'")   # Escaped apostrophes
        
        return cleaned_text
    
    return "Could not extract readable content from the response."

def extract_key_info(response_text):
    """
    Extract key metadata from the response object.
    """
    info = {}
    
    # Extract model
    model_match = re.search(r"model='([^']*)'", response_text)
    if model_match:
        info['model'] = model_match.group(1)
    
    # Extract creation time
    created_match = re.search(r"created_at=([0-9.]+)", response_text)
    if created_match:
        import datetime
        timestamp = float(created_match.group(1))
        info['created_at'] = datetime.datetime.fromtimestamp(timestamp).strftime('%Y-%m-%d %H:%M:%S')
    
    # Extract token usage
    input_tokens_match = re.search(r"input_tokens=(\d+)", response_text)
    output_tokens_match = re.search(r"output_tokens=(\d+)", response_text)
    total_tokens_match = re.search(r"total_tokens=(\d+)", response_text)
    
    if input_tokens_match:
        info['input_tokens'] = input_tokens_match.group(1)
    if output_tokens_match:
        info['output_tokens'] = output_tokens_match.group(1)
    if total_tokens_match:
        info['total_tokens'] = total_tokens_match.group(1)
    
    # Count web searches performed
    web_search_count = len(re.findall(r"ResponseFunctionWebSearch", response_text))
    info['web_searches_performed'] = web_search_count
    
    return info

def extract_email_info_with_gemini(response_content):
    """
    Use Gemini to extract email list, subject, and body from the response content.
    """
    
    # Create a prompt for Gemini to extract the required information
    extraction_prompt = """
You are an expert at parsing text content to extract specific information. 
From the provided text, I need you to extract exactly three pieces of information:

1. **List of emails**: Find all email addresses mentioned in the text and return them separated by semicolons (;)
2. **Subject**: Find the email subject line mentioned in the text
3. **Body**: Find the email body/content mentioned in the text

If the email **body** is written in plain text (i.e., no HTML tags like `<p>`, `<br>`, `<strong>`, etc.), **convert it into clean, well-formatted HTML**.  
When converting:
- Wrap each paragraph in `<p>` tags  
- Use `<br>` for intentional line breaks  
- Bold key labels (like “Location:”, “Scope Includes:”) with `<strong>`  
- Format lists with `<ul>` and `<li>`  
- Add logical spacing and line breaks for readability  
- Do **not** alter or add words — only structure and format it for professional Gmail display  

Please return your response in this exact JSON format:
{
  "emails": "email1@domain.com; email2@domain.com; email3@domain.com",
  "subject": "The subject line found in the text",
  "body": "The complete email body content (in formatted HTML if originally plain text)"
}

If any of these elements are not found, use "Not found" as the value.

Here is the text to analyze:

"""
    
    # Combine the prompt with the response content
    full_prompt = extraction_prompt + "\n\n" + response_content
    
    try:
        # Use Gemini to analyze and extract the information
        result = analyze_contract_text(full_prompt, custom_prompt=extraction_prompt, use_cache=False)
        
        if isinstance(result, dict) and "error" not in result:
            return result
        else:
            print(f"Error from Gemini: {result}")
            return None
            
    except Exception as e:
        print(f"Error calling Gemini: {e}")
        return None

def extract_email_info_regex(response_content):
    """
    Fallback method using regex to extract email information.
    """
    result = {
        "emails": "Not found",
        "subject": "Not found", 
        "body": "Not found"
    }
    
    # Extract emails using regex
    email_pattern = r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'
    emails = re.findall(email_pattern, response_content)
    
    if emails:
        # Remove duplicates while preserving order
        unique_emails = []
        seen = set()
        for email in emails:
            if email.lower() not in seen:
                unique_emails.append(email)
                seen.add(email.lower())
        result["emails"] = "; ".join(unique_emails)
    
    # Extract subject line
    subject_patterns = [
        r'Subject:\s*([^\n\r]+)',
        r'subject:\s*([^\n\r]+)',
        r'SUBJECT:\s*([^\n\r]+)'
    ]
    
    for pattern in subject_patterns:
        subject_match = re.search(pattern, response_content)
        if subject_match:
            result["subject"] = subject_match.group(1).strip()
            break
    
    # Extract email body (this is more complex, looking for email-like content)
    # Look for patterns that indicate email body content
    body_patterns = [
        r'Hi[,\s].*?(?=\n\n|\n[A-Z][a-z]+:|$)',
        r'Hello[,\s].*?(?=\n\n|\n[A-Z][a-z]+:|$)',
        r'We\'re requesting.*?(?=\n\n|\nThanks|$)',
    ]
    
    for pattern in body_patterns:
        body_match = re.search(pattern, response_content, re.DOTALL)
        if body_match:
            result["body"] = body_match.group(0).strip()
            break
    
    return result

def main():
    # Read the response file
    with open('response.txt', 'r') as f:
        response_content = f.read()
    
    print("=" * 80)
    print("OPENAI API RESPONSE PARSER")
    print("=" * 80)
    
    # Extract key information
    key_info = extract_key_info(response_content)
    
    print("\n📊 RESPONSE METADATA:")
    print("-" * 40)
    for key, value in key_info.items():
        print(f"{key.replace('_', ' ').title()}: {value}")
    
    print("\n" + "=" * 80)
    print("📝 EXTRACTED CONTENT:")
    print("=" * 80)
    
    # Parse and display the main content
    readable_content = parse_openai_response(response_content)
    print(readable_content)
    
    # Extract email information
    print("\n" + "=" * 80)
    print("📧 EMAIL EXTRACTION")
    print("=" * 80)
    
    # First try with Gemini
    print("\n🤖 Trying extraction with Gemini AI...")
    gemini_result = extract_email_info_with_gemini(readable_content)
    
    if gemini_result and isinstance(gemini_result, dict):
        print("✅ Gemini extraction successful!")
        
        print(f"\n📧 EMAILS:")
        print("-" * 40)
        emails = gemini_result.get("emails", "Not found")
        print(emails)
        
        print(f"\n📋 SUBJECT:")
        print("-" * 40)
        subject = gemini_result.get("subject", "Not found")
        print(subject)
        
        print(f"\n📝 BODY:")
        print("-" * 40)
        body = gemini_result.get("body", "Not found")
        print(body)
        
        # Save to separate files for easy access
        try:
            with open('extracted_emails.txt', 'w', encoding='utf-8') as f:
                f.write(emails)
            
            with open('extracted_subject.txt', 'w', encoding='utf-8') as f:
                f.write(subject)
                
            with open('extracted_body.txt', 'w', encoding='utf-8') as f:
                f.write(body)
                
            print(f"\n💾 Results saved to:")
            print("   - extracted_emails.txt")
            print("   - extracted_subject.txt") 
            print("   - extracted_body.txt")
            
        except Exception as e:
            print(f"Warning: Could not save results to files: {e}")
    
    else:
        print("❌ Gemini extraction failed. Trying regex fallback...")
        
        # Fallback to regex extraction
        regex_result = extract_email_info_regex(readable_content)
        
        print(f"\n📧 EMAILS (Regex):")
        print("-" * 40)
        print(regex_result["emails"])
        
        print(f"\n📋 SUBJECT (Regex):")
        print("-" * 40)
        print(regex_result["subject"])
        
        print(f"\n📝 BODY (Regex):")
        print("-" * 40)
        print(regex_result["body"])
    
    print("\n" + "=" * 80)
    print("✅ Parsing complete!")
    print("=" * 80)

if __name__ == "__main__":
    main()