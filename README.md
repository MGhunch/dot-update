# Dot Update

Status updates for Hunch agency jobs.

## What it does

Processes status updates and logs them:
- Extracts update type (stage change, status, live date, etc.)
- Creates record in Updates table
- Updates Project fields if needed
- Returns formatted Teams post

## Endpoint

`POST /update`

### Input

```json
{
  "jobNumber": "TOW 087",
  "emailContent": "Moving to Craft, due Friday, live date 20 Jan"
}
```

### Output

```json
{
  "updateTypes": ["stage", "due_date", "live_date"],
  "airtableUpdate": "Moving to Craft. Due Fri. Live 20 Jan.",
  "teamsPost": "UPDATE | Moving to Craft. Due Fri. Live 20 Jan.",
  "projectUpdates": {
    "Stage": "Craft",
    "Live Date": "2025-01-20"
  },
  "updateCreated": true,
  "projectUpdated": true
}
```

## Environment Variables

- `ANTHROPIC_API_KEY` - Claude API key
- `AIRTABLE_API_KEY` - Airtable API key

## Deployment

Deploy to Railway. Add environment variables. Done.
