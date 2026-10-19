# Multi-AI Agent Coordination Study

**Separate private research repository**. No files or deployment resources are shared with the algorithmic-control experiment.

## Prototype features
- Three agent chat panels displayed side by side: Insurance, Scheduling, and Records & Labs
- Independent per-agent conversation histories with no automatic sharing between agents
- Fictional Jordan Smith case, fixed per-session reference date, relative date calculation
- Participant ID entry; no participant login
- Researcher-only `/researcher` dashboard to review chronological sessions
- Logged participant and agent messages, timestamps, focus events, and AI latency
- CSV export at `/api/researcher/export.csv`

## Run locally
Requires Python 3.11+:
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export OPENAI_API_KEY="your_key"
export ADMIN_PASSWORD="a_long_unique_password"
export DATABASE_PATH="./research.db"
python app.py
```
Open `http://localhost:8000` for participant view and `http://localhost:8000/researcher` for researcher dashboard. On Windows use the corresponding PowerShell environment variable syntax.

## Deploy for participants
GitHub **does not host this Flask application**. Connect this repository to a Python web host (for example, Render or Railway). Configure a web service with:

- Build: `pip install -r requirements.txt`
- Start: `gunicorn app:app --bind 0.0.0.0:$PORT --workers 1 --timeout 90`
- Environment secrets: `OPENAI_API_KEY`, `ADMIN_PASSWORD`
- Optional model: `OPENAI_MODEL=gpt-4.1-mini`
- Persistent volume/disk mounted at `/data`, with `DATABASE_PATH=/data/research.db`
- Server date/time zone consistent with study protocol, e.g. `TZ=America/New_York`

The participant URL is the host's root URL. The private researcher dashboard is `/researcher`. Both should use HTTPS. Do not put API keys in GitHub, HTML or JavaScript.

## Study design
Participant brief and all local agent instructions are in `agents.py`. Insurance knows coverage; Scheduling knows available appointments; Records knows missing documents and simulated processing duration. Each receives only its own history; the global solution is not supplied to the agents. Booking is **simulated**, never a real appointment.

## Before live data collection
**Not production-ready.** Obtain applicable institutional IRB/security approval and test thoroughly. Implement or verify:
- consent and disclosure before any logging, permitted data retention, deletion and secure backups;
- strong researcher authentication (prototype uses HTTP Basic with a password);
- abuse protection / rate limiting, participant access management and monitoring of API costs;
- a reliable persistent database with access controls and backup/restore;
- deterministic appointment and records state transitions if study outcomes must be objectively auditable;
- cross-device session recovery if participants might close the tab; current token is kept in `sessionStorage`;
- pilot tests for hallucinations, separation of agent knowledge, timestamp fidelity and browser behavior.

The application logs text chat and agent focus events, not spoken think-aloud audio. For concurrent think-aloud, record audio separately with an approved procedure.
