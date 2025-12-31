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


# ===================
# UPDATE ENDPOINT
# ===================
@app.route('/update', methods=['POST'])
def update():
    """Process job updates - parse email and return structured data"""
    try:
        data = request.get_json()
        email_content = data.get('emailContent', '')
        job_number = data.get('jobNumber', '')
        
        # Optional: current job context from Traffic
        current_stage = data.get('currentStage', 'Unknown')
        current_status = data.get('currentStatus', 'Unknown')
        with_client = data.get('withClient', False)
        current_update = data.get('currentUpdate', 'None')
        project_name = data.get('projectName', 'Unknown')
        
        if not email_content:
            return jsonify({'error': 'No email content provided'}), 400
        
        if not job_number:
            return jsonify({'error': 'No job number provided'}), 400
        
        # Build context for Claude
        today = date.today()
        current_context = f"""
Today's date: {today.strftime('%A, %d %B %Y')}

Current job data:
- Job Number: {job_number}
- Project Name: {project_name}
- Stage: {current_stage}
- Status: {current_status}
- With Client: {with_client}
- Current Update: {current_update}
"""
        
        # Call Claude with Update prompt
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
        
        # Return parsed data for Power Automate to act on
        return jsonify({
            'jobNumber': job_number,
            'update': analysis.get('updateSummary', ''),
            'updateDue': update_due,
            'stage': analysis.get('stage'),
            'status': analysis.get('status'),
            'withClient': analysis.get('withClient'),
            'hasBlocker': analysis.get('hasBlocker', False),
            'blockerNote': analysis.get('blockerNote'),
            'confidence': analysis.get('confidence', 'MEDIUM'),
            'confidenceNote': analysis.get('confidenceNote'),
            'teamsMessage': analysis.get('teamsMessage', {})
        })
        
    except json.JSONDecodeError as e:
        return jsonify({
            'error': 'Claude returned invalid JSON',
            'details': str(e),
            'raw_response': content
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
        'endpoints': ['/update', '/health']
    })


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)
