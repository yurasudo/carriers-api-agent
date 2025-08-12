````markdown
# PostNL Carrier API Agent

A small utility to query the PostNL "Status by Reference" API and check shipment status.

## Overview
- Reads PostNL API docs (for reference only)
- Generates or uses a Python script to call the API
- Executes the script and validates the response
- Saves the output and generated code into the `artifacts/` directory

## Requirements
- Python 3.9+
- Install dependencies:
  pip install requests openai
````

## How to run

Set environment variables:

```bash
export OPENAI_API_KEY="sk-..."   # without this, the script will use fallback mode
export POSTNL_APIKEY="..."
export POSTNL_CUSTOMER_CODE="..."
export POSTNL_CUSTOMER_NUMBER="..."
export POSTNL_REFERENCE="..."
export POSTNL_BASE_URL="https://api-sandbox.postnl.nl"
```

Run the script:

```bash
python carrier_api_agent.py
```

## Notes

* If `OPENAI_API_KEY` is missing or OpenAI API fails (no credits, rate limit, etc.), the script falls back to the built-in Python client.
* All outputs and generated code are stored in the `artifacts/` folder.# carriers-api-agent
