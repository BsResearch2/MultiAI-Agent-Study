import os, sqlite3, secrets, hmac, csv, io, time
from datetime import datetime, timezone
from functools import wraps
from flask import Flask, request, jsonify, Response, abort, render_template_string
from openai import OpenAI
from agents import AGENTS, BASE, BRIEF

app = Flask(__name__)
DB = os.environ.get("DATABASE_PATH", "research.db")
KEY = os.environ.get("OPENAI_API_KEY", "")
MODEL = os.environ.get("OPENAI_MODEL", "gpt-4.1-mini")
ADMIN = os.environ.get("ADMIN_PASSWORD", "")
def db():
    c = sqlite3.connect(DB, timeout=30)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    return c
def init():
    with db() as c:
        c.executescript("""CREATE TABLE IF NOT EXISTS sessions (
          token TEXT PRIMARY KEY, participant TEXT NOT NULL, refdate TEXT NOT NULL,
          started TEXT NOT NULL, finished TEXT);
          CREATE TABLE IF NOT EXISTS events (
          id INTEGER PRIMARY KEY AUTOINCREMENT, token TEXT NOT NULL,
          agent TEXT, kind TEXT NOT NULL, role TEXT, content TEXT,
          timestamp TEXT NOT NULL, latency_ms INTEGER);
          CREATE INDEX IF NOT EXISTS event_session ON events(token,id);""")
init()
def stamp(): return datetime.now(timezone.utc).isoformat()
def log(token, kind, agent=None, role=None, content=None, latency=None):
    with db() as c:
        c.execute("INSERT INTO events(token,agent,kind,role,content,timestamp,latency_ms) VALUES(?,?,?,?,?,?,?)",
                  (token,agent,kind,role,content,stamp(),latency))
def sess():
    token = request.headers.get("X-Session-Token", "")
    with db() as c: row = c.execute("SELECT * FROM sessions WHERE token=?", (token,)).fetchone()
    if row is None: abort(401)
    return row
def admin_required(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        auth=request.authorization
        if not ADMIN or not auth or not hmac.compare_digest(auth.password or "", ADMIN):
            return Response("Researcher login required", 401, {"WWW-Authenticate": 'Basic realm="Research dashboard"'})
        return fn(*a,**kw)
    return wrapper
@app.get("/")
def home():
    return render_template_string(PARTICIPANT_HTML, brief=BRIEF)
@app.post("/api/start")
def start():
    payload=request.get_json(silent=True) or {}
    pid=str(payload.get("participant","")).strip()
    if not (2<=len(pid)<=50 and all(ch.isalnum() or ch in "-_" for ch in pid)):
        return jsonify(error="Use a 2–50 character participant ID (letters, numbers, - or _)."),400
    token=secrets.token_urlsafe(32)
    # Study date is frozen for the whole participant session.
    ref=datetime.now().astimezone().date().isoformat()
    with db() as c: c.execute("INSERT INTO sessions VALUES(?,?,?,?,?)",(token,pid,ref,stamp(),None))
    log(token,"session_start")
    return jsonify(token=token,reference_date=ref)
@app.get("/api/session")
def restore():
    s=sess()
    with db() as c:
        rows=c.execute("SELECT agent,role,content FROM events WHERE token=? AND kind='message' ORDER BY id",(s["token"],)).fetchall()
    history={k:[] for k in AGENTS}
    for r in rows:
        if r["agent"] in history: history[r["agent"]].append({"role":r["role"],"content":r["content"]})
    return jsonify(participant=s["participant"],finished=bool(s["finished"]),history=history,reference_date=s["refdate"])
@app.post("/api/event")
def event():
    s=sess()
    data=request.get_json(silent=True) or {}
    kind=data.get("kind")
    if kind not in ("focus","finish"): abort(400)
    agent=data.get("agent") if data.get("agent") in AGENTS else None
    log(s["token"],kind,agent)
    if kind=="finish":
        with db() as c: c.execute("UPDATE sessions SET finished=? WHERE token=?",(stamp(),s["token"]))
    return jsonify(ok=True)
@app.post("/api/chat/<agent>")
def chat(agent):
    if agent not in AGENTS: abort(404)
    s=sess()
    if s["finished"]: return jsonify(error="Session already finished."),409
    text=str((request.get_json(silent=True) or {}).get("text","")).strip()
    if not (1<=len(text)<=4000): return jsonify(error="Message must be 1–4000 characters."),400
    if not KEY: return jsonify(error="The researcher has not configured the AI service."),503
    with db() as c:
        hist=c.execute("SELECT role,content FROM events WHERE token=? AND kind='message' AND agent=? ORDER BY id",
                       (s["token"],agent)).fetchall()
    if len(hist)>=100: return jsonify(error="Message limit reached."),429
    log(s["token"],"message",agent,"user",text)
    msgs=[{"role":"system","content":BASE.format(reference_date=s["refdate"])+"\n"+AGENTS[agent]}]
    msgs.extend({"role":r["role"],"content":r["content"]} for r in hist)
    msgs.append({"role":"user","content":text})
    t=time.monotonic()
    try:
        reply=OpenAI(api_key=KEY,timeout=60).chat.completions.create(model=MODEL,messages=msgs).choices[0].message.content or ""
    except Exception:
        app.logger.exception("AI request failed")
        log(s["token"],"model_error",agent)
        return jsonify(error="Agent temporarily unavailable. Try again."),502
    log(s["token"],"message",agent,"assistant",reply,int(1000*(time.monotonic()-t)))
    return jsonify(reply=reply)
@app.get("/researcher")
@admin_required
def researcher(): return render_template_string(RESEARCHER_HTML)
@app.get("/api/researcher/sessions")
@admin_required
def sessions():
    with db() as c:
        rows=c.execute("""SELECT s.participant,s.token,s.refdate,s.started,s.finished,COUNT(e.id) AS event_count
        FROM sessions s LEFT JOIN events e ON e.token=s.token GROUP BY s.token ORDER BY s.started DESC""").fetchall()
    return jsonify(sessions=[dict(r) for r in rows])
@app.get("/api/researcher/session/<token>")
@admin_required
def detail(token):
    with db() as c:
        s=c.execute("SELECT * FROM sessions WHERE token=?",(token,)).fetchone()
        if not s: abort(404)
        ev=c.execute("SELECT agent,kind,role,content,timestamp,latency_ms FROM events WHERE token=? ORDER BY id",(token,)).fetchall()
    return jsonify(session=dict(s),events=[dict(x) for x in ev])
@app.get("/api/researcher/export.csv")
@admin_required
def export():
    with db() as c:
        rows=c.execute("""SELECT s.participant,s.refdate,e.agent,e.kind,e.role,e.content,e.timestamp,e.latency_ms
        FROM events e JOIN sessions s ON e.token=s.token ORDER BY s.started,e.id""").fetchall()
    buff=io.StringIO()
    w=csv.writer(buff)
    w.writerow(["participant","reference_date","agent","event","role","content","utc_timestamp","agent_latency_ms"])
    w.writerows(tuple(r) for r in rows)
    return Response(buff.getvalue(),mimetype="text/csv",
                    headers={"Content-Disposition":"attachment; filename=multi_agent_study_logs.csv"})
PARTICIPANT_HTML = r"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Healthcare Coordination Study</title><style>
*{box-sizing:border-box}body{margin:0;font:15px/1.5 system-ui,sans-serif;background:#f5f7fa;color:#1f2e3d}header{background:#193b50;color:white;padding:18px 28px}h1{font-size:22px;margin:0}main{padding:20px;max-width:1600px;margin:auto}.brief,.agent{background:white;border:1px solid #d9e2e8;border-radius:12px}.brief{padding:16px 20px;margin-bottom:16px}.brief p{margin:8px 0}.setup{display:flex;align-items:center;gap:10px;flex-wrap:wrap}input,textarea,button{font:inherit}input{padding:9px;border:1px solid #bbc7d2;border-radius:7px}button{padding:9px 14px;border:0;border-radius:7px;background:#155e79;color:white;cursor:pointer}button:disabled{opacity:.45;cursor:default}.agents{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}.agent{display:flex;flex-direction:column;height:63vh;min-height:430px}.agent h2{font-size:16px;margin:0}.agent-head{border-bottom:1px solid #d9e2e8;padding:14px}.agent-head small{color:#687b89}.messages{padding:12px;flex:1;overflow:auto;display:flex;flex-direction:column;gap:12px}.bubble{padding:10px 12px;white-space:pre-wrap;overflow-wrap:anywhere;border-radius:10px;background:#edf3f7;max-width:96%}.bubble.user{background:#dceff2;align-self:flex-end}.bubble.assistant{align-self:flex-start}.compose{display:flex;gap:6px;padding:10px;border-top:1px solid #d9e2e8}textarea{flex:1;min-width:0;resize:vertical;min-height:55px;border:1px solid #bbc7d2;border-radius:8px;padding:8px}.hint{color:#596977;font-size:13px}.error{color:#a82626}.busy{opacity:.6}@media(max-width:850px){.agents{grid-template-columns:1fr}.agent{height:440px}} </style></head><body>
<header><h1>Healthcare Coordination Study</h1></header><main><section class="brief"><strong>Case brief</strong><p>{{ brief }}</p><div class="setup" id="setup"><label>Participant ID <input id="pid" maxlength="50" placeholder="P001"></label><button id="begin">Begin session</button><span class="error" id="error"></span></div><div id="active" hidden><span class="hint" id="session_label"></span> <button id="finish">Finish session</button></div></section><section class="agents">
<article class="agent"><div class="agent-head"><h2>Onboarding & Insurance</h2><small>Coverage, eligibility, referrals</small></div><div class="messages" id="m-insurance"></div><form class="compose" data-agent="insurance"><textarea placeholder="Ask the Insurance Agent..." disabled></textarea><button disabled>Send</button></form></article>
<article class="agent"><div class="agent-head"><h2>Scheduling</h2><small>Appointment options and booking</small></div><div class="messages" id="m-scheduling"></div><form class="compose" data-agent="scheduling"><textarea placeholder="Ask the Scheduling Agent..." disabled></textarea><button disabled>Send</button></form></article>
<article class="agent"><div class="agent-head"><h2>Records & Labs</h2><small>Missing records and requests</small></div><div class="messages" id="m-records"></div><form class="compose" data-agent="records"><textarea placeholder="Ask the Records Agent..." disabled></textarea><button disabled>Send</button></form></article>
</section><p class="hint">Fictional case only. Do not enter actual patient information.</p></main><script>
let token=sessionStorage.getItem('study_token'),finished=false;
const names=['insurance','scheduling','records'];
const headers=()=>({'Content-Type':'application/json','X-Session-Token':token||''});
async function api(path,method='GET',data){const r=await fetch(path,{method,headers:headers(),body:data?JSON.stringify(data):undefined});const p=await r.json().catch(()=>({}));if(!r.ok)throw Error(p.error||'Request failed');return p}
function bubble(agent,role,text){const wrap=document.getElementById('m-'+agent);const el=document.createElement('div');el.className='bubble '+role;el.textContent=text;wrap.append(el);wrap.scrollTop=wrap.scrollHeight}
function setActive(pid){document.getElementById('setup').hidden=true;document.getElementById('active').hidden=false;document.getElementById('session_label').textContent='Participant: '+pid;document.querySelectorAll('form.compose textarea,form.compose button').forEach(x=>x.disabled=finished)}
document.getElementById('begin').onclick=async()=>{try{const p=await api('/api/start','POST',{participant:document.getElementById('pid').value});token=p.token;sessionStorage.setItem('study_token',token);setActive(document.getElementById('pid').value)}catch(e){document.getElementById('error').textContent=e.message}};
document.querySelectorAll('form.compose').forEach(form=>{const agent=form.dataset.agent,area=form.querySelector('textarea'),button=form.querySelector('button');area.addEventListener('focus',()=>{if(token)api('/api/event','POST',{kind:'focus',agent}).catch(()=>{})});area.addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();form.requestSubmit()}});form.onsubmit=async e=>{e.preventDefault();const text=area.value.trim();if(!text||area.disabled)return;area.value='';bubble(agent,'user',text);area.disabled=button.disabled=true;try{const p=await api('/api/chat/'+agent,'POST',{text});bubble(agent,'assistant',p.reply)}catch(err){bubble(agent,'assistant','Error: '+err.message)}finally{if(!finished)area.disabled=button.disabled=false;area.focus()}}});
document.getElementById('finish').onclick=async()=>{if(!confirm('Finish this session? You will not be able to send additional messages.'))return;try{await api('/api/event','POST',{kind:'finish'});finished=true;document.querySelectorAll('form.compose textarea,form.compose button').forEach(x=>x.disabled=true);document.getElementById('finish').disabled=true}catch(e){alert(e.message)}};
if(token){api('/api/session').then(p=>{finished=p.finished;setActive(p.participant);for(const agent of names)for(const m of p.history[agent])bubble(agent,m.role,m.content);if(finished)document.getElementById('finish').disabled=true}).catch(()=>{sessionStorage.removeItem('study_token');token=null})}
</script></body></html>"""
RESEARCHER_HTML = r"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Researcher Dashboard</title><style>body{font:15px/1.5 system-ui,sans-serif;max-width:1250px;margin:25px auto;padding:0 18px;color:#1f2e3d}button{cursor:pointer;border:1px solid #ccd6dc;background:white;padding:10px;margin:4px;border-radius:6px;text-align:left}main{display:grid;grid-template-columns:1fr 2fr;gap:20px}.event{padding:12px 0;border-bottom:1px solid #ddd;white-space:pre-wrap;overflow-wrap:anywhere}.meta{color:#657789;font-size:12px}@media(max-width:700px){main{grid-template-columns:1fr}}</style></head><body><h1>Multi-agent study · Researcher logs</h1><p><a href="/api/researcher/export.csv">Download all logs as CSV</a></p><main><section><h2>Sessions</h2><div id="sessions">Loading...</div></section><section><h2>Timeline</h2><div id="timeline">Select a session</div></section></main><script>
async function get(url){const r=await fetch(url);if(!r.ok)throw Error('Could not fetch data');return r.json()}
async function openSession(token){const d=await get('/api/researcher/session/'+encodeURIComponent(token));const box=document.getElementById('timeline');box.replaceChildren();for(const e of d.events){const row=document.createElement('div');row.className='event';const meta=document.createElement('div');meta.className='meta';meta.textContent=e.timestamp+' · '+(e.agent||'session')+' · '+e.kind+(e.role?' · '+e.role:'');row.append(meta);if(e.content){const content=document.createElement('div');content.textContent=e.content;row.append(content)}box.append(row)}}
get('/api/researcher/sessions').then(d=>{const box=document.getElementById('sessions');box.replaceChildren();for(const s of d.sessions){const btn=document.createElement('button');btn.textContent=s.participant+' · '+s.event_count+' events · '+(s.finished?'Finished':'Open');btn.onclick=()=>openSession(s.token);box.append(btn)}if(!d.sessions.length)box.textContent='No sessions yet'}).catch(e=>document.getElementById('sessions').textContent=e.message);
</script></body></html>"""
if __name__=="__main__":
    app.run(port=int(os.environ.get("PORT","8000")),debug=False)
