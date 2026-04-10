import os
import io
import requests
import re
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseUpload, MediaIoBaseDownload

# If modifying these SCOPES, delete the file token.json.
SCOPES = ['https://www.googleapis.com/auth/drive','https://www.googleapis.com/auth/gmail.compose']
# Path to your credentials file downloaded from Google Cloud Console
CREDENTIALS_FILE = 'credentials.json'
# Path to store the token after authorization
TOKEN_FILE = 'token.json'
# ID of the root folder in Google Drive where all contract folders will be created.
# You can create this folder manually in Drive and paste its ID here,
# or leave it as None to use the root 'My Drive'.
# Example: ROOT_FOLDER_ID = 'YOUR_ROOT_FOLDER_ID_HERE'
ROOT_FOLDER_ID = None # Set this to a specific Folder ID if desired

def authenticate_gdrive():
    """Shows basic usage of the Drive v3 API.
    Performs OAuth 2.0 authorization and returns an authenticated service object.
    """
    creds = None
    # The file token.json stores the user's access and refresh tokens, and is
    # created automatically when the authorization flow completes for the first
    # time.
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    # If there are no (valid) credentials available, let the user log in.
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception as e:
                print(f"Error refreshing token: {e}. Need to re-authenticate.")
                flow = InstalledAppFlow.from_client_secrets_file(
                    CREDENTIALS_FILE, SCOPES)
                creds = flow.run_local_server(port=0)
        else:
            if not os.path.exists(CREDENTIALS_FILE):
                print(f"Error: Credentials file '{CREDENTIALS_FILE}' not found.")
                print("Please download it from Google Cloud Console and place it in the script directory.")
                return None
            flow = InstalledAppFlow.from_client_secrets_file(
                CREDENTIALS_FILE, SCOPES)
            creds = flow.run_local_server(port=0)
        # Save the credentials for the next run
        with open(TOKEN_FILE, 'w') as token:
            token.write(creds.to_json())
        print(f"Credentials saved to {TOKEN_FILE}")

    try:
        service = build('drive', 'v3', credentials=creds)
        print("Google Drive API service created successfully.")
        return service
    except HttpError as error:
        print(f'An error occurred building the service: {error}')
        return None
    except Exception as e:
        print(f"An unexpected error occurred during authentication: {e}")
        return None

def find_or_create_folder(service, folder_name, parent_folder_id=None):
    """Finds a folder by name or creates it if it doesn't exist.

    Args:
        service: Authorized Google Drive API service instance.
        folder_name: The name of the folder to find or create.
        parent_folder_id: The ID of the parent folder. If None, uses root.

    Returns:
        The ID of the found or created folder, or None if an error occurs.
    """
    query = f"mimeType='application/vnd.google-apps.folder' and trashed=false and name='{folder_name}'"
    if parent_folder_id:
        query += f" and '{parent_folder_id}' in parents"
    else:
         # Search in root if no parent specified
         query += " and 'root' in parents"

    try:
        response = service.files().list(q=query,
                                        spaces='drive',
                                        fields='files(id, name)').execute()
        folders = response.get('files', [])

        if folders:
            print(f"Folder '{folder_name}' found with ID: {folders[0]['id']}")
            return folders[0]['id']
        else:
            print(f"Folder '{folder_name}' not found. Creating...")
            file_metadata = {
                'name': folder_name,
                'mimeType': 'application/vnd.google-apps.folder'
            }
            if parent_folder_id:
                file_metadata['parents'] = [parent_folder_id]

            folder = service.files().create(body=file_metadata,
                                            fields='id').execute()
            print(f"Folder '{folder_name}' created with ID: {folder.get('id')}")
            return folder.get('id')

    except HttpError as error:
        print(f"An error occurred finding/creating folder '{folder_name}': {error}")
        return None
    except Exception as e:
        print(f"An unexpected error occurred with folder '{folder_name}': {e}")
        return None


def get_filename_from_cd(content_disposition):
    """
    Get filename from content-disposition header.
    """
    if not content_disposition:
        return None
    fname = re.findall('filename="?(.+)"?', content_disposition)
    if len(fname) == 0:
        return None
    return fname[0]

def upload_file_to_gdrive(service, file_name, file_content, folder_id):
    """Uploads file content to a specific Google Drive folder.

    Args:
        service: Authorized Google Drive API service instance.
        file_name: The desired name of the file in Google Drive.
        file_content: The binary content of the file.
        folder_id: The ID of the Google Drive folder to upload into.

    Returns:
        The ID of the uploaded file, or None if an error occurs.
    """
    try:
        file_metadata = {
            'name': file_name,
            'parents': [folder_id]
        }
        media = MediaIoBaseUpload(io.BytesIO(file_content),
                                  mimetype='application/octet-stream', # Adjust if mime type is known
                                  resumable=True)
        file = service.files().create(body=file_metadata,
                                      media_body=media,
                                      fields='id').execute()
        print(f"File '{file_name}' uploaded successfully with ID: {file.get('id')}")
        return file.get('id')
    except HttpError as error:
        print(f"An error occurred uploading file '{file_name}': {error}")
        # Handle specific errors like quota exceeded if necessary
        if error.resp.status == 403:
             print("Possible reasons: Quota exceeded, insufficient permissions, or API not enabled correctly.")
        return None
    except Exception as e:
        print(f"An unexpected error occurred uploading file '{file_name}': {e}")
        return None

def check_file_exists_in_drive(service, filename, folder_id):
    """Checks if a file with the given name exists in the specified Drive folder."""
    try:
        # Escape single quotes in filename for the query
        escaped_filename = filename.replace("'", "\\'")
        query = f"name='{escaped_filename}' and '{folder_id}' in parents and trashed=false"
        response = service.files().list(q=query,
                                        spaces='drive',
                                        fields='files(id)',
                                        pageSize=1).execute() # Only need to know if at least one exists
        if response.get('files'):
            # print(f"  File '{filename}' already exists in Drive folder {folder_id}.")
            return True
        return False
    except HttpError as error:
        print(f"  Error checking for file '{filename}' in Drive: {error}")
        return False # Assume not found on error to allow potential upload attempt
    except Exception as e:
        print(f"  Unexpected error checking for file '{filename}': {e}")
        return False

def process_contract_files(service, contract, parent_folder_id):
    """Downloads files from resourceLinks and uploads them to a contract-specific folder in Drive,
       skipping files that already exist."""
    notice_id = contract.get('noticeId')
    resource_links = contract.get('resourceLinks')
    contract_folder_id = None
    contract_folder_link = None

    if not notice_id:
        print("Skipping contract - missing 'noticeId'.")
        return None, None

    print(f"\nProcessing contract files for: {notice_id}")

    contract_folder_id = find_or_create_folder(service, notice_id, parent_folder_id)
    if not contract_folder_id:
        print(f"Failed to create or find folder for contract {notice_id}. Skipping file downloads.")
        return None, None
    else:
        contract_folder_link = get_shareable_link(service, contract_folder_id)
        if not contract_folder_link:
             print(f"Warning: Could not get shareable link for contract folder {notice_id}")

    if resource_links and isinstance(resource_links, list):
        download_count = 0
        upload_count = 0
        skipped_count = 0
        for i, link in enumerate(resource_links):
            filename = None # Reset filename for each link
            try:
                # --- Peek at headers to get filename BEFORE full download ---
                # Use a HEAD request or GET with stream=True and read headers only first
                # This avoids downloading the whole file just to find out it exists.
                # Using GET with stream=True is often more reliable than HEAD for download links.
                with requests.get(link, stream=True, timeout=30) as head_response:
                    head_response.raise_for_status() # Check if link is valid
                    cd = head_response.headers.get('content-disposition')
                    filename = get_filename_from_cd(cd)
                    if not filename:
                        filename = f"{notice_id}_attachment_{i+1}.dat"
                        print(f"  Warning: Could not determine filename from headers. Using fallback: {filename}")
                    else:
                        filename = re.sub(r'[\\/*?:"<>|]', "_", filename) # Clean filename
                        # print(f"  Determined filename: {filename}") # Less verbose

                # --- File Level Cache Check ---
                if filename and check_file_exists_in_drive(service, filename, contract_folder_id):
                    print(f"  Skipping '{filename}': Already exists in Google Drive.")
                    skipped_count += 1
                    continue # Skip to the next link

                # --- Download (if necessary) ---
                print(f"  Downloading file from: {link}")
                # Now perform the full download since it doesn't exist or filename wasn't determined initially
                response = requests.get(link, timeout=120) # Longer timeout for actual download
                response.raise_for_status()

                # Re-determine filename if it wasn't found from headers earlier (less likely now)
                if not filename:
                    cd = response.headers.get('content-disposition')
                    filename = get_filename_from_cd(cd)
                    if not filename:
                        filename = f"{notice_id}_attachment_{i+1}.dat"
                    else:
                        filename = re.sub(r'[\\/*?:"<>|]', "_", filename)

                file_content = response.content
                download_count += 1

                # --- Upload to Google Drive ---
                print(f"  Uploading '{filename}' to Drive folder {notice_id}...")
                if upload_file_to_gdrive(service, filename, file_content, contract_folder_id):
                    upload_count += 1
                else:
                    print(f"  Upload failed for '{filename}'.")


            except requests.exceptions.Timeout:
                 print(f"  Timeout occurred while processing link {i+1} for {notice_id}: {link}")
            except requests.exceptions.RequestException as e:
                print(f"  Error processing file {i+1} for {notice_id} from {link}: {e}")
            except Exception as e:
                print(f"  An unexpected error occurred processing file {i+1} for {notice_id}: {e}")

        print(f"Finished processing files for {notice_id}. Downloads attempted: {download_count}, Uploads successful: {upload_count}, Skipped (already exist): {skipped_count}")
    else:
        print(f"No resource links found for contract {notice_id}. Folder created/found.")

    return contract_folder_id, contract_folder_link


def get_shareable_link(service, folder_id):
    """Gets a shareable link for a folder.

    Args:
        service: Authorized Google Drive API service instance.
        folder_id: The ID of the folder.

    Returns:
        The webViewLink (shareable link) or None if an error occurs.
    """
    if not folder_id:
        return None
    try:
        # Get the folder's metadata, including the webViewLink
        folder = service.files().get(fileId=folder_id, fields='webViewLink').execute()
        link = folder.get('webViewLink')
        if link:
             # Optional: Make the folder public (anyone with the link can view)
             # Be cautious with permissions. Consider your security needs.
             # permission = {
             #     'type': 'anyone',
             #     'role': 'reader'
             # }
             # service.permissions().create(fileId=folder_id, body=permission).execute()
             # print(f"Folder {folder_id} made publicly viewable (anyone with link).")
             return link
        else:
            print(f"Could not retrieve webViewLink for folder {folder_id}.")
            return None
    except HttpError as error:
        print(f"An error occurred getting shareable link for folder {folder_id}: {error}")
        return None
    except Exception as e:
        print(f"An unexpected error occurred getting shareable link for folder {folder_id}: {e}")
        return None 

def download_file_content(service, file_id):
    """Download content of a file from Google Drive."""
    if not service or not file_id:
        return None
        
    try:
        request = service.files().get_media(fileId=file_id)
        fh = io.BytesIO()
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while done is False:
            status, done = downloader.next_chunk()
        return fh.getvalue()
    except Exception as e:
        print(f"Error downloading file {file_id}: {e}")
        return None

def extract_text_from_file_content(filename, file_bytes):
    """Extract text from file content based on file type."""
    if not file_bytes:
        return ""
        
    file_lower = filename.lower()
    extracted_text = ""
    
    try:
        if file_lower.endswith(".txt"):
            # Try different encodings
            try:
                extracted_text = file_bytes.decode('utf-8')
            except UnicodeDecodeError:
                try:
                    extracted_text = file_bytes.decode('latin-1')
                except Exception:
                    return ""
                    
        elif file_lower.endswith(".pdf"):
            try:
                from pypdf import PdfReader
                reader = PdfReader(io.BytesIO(file_bytes))
                text_parts = [page.extract_text() for page in reader.pages if page.extract_text()]
                extracted_text = "\n".join(text_parts)
            except ImportError:
                print("pypdf not installed, skipping PDF extraction")
            except Exception:
                return ""
                
        elif file_lower.endswith(".docx"):
            try:
                import docx
                doc = docx.Document(io.BytesIO(file_bytes))
                text_parts = [para.text for para in doc.paragraphs if para.text]
                extracted_text = "\n".join(text_parts)
            except ImportError:
                print("python-docx not installed, skipping DOCX extraction")
            except Exception:
                return ""
                
        return extracted_text
        
    except Exception:
        return "" 