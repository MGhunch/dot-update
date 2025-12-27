# Dot Update
# Status updates for Hunch agency jobs
# Standalone version - no external dependencies

import os
import json
from datetime import date, timedelta
from flask import Flask, request, jsonify
from anthropic import Anthropic
import httpx

app = Flask(__name__)

# ===================
# CONFIG
# ===================

AIRTABLE_API_KEY = os.environ.get('AIRTABLE_API_KEY')
AIRTABLE_BASE_ID = 'app8CI7NAZqhQ4G1Y'
AIRTABLE_PROJECTS_TABLE = 'Projects'
AIRTABLE_UPDATES_TABLE = 'Updates'

ANTHROPIC_API_KEY = os.environ.get('ANTHROPIC_API_KEY')
ANTHROPIC_MODEL = 'claude-sonnet-4-20250514'

# Anthropic client
anthropic_client = Anthropic(
    api_key=ANTHROPIC_API_KEY,
    http_client=httpx.Client(timeout=60.0, follow_redirects=True)
)

# Load prompt
PROMPT_PATH = os.path.join(os.path.dirname(__file__), 'prompt.txt')
with open(PROMPT_PATH, 'r') as f:
    UPDATE_PROMPT = f.read()


# ===================
# HELPERS
# ===================

def strip_markdown_json(content):
    """Strip markdown code blocks from Claude's JSON response"""
    content = content.strip()
    if content.startswith('```'):
        content = content.split('\n', 1)[1] if '\n' in content else content[3:]
    if content.endswith('```'):
        content = content.rsplit('```', 1)[0]
    return content.strip()


def _get_airtable_headers():
    """Get standard Airtable headers"""
    return {
        'Authorization': f'Bearer {AIRTABLE_API_KEY}',
        'Content-Type': 'application/json'
    }


def get_next_working_day(start_date, days=5):
    """Add working days (skipping weekends) to a date."""
    current = start_date
    added = 0
    while added < days:
        current += timedelta(days=1)
        if current.weekday() < 5:  # Monday = 0, Friday = 4
            added += 1
    return current


# ===================
# AIRTABLE FUNCTIONS
# ===================

def get_project_by_job_number(job_number):
    """Look up existing project by job number."""
    if not AIRTABLE_API_KEY:
        print("No Airtable API key configured")
        return None
    
    try:
        search_url = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}/{AIRTABLE_PROJECTS_TABLE}"
        params = {'filterByFormula': f"{{Job Number}}='{job_number}'"}
        
        response = httpx.get(search_url, headers=_get_airtable_headers(), params=params, timeout=10.0)
        response.raise_for_status()
        
        records = response.json().get('records', [])
        
        if not records:
            print(f"Job '{job_number}' not found in Airtable")
            return None
        
        record = records[0]
        fields = record['fields']
        
        # Get client name from linked record if available
        client_name = fields.get('Client', '')
        if isinstance(client_name, list):
            client_name = client_name[0] if client_name else ''
        
        return {
            'recordId': record['id'],
            'jobNumber': fields.get('Job Number', job_number),
            'jobName': fields.get('Project Name', ''),
            'clientName': client_name,
            'stage': fields.get('Stage', ''),
            'status': fields.get('Status', ''),
            'round': fields.get('Round', 0) or 0,
            'withClient': fields.get('With Client?', False),
            'teamsChannelId': fields.get('Teams Channel ID', None)
        }
        
    except Exception as e:
        print(f"Error looking up project in Airtable: {e}")
        return None


def create_update(project_record_id, update_text, update_due=None):
    """Create a new update record in the Updates table.
    
    Defaults to 5 working days for due date if not specified.
    """
    if not AIRTABLE_API_KEY:
        print("No Airtable API key configured")
        return False
    
    try:
        # Default to 5 working days if no due date provided
        if not update_due:
            update_due = get_next_working_day(date.today(), 5).isoformat()
        
        update_data = {
            'fields': {
                'Project Link': [project_record_id],
                'Update': update_text,
                'Updated on': date.today().isoformat(),
                'Update due': update_due
            }
        }
        
        create_url = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}/{AIRTABLE_UPDATES_TABLE}"
        response = httpx.post(create_url, headers=_get_airtable_headers(), json=update_data, timeout=10.0)
        response.raise_for_status()
        
        print(f"Created update for project {project_record_id}: {update_text}")
        return True
        
    except Exception as e:
        print(f"Error creating update in Airtable: {e}")
        return False


def update_project_fields(job_number, updates):
    """Update specific fields on a Project record.
    
    Used for Stage, Status, Live Date, With Client changes.
    NOT for Update field - that's a lookup from Updates table.
    """
    if not AIRTABLE_API_KEY:
        print("No Airtable API key configured")
        return False
    
    try:
        # First find the record
        project = get_project_by_job_number(job_number)
        
        if not project:
            return False
        
        # Build update payload - only include valid fields
        field_mapping = {
            'Stage': 'Stage',
            'Status': 'Status',
            'Live Date': 'Live Date',
            'With Client?': 'With Client?'
        }
        
        update_fields = {}
        for key, airtable_field in field_mapping.items():
            if key in updates and updates[key] is not None:
                update_fields[airtable_field] = updates[key]
        
        if not update_fields:
            print("No project fields to update")
            return True
        
        update_url = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}/{AIRTABLE_PROJECTS_TABLE}/{project['recordId']}"
        update_data = {'fields': update_fields}
        
        response = httpx.patch(update_url, headers=_get_airtable_headers(), json=update_data, timeout=10.0)
        response.raise_for_status()
        
        print(f"Updated project {job_number}: {update_fields}")
        return True
        
    except Exception as e:
        print(f"Error updating project in Airtable: {e}")
        return False


# ===================
# UPDATE ENDPOINT
# ===================

@app.route('/update', methods=['POST'])
def update():
    """Process job updates.
    
    Accepts:
        - jobNumber: The job to update
        - emailContent: The update message/email
    
    Returns:
        - teamsPost: Formatted message for Teams
        - airtableUpdate: What was written to Updates table
        - updateCreated: Boolean success flag
        - projectUpdated: Boolean if project fields changed
    """
    try:
        data = request.get_json()
        
        job_number = data.get('jobNumber')
        email_content = data.get('emailContent', '')
        
        if not job_number:
            return jsonify({'error': 'No job number provided'}), 400
        
        if not email_content:
            return jsonify({'error': 'No email content provided'}), 400
        
        # Get project details from Airtable
        project = get_project_by_job_number(job_number)
        
        if not project:
            return jsonify({
                'error': 'job_not_found',
                'jobNumber': job_number,
                'message': f"Could not find job {job_number} in the system"
            }), 404
        
        # Build content for Claude
        update_content = f"""Job Number: {job_number}
Job Name: {project['jobName']}
Client Name: {project['clientName']}
Current Stage: {project['stage']}
Email/Message Content:
{email_content}"""
        
        # Call Claude for update analysis
        response = anthropic_client.messages.create(
            model=ANTHROPIC_MODEL,
            max_tokens=1500,
            temperature=0.2,
            system=UPDATE_PROMPT,
            messages=[
                {'role': 'user', 'content': update_content}
            ]
        )
        
        # Parse response
        content = response.content[0].text
        content = strip_markdown_json(content)
        analysis = json.loads(content)
        
        # Check for errors from Claude
        if analysis.get('error'):
            return jsonify(analysis), 400
        
        # Get the update text
        update_text = analysis.get('airtableUpdate', '')
        
        # Get due date from analysis (or let create_update default to 5 working days)
        update_due = None
        if analysis.get('projectUpdates', {}).get('Update due'):
            update_due = analysis['projectUpdates']['Update due']
        
        # Create the update record in Updates table
        update_created = False
        if update_text:
            update_created = create_update(
                project_record_id=project['recordId'],
                update_text=update_text,
                update_due=update_due
            )
        
        # Update Project fields if needed (Stage, Status, Live Date, With Client)
        project_updated = False
        if analysis.get('projectUpdates'):
            # Remove Update and Update due - those go to Updates table
            project_fields = {k: v for k, v in analysis['projectUpdates'].items() 
                           if k not in ['Update', 'Update due']}
            if project_fields:
                project_updated = update_project_fields(job_number, project_fields)
        
        # Add results to response
        analysis['jobNumber'] = job_number
        analysis['jobName'] = project['jobName']
        analysis['updateCreated'] = update_created
        analysis['projectUpdated'] = project_updated
        analysis['teamsChannelId'] = project['teamsChannelId']
        analysis['projectRecordId'] = project['recordId']
        
        return jsonify(analysis)
        
    except json.JSONDecodeError as e:
        return jsonify({
            'error': 'Claude returned invalid JSON',
            'details': str(e),
            'raw_response': content if 'content' in locals() else 'No response'
        }), 500
    except Exception as e:
        return jsonify({
            'error': 'Internal server error',
            'details': str(e)
        }), 500


# ===================
# HEALTH CHECK
# ===================

@app.route('/health', methods=['GET'])
def health():
    """Health check endpoint"""
    return jsonify({
        'status': 'healthy',
        'service': 'Dot Update',
        'version': '2.0'
    })


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)
