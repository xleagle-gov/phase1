# XL Eagle Phase 1: opportunity discovery

## What it does
Phase 1 of XL Eagle's government-contracting pipeline: it finds bid opportunities and turns them into qualified leads. It pulls federal solicitations from the SAM.gov Opportunities API and local solicitations (Texas ESBD, Louisiana, Bonfire portals), downloads the solicitation documents to Google Drive, and uses an LLM to summarize and qualify each one into the lead-tracking spreadsheet.

## How it works
- `federal_contracts_main.py`, `main.py`: fetch and cache SAM.gov opportunities, filter by contract and set-aside type, and process each solicitation.
- `run_local_contracts.py`, `localContracts_texas.py`, `localContracts_la.py`, `download_esbd_files.py`, `bonfire_downloader.py`: state and local sources.
- `generateLeads.py`, `download_sam_files.py`, `google_drive_utils.py`, `backfillfolderLinks.py`: document download, Drive upload, and sheet updates.
- `gemini.py`, `services/openai_service.py`, `promptv3.txt`: LLM qualification prompts and clients.
- `runN8nFlows.py`: triggers downstream n8n automation flows.
- `config.py`, `.env.example`: configuration. Keys belong in `.env`, never in code.

## Running it
```bash
cp .env.example .env   # fill in your own keys
python federal_contracts_main.py
python run_local_contracts.py
```
