"""Local, simulated tools for independent healthcare agents.

Each tool is scoped to one role. No external systems or cross-agent state are read.
All results are fictional. State is durable only for as long as the configured
SQLite database persists (Render free ephemeral disks are NOT durable).
"""
import json
import sqlite3
from datetime import date, timedelta

TOOLS = {
    "insurance": [
        {"type":"function","function":{"name":"verify_eligibility","description":"Independently verify Jordan's insurance and registration status and record the result.","parameters":{"type":"object","properties":{},"additionalProperties":False}}},
        {"type":"function","function":{"name":"verify_referral","description":"Check referral and authorization requirements; persist the verification.","parameters":{"type":"object","properties":{},"additionalProperties":False}}},
        {"type":"function","function":{"name":"check_facility_coverage","description":"Check coverage for a specified facility and save the local check.","parameters":{"type":"object","properties":{"facility":{"type":"string","enum":["Facility A","Facility B","Facility C"]}},"required":["facility"],"additionalProperties":False}}}
    ],
    "scheduling": [
        {"type":"function","function":{"name":"search_appointments","description":"Search the local appointment inventory and patient availability; independently shortlist viable scheduling options.","parameters":{"type":"object","properties":{},"additionalProperties":False}}},
        {"type":"function","function":{"name":"propose_appointment","description":"Store a recommended appointment as a proposal, NOT a reservation. Requires facility.","parameters":{"type":"object","properties":{"facility":{"type":"string","enum":["Facility A","Facility B","Facility C"]}},"required":["facility"],"additionalProperties":False}}},
        {"type":"function","function":{"name":"book_appointment","description":"Book a simulated appointment only after the participant explicitly authorizes booking and has explicitly reported both facility coverage and records readiness. Supply exact quotes of the participant's statements as evidence; cannot independently check either fact.","parameters":{"type":"object","properties":{"facility":{"type":"string","enum":["Facility A","Facility B","Facility C"]},"coverage_quote":{"type":"string"},"records_quote":{"type":"string"},"authorization_quote":{"type":"string"}},"required":["facility","coverage_quote","records_quote","authorization_quote"],"additionalProperties":False}}},
        {"type":"function","function":{"name":"cancel_appointment","description":"Cancel a currently booked simulated appointment only when the participant explicitly requests cancellation; provide exact quote.","parameters":{"type":"object","properties":{"authorization_quote":{"type":"string"}},"required":["authorization_quote"],"additionalProperties":False}}}
    ],
    "records": [
        {"type":"function","function":{"name":"review_documentation","description":"Review required documents and identify missing ones without waiting for detailed human instructions.","parameters":{"type":"object","properties":{},"additionalProperties":False}}},
        {"type":"function","function":{"name":"request_missing_record","description":"Independently initiate the routine SIMULATED missing medical-record request and save its status; no external request occurs.","parameters":{"type":"object","properties":{},"additionalProperties":False}}},
        {"type":"function","function":{"name":"check_records_timeline","description":"Determine whether a specified appointment date is later than the expected records availability date. Cannot determine insurance or scheduling status.","parameters":{"type":"object","properties":{"appointment_date":{"type":"string","description":"Calendar date YYYY-MM-DD provided by the participant"}},"required":["appointment_date"],"additionalProperties":False}}}
    ],
}

SLOTS = {"Facility A": (2, "09:00"), "Facility B": (4, "11:00"), "Facility C": (6, "14:00")}
COVERED = {"Facility A":False,"Facility B":True,"Facility C":True}

def _day(ref, offset):
    return (date.fromisoformat(ref) + timedelta(days=offset)).isoformat()

def load_state(conn, token, agent):
    row=conn.execute("SELECT state FROM agent_state WHERE token=? AND agent=?", (token,agent)).fetchone()
    return json.loads(row["state"]) if row else {}

def _save(conn, token, agent, state):
    conn.execute("INSERT INTO agent_state(token,agent,state) VALUES(?,?,?) ON CONFLICT(token,agent) DO UPDATE SET state=excluded.state",
                 (token,agent,json.dumps(state,sort_keys=True)))

def _quote_in_user_messages(quote, user_messages):
    q=quote.strip()
    return len(q)>=4 and any(q.casefold() in item.casefold() for item in user_messages)

def _normalize(text):
    return " ".join(text.lower().replace("-", " ").split())

def execute(conn, token, agent, refdate, name, args, user_messages, current_user_message):
    """Return a tool result; changes to state are committed by caller's transaction."""
    permitted={item["function"]["name"] for item in TOOLS[agent]}
    if name not in permitted:
        return {"error":"Action not permitted for this agent"}
    state=load_state(conn,token,agent)
    if agent=="insurance":
        if name=="verify_eligibility":
            state["eligibility"]="verified_active"
            state["registration"]="complete"
            result={"insurance_plan":"Plan X","active":True,"registration_received":True,"insurance_card_received":True,"verification":"completed"}
        elif name=="verify_referral":
            state["referral"]="verified_valid"
            result={"referral_required":True,"referral_valid":True,"prior_authorization_required":False,"verification":"completed"}
        else:
            fac=args.get("facility")
            if fac not in COVERED: return {"error":"Unknown facility"}
            state.setdefault("facility_checks",{})[fac]="covered" if COVERED[fac] else "not_covered"
            result={"facility":fac,"covered":COVERED[fac],"verification":"completed"}

    elif agent=="records":
        if name=="review_documentation":
            state["reviewed"]=True
            result={"referral_document":"received","previous_medical_record":"missing","previous_medical_record_required":True,"standard_processing_days":5}
        elif name=="request_missing_record":
            state["request_status"]="requested"
            state["expected_record_date"]=_day(refdate,5)
            result={"status":"requested","simulated_only":True,"initiated_date":refdate,"expected_availability":_day(refdate,5),"note":"Expected availability is a projection; record is not yet received."}
        else:
            raw=args.get("appointment_date","")
            try: apt=date.fromisoformat(raw)
            except (TypeError,ValueError): return {"error":"Use an appointment date in YYYY-MM-DD format"}
            if not (_day(refdate,0) <= apt.isoformat() <= _day(refdate,6)):
                return {"error":"Appointment date outside six-day case window"}
            state["last_appointment_date"]=raw
            if state.get("request_status")!="requested":
                result={"appointment_date":raw,"request_status":"not_requested","ready_before_appointment":False,"action_needed":"Request missing record"}
            else:
                expected=_day(refdate,5)
                result={"appointment_date":raw,"expected_record_date":expected,"ready_before_appointment":expected<raw,"note":"Projected readiness only; the record is not marked received"}
    else:
        if name=="search_appointments":
            result={"available":[{"facility":f,"appointment_date":_day(refdate,o),"time":t} for f,(o,t) in SLOTS.items()],
                    "note":"Coverage and medical record readiness are unknown to this agent unless the participant reports them"}
        elif name=="propose_appointment":
            fac=args.get("facility")
            if fac not in SLOTS: return {"error":"Unknown facility"}
            state["proposed_facility"]=fac
            o,t=SLOTS[fac]
            result={"status":"proposed_not_booked","facility":fac,"appointment_date":_day(refdate,o),"time":t}
        elif name=="book_appointment":
            fac=args.get("facility")
            if fac not in SLOTS: return {"error":"Unknown facility"}
            cq=args.get("coverage_quote",""); rq=args.get("records_quote",""); aq=args.get("authorization_quote","")
            if not all(_quote_in_user_messages(q,user_messages) for q in (cq,rq,aq)):
                return {"error":"Exact participant quotes for coverage, records and authorization are required; booking not made"}
            if not _quote_in_user_messages(aq,[current_user_message]):
                return {"error":"Booking requires an explicit authorization in the current participant message"}
            norm_c=_normalize(cq);norm_r=_normalize(rq);norm_a=_normalize(aq)
            if fac.lower() not in cq.lower() or not any(x in norm_c for x in ("covered","in network","insurance")) or any(x in norm_c for x in ("not covered","not in network","uncovered","out of network")):
                return {"error":"Coverage confirmation for the specific facility is missing"}
            if not any(x in norm_r for x in ("record","document")) or not any(x in norm_r for x in ("ready","available","arrive","before","by ")):
                return {"error":"Explicit records-readiness confirmation missing"}
            if not any(x in norm_a for x in ("book","schedule","confirm","reserve")):
                return {"error":"Explicit booking authorization missing"}
            o,t=SLOTS[fac]
            state["booked_facility"]=fac
            state["proposed_facility"]=fac
            result={"status":"booked_in_simulation","facility":fac,"appointment_date":_day(refdate,o),"time":t,"coverage_source":"participant_report","records_source":"participant_report"}
        else:
            aq=args.get("authorization_quote","")
            if not _quote_in_user_messages(aq,[current_user_message]) or not any(x in _normalize(aq) for x in ("cancel","release")):
                return {"error":"Cancellation must be explicitly requested in the current participant message"}
            old=state.pop("booked_facility",None)
            result={"status":"cancelled" if old else "no_booking_to_cancel","previous_facility":old}
    _save(conn,token,agent,state)
    return result
