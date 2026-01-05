from flask import Flask, request, jsonify
from anthropic import Anthropic
import httpx
import json
import os
from datetime import datetime, date, timedelta

app = Flask(__name__)

# Custom HTTP client for Anthropic
custom_http_client = httpx.Client(
    timeout=60.0,
    follow_redirects=True
)
  
client = Anthropic(
    api_key=os.environ.get('ANTHROPIC_API_KEY'),
    http_client=custom_http_client
)

# Airtable config
AIRTABLE_API_KEY = os.environ.get('AIRTABLE_API_KEY')
AIRTABLE_BASE_ID = 'app8CI7NAZqhQ4G1Y'
AIRTABLE_PROJECTS_TABLE = 'Projects'
AIRTABLE_UPDATES_TABLE = 'Updates'

# Load prompt from file
with open('prompt.txt', 'r') as f:
    UPDATE_PROMPT = f.read()


def strip_markdown_json(content):
    """Strip markdown code blocks from Claude's JSON response"""
    content = content.strip()
    if content.startswith('```'):
        content = content.split('\n', 1)[1] if '\n' in content else content[3:]
    if content.endswith('```'):
        content = content.rsplit('```', 1)[0]
    return content.strip()


def get_working_days_from_today(days=5):
    """Calculate a date N working days from today"""
    current = date.today()
    added = 0
    while added < days:
        current += timedelta(days=1)
        if current.weekday() < 5:  # Monday = 0, Friday = 4
            added += 1
    return current.isoformat()


def extract_team_id_from_url(channel_url):
    """Extract the groupId (Team ID) from a Teams channel URL"""
    if not channel_url:
        return None
    try:
        # Look for groupId= in the URL
        import re
        match = re.search(r'groupId=([a-f0-9-]+)', channel_url)
        if match:
            return match.group(1)
        return None
    except:
        return None


def lookup_job_in_airtable(job_number):
    """Look up a job by job number in Airtable Projects table"""
    if not AIRTABLE_API_KEY:
        return None, None, "No Airtable API key configured"
    
    try:
        headers = {
            'Authorization': f'Bearer {AIRTABLE_API_KEY}',
            'Content-Type': 'application/json'
        }
        
        # Search for the job number
        filter_formula = f"{{Job Number}}='{job_number}'"
        url = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}/{AIRTABLE_PROJECTS_TABLE}"
        params = {'filterByFormula': filter_formula}
        
        response = httpx.get(url, headers=headers, params=params, timeout=10.0)
        response.raise_for_status()
        
        records = response.json().get('records', [])
        
        if not records:
            return None, None, f"Job '{job_number}' not found in Airtable"
        
        record = records[0]
        record_id = record['id']
        fields = record['fields']
        
        # Extract Team ID from Channel URL
        channel_url = fields.get('Channel Url', '')
        team_id = extract_team_id_from_url(channel_url)
        
        project_info = {
            'recordId': record_id,
            'projectName': fields.get('Project Name', 'Unknown'),
            'stage': fields.get('Stage', 'Unknown'),
            'status': fields.get('Status', 'Unknown'),
            'withClient': fields.get('With Client?', False),
            'currentUpdate': fields.get('Update', 'None'),
            'channelId': fields.get('Teams Channel ID', None),
            'teamId': team_id
        }
        
        return record_id, project_info, None
        
    except Exception as e:
        return None, None, f"Error looking up job: {str(e)}"


def write_update_to_airtable(job_record_id, update_text, update_due):
    """Write an update to the Airtable Updates table"""
    if not AIRTABLE_API_KEY:
        return None, "No Airtable API key configured"
    
    try:
        headers = {
            'Authorization': f'Bearer {AIRTABLE_API_KEY}',
            'Content-Type': 'application/json'
        }
        
        # Build the update record
        update_data = {
            'fields': {
                'Project Link': [job_record_id],
                'Update': update_text
            }
        }
        
        # Add due date if provided
        if update_due:
            update_data['fields']['Update due'] = update_due
        
        # Create the record
        url = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}/{AIRTABLE_UPDATES_TABLE}"
        response = httpx.post(url, headers=headers, json=update_data, timeout=10.0)
        response.raise_for_status()
        
        new_record = response.json()
        return new_record.get('id'), None
        
    except Exception as e:
        return None, f"Error writing update: {str(e)}"


def update_project_in_airtable(job_record_id, stage=None, status=None, with_client=None):
    """Update the project record with new stage/status/withClient values"""
    if not AIRTABLE_API_KEY:
        return False, "No Airtable API key configured"
    
    try:
        headers = {
            'Authorization': f'Bearer {AIRTABLE_API_KEY}',
            'Content-Type': 'application/json'
        }
        
        # Build update fields (only include changed values)
        fields = {}
        if stage and stage != 'Unknown':
            fields['Stage'] = stage
        if status and status != 'Unknown':
            fields['Status'] = status
        if with_client is not None:
            fields['With Client?'] = with_client
        
        if not fields:
            return True, None  # Nothing to update
        
        # Update the record
        url = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}/{AIRTABLE_PROJECTS_TABLE}/{job_record_id}"
        response = httpx.patch(url, headers=headers, json={'fields': fields}, timeout=10.0)
        response.raise_for_status()
        
        return True, None
        
    except Exception as e:
        return False, f"Error updating project: {str(e)}"


# ===================
# UPDATE ENDPOINT
# ===================
@app.route('/update', methods=['POST'])
def update():
    """Process job updates - parse email, write to Airtable, return result"""
    try:
        data = request.get_json()
        email_content = data.get('emailContent', '')
        job_number = data.get('jobNumber', '')
        
        if not email_content:
            return jsonify({
                'success': False,
                'failReason': 'No email content provided',
                'jobNumber': job_number
            }), 400
        
        if not job_number:
            return jsonify({
                'success': False,
                'failReason': 'No job number provided',
                'jobNumber': job_number
            }), 400
        
        # Step 1: Look up job in Airtable
        job_record_id, project_info, lookup_error = lookup_job_in_airtable(job_number)
        
        if lookup_error:
            return jsonify({
                'success': False,
                'failReason': lookup_error,
                'jobNumber': job_number,
                'teamsMessage': {
                    'subject': f'UPDATE FAILED: {job_number}',
                    'body': f'Could not log update - {lookup_error}'
                }
            })
        
        # Step 2: Build context for Claude
        today = date.today()
        current_context = f"""
Today's date: {today.strftime('%A, %d %B %Y')}

Current job data:
- Job Number: {job_number}
- Project Name: {project_info['projectName']}
- Stage: {project_info['stage']}
- Status: {project_info['status']}
- With Client: {project_info['withClient']}
- Current Update: {project_info['currentUpdate']}
"""
        
        # Step 3: Call Claude with Update prompt
        response = client.messages.create(
            model='claude-sonnet-4-20250514',
            max_tokens=1500,
            temperature=0.2,
            system=UPDATE_PROMPT,
            messages=[
                {'role': 'user', 'content': f'{current_context}\n\nEmail content:\n\n{email_content}'}
            ]
        )
        
        # Parse Claude's JSON response
        content = response.content[0].text
        content = strip_markdown_json(content)
        analysis = json.loads(content)
        
        # Ensure update_due is always set
        update_due = analysis.get('updateDue') or get_working_days_from_today(5)
        update_summary = analysis.get('updateSummary', '')
        
        # Step 4: Write update to Airtable Updates table
        update_record_id, write_error = write_update_to_airtable(
            job_record_id, 
            update_summary, 
            update_due
        )
        
        if write_error:
            return jsonify({
                'success': False,
                'failReason': write_error,
                'jobNumber': job_number,
                'teamsMessage': {
                    'subject': f'UPDATE FAILED: {job_number}',
                    'body': f'Could not write update - {write_error}'
                }
            })
        
        # Step 5: Update project record if stage/status/withClient changed
        new_stage = analysis.get('stage')
        new_status = analysis.get('status')
        new_with_client = analysis.get('withClient')
        
        # Only update if values changed from current
        stage_to_update = new_stage if new_stage != project_info['stage'] else None
        status_to_update = new_status if new_status != project_info['status'] else None
        with_client_to_update = new_with_client if new_with_client != project_info['withClient'] else None
        
        if stage_to_update or status_to_update or with_client_to_update is not None:
            update_project_in_airtable(
                job_record_id,
                stage=stage_to_update,
                status=status_to_update,
                with_client=with_client_to_update
            )
        
        # Step 6: Return success with Teams message
        return jsonify({
            'success': True,
            'jobNumber': job_number,
            'projectName': project_info['projectName'],
            'update': update_summary,
            'updateDue': update_due,
            'updateRecordId': update_record_id,
            'stage': new_stage,
            'status': new_status,
            'withClient': new_with_client,
            'hasBlocker': analysis.get('hasBlocker', False),
            'blockerNote': analysis.get('blockerNote'),
            'confidence': analysis.get('confidence', 'MEDIUM'),
            'confidenceNote': analysis.get('confidenceNote'),
            'channelId': project_info.get('channelId'),
            'teamId': project_info.get('teamId'),
            'teamsMessage': analysis.get('teamsMessage', {
                'subject': f'UPDATE: {job_number}',
                'body': update_summary
            })
        })
        
    except json.JSONDecodeError as e:
        return jsonify({
            'success': False,
            'failReason': f'Claude returned invalid JSON: {str(e)}',
            'jobNumber': job_number,
            'teamsMessage': {
                'subject': f'UPDATE FAILED: {job_number}',
                'body': 'Could not process update - invalid response from AI'
            }
        }), 500
    except Exception as e:
        return jsonify({
            'success': False,
            'failReason': f'Internal error: {str(e)}',
            'jobNumber': job_number if 'job_number' in dir() else 'Unknown',
            'teamsMessage': {
                'subject': 'UPDATE FAILED',
                'body': f'Could not process update - {str(e)}'
            }
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
        'endpoints': ['/update', '/health']
    })


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)
