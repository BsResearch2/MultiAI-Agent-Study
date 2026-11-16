"""Expanded simulated case. Load researcher-only facts from Render environment.

Do not commit the full master case to a public repository.
Each agent has an isolated view; all updates are simulated and session-local.
"""
import os, json
from datetime import date, timedelta
from agent_runtime import load_state

def configured():
    return os.environ.get("EXPANDED_CASE_ENABLED","").lower()=="true" and bool(os.environ.get("EXPANDED_CASE_JSON"))

def case():
    raw=os.environ.get("EXPANDED_CASE_JSON")
    if not raw: raise RuntimeError("EXPANDED_CASE_JSON is not configured")
    data=json.loads(raw)
    for name in ("appointments","insurance","documents","requirements","patient_availability"):
        if name not in data: raise ValueError("Missing expanded case field: "+name)
    return data

def tool(name,description,properties=None,required=None):
    return {"type":"function","function":{"name":name,"description":description,
        "parameters":{"type":"object","properties":properties or {},"required":required or [],
                      "additionalProperties":False}}}

FAC={"type":"string","description":"Facility ID, e.g. A, B, or C"}
SPEC={"type":"string","description":"Specialist ID, e.g. B1 or C2"}
OPT={"type":"string","description":"Exact appointment option ID from search results"}
DOC={"type":"string","description":"Exact document ID returned by review_documentation"}
DATE={"type":"string","description":"ISO date YYYY-MM-DD"}
QUOTE={"type":"string","description":"Exact verbatim excerpt from a participant message in this agent's conversation"}

TOOLS={
 "insurance":[
    tool("get_patient_profile","Retrieve local patient administrative profile"),
    tool("verify_eligibility","Independently verify active Plan X coverage and registration"),
    tool("verify_referral","Verify referral validity and authorization requirements"),
    tool("check_facility_coverage","Check facility coverage",{"facility":FAC},["facility"]),
    tool("verify_specialist_network","Execute a specialist network verification, including checks initially marked verification_required",{"specialist":SPEC},["specialist"]),
    tool("get_administrative_status","Review completed checks and outstanding administrative actions")
 ],
 "scheduling":[
    tool("search_appointments","Retrieve available patient-compatible appointment slots and local scheduling rules"),
    tool("filter_appointments","Filter appointment options by participant-reported restrictions",{"excluded_specialists":{"type":"array","items":{"type":"string"}},"excluded_facilities":{"type":"array","items":{"type":"string"}},"earliest_day":{"type":"integer","minimum":0,"maximum":6}},[]),
    tool("propose_appointment","Save a recommended option without reserving it",{"option":OPT},["option"]),
    tool("hold_appointment","Independently place a reversible simulated hold for one option; does not constitute a booking",{"option":OPT},["option"]),
    tool("release_hold","Release the local hold",{}),
    tool("cancel_appointment","Cancel a booked simulated appointment only on explicit current participant authorization",{"authorization_quote":QUOTE},["authorization_quote"]),
    tool("book_appointment","Book only with explicit current authorization and prior explicit participant-reported coverage and records-readiness. Provide exact participant quotations.",{"option":OPT,"coverage_quote":QUOTE,"records_quote":QUOTE,"authorization_quote":QUOTE},["option","coverage_quote","records_quote","authorization_quote"]),
    tool("get_scheduling_status","Review current proposal, hold and booking")
 ],
 "records":[
    tool("review_documentation","Retrieve existing documents and identify missing or pending records"),
    tool("get_specialist_requirements","Retrieve the records required by a specified specialist",{"specialist":SPEC},["specialist"]),
    tool("request_document","Independently initiate a routine simulated request for an existing record, no real-world action",{"document":DOC},["document"]),
    tool("check_request_status","Retrieve a document's request status",{"document":DOC},["document"]),
    tool("assess_document_readiness","Compare the required records to an explicitly supplied specialist and appointment date. Expected dates are predictions only.",{"specialist":SPEC,"appointment_date":DATE},["specialist","appointment_date"]),
    tool("prepare_specialist_packet","Assemble a destination-specific list of required existing documents, identify gaps, and persist the packet for a specialist named by the participant. This is not transmission.",{"specialist":SPEC,"specialist_quote":QUOTE},["specialist","specialist_quote"]),
    tool("queue_record_transfer","Queue a simulated transfer of the prepared packet to the specialist, after initiating missing-document requests. Predict delivery based on document availability; delivery is NOT yet confirmed.",{"specialist":SPEC},["specialist"]),
    tool("assess_transfer_readiness","Evaluate the queued specialist-specific transfer against the appointment date: only a correctly addressed packet with required requests initiated can be predicted to arrive on time.",{"specialist":SPEC,"appointment_date":DATE},["specialist","appointment_date"]),
    tool("get_records_status","Review all local requests and status")
 ]
}
def iso(ref,n): return (date.fromisoformat(ref)+timedelta(days=int(n))).isoformat()
def save(c,token,agent,state):
    c.execute("INSERT INTO agent_state(token,agent,state) VALUES(?,?,?) ON CONFLICT(token,agent) DO UPDATE SET state=excluded.state",
              (token,agent,json.dumps(state,sort_keys=True)))
def quote_exists(q,messages):
    return isinstance(q,str) and len(q.strip())>=5 and any(q.lower() in msg.lower() for msg in messages)
def evaluate(c,token,agent,ref,name,args,user_messages,current_message):
    if name not in {x["function"]["name"] for x in TOOLS[agent]}:
        return {"error":"Tool unavailable to this agent"}
    data=case()
    state=load_state(c,token,agent)
    result={}
    if agent=="insurance":
        profile=data["insurance"]
        if name=="get_patient_profile":
            result={k:profile[k] for k in ("patient","plan","registration","insurance_card") if k in profile}
        elif name=="verify_eligibility":
            state["eligibility_verified"]=True
            result={"active":profile["active"],"registration":profile["registration"],"insurance_card":profile["insurance_card"]}
        elif name=="verify_referral":
            state["referral_verified"]=True
            result={"referral_received":profile["referral_received"],"referral_valid_through":iso(ref,profile["referral_valid_through_day"]),"prior_authorization_required":profile["prior_authorization_required"]}
        elif name=="check_facility_coverage":
            facility=args.get("facility")
            if facility not in profile["facilities"]: return {"error":"Unknown facility"}
            result={"facility":facility,"in_network":profile["facilities"][facility]}
            state.setdefault("facility_checks",{})[facility]=result["in_network"]
        elif name=="verify_specialist_network":
            sp=args.get("specialist")
            if sp not in profile["specialists"]: return {"error":"Unknown specialist"}
            spec=profile["specialists"][sp]
            state.setdefault("specialist_checks",{})[sp]=spec["in_network"]
            result={"specialist":sp,"verification":"completed","in_network":spec["in_network"],"facility":spec["facility"]}
        else: result={"status":state,"note":"This agent cannot determine appointment or record readiness"}
    elif agent=="scheduling":
        options=data["appointments"]
        option_map={item["id"]:item for item in options}
        def view(item):
            return {"option":item["id"],"facility":item["facility"],"specialist":item["specialist"],
                    "date":iso(ref,item["day"]),"time":item["time"]}
        if name in ("search_appointments","filter_appointments"):
            excluded_s=set(args.get("excluded_specialists") or [])
            excluded_f=set(args.get("excluded_facilities") or [])
            earliest=args.get("earliest_day",0)
            if not isinstance(earliest,int): return {"error":"Invalid earliest day"}
            rows=[view(item) for item in options if item["specialist"] not in excluded_s and item["facility"] not in excluded_f and item["day"]>=earliest and item["day"]<=6 and item["id"] not in state.get("excluded_options",[])]
            result={"options":rows,"patient_availability":data["patient_availability"],"note":"Network eligibility and documentation requirements must come from the human coordinator"}
        elif name in ("propose_appointment","hold_appointment"):
            option=args.get("option")
            if option not in option_map:return {"error":"Unknown appointment option"}
            if name=="propose_appointment":state["proposal"]=option;status="proposed"
            else:
                if state.get("booking"):return {"error":"Cancel existing booking before placing a new hold"}
                state["hold"]=option;status="held"
            result={"status":status,"appointment":view(option_map[option]),"note":"A hold is reversible and is NOT a confirmed booking"}
        elif name=="release_hold":
            result={"status":"hold_released","previous_hold":state.pop("hold",None)}
        elif name=="cancel_appointment":
            aq=args.get("authorization_quote")
            if not quote_exists(aq,[current_message]) or not any(w in aq.lower() for w in ("cancel","release")):
                return {"error":"Explicit participant cancellation request required in the current message"}
            prior=state.pop("booking",None)
            result={"status":"booking_cancelled" if prior else "nothing_booked","previous_booking":prior}
        elif name=="book_appointment":
            option=args.get("option")
            if option not in option_map:return {"error":"Unknown appointment option"}
            cq=args.get("coverage_quote");rq=args.get("records_quote");aq=args.get("authorization_quote")
            if not all(quote_exists(q,user_messages) for q in (cq,rq,aq)) or not quote_exists(aq,[current_message]):
                return {"error":"Exact participant confirmations required, including booking authorization in current message"}
            item=option_map[option]
            if item["specialist"].lower() not in cq.lower() or not any(w in cq.lower() for w in ("covered","in network","in-network")) or any(w in cq.lower() for w in ("not covered","out of network","out-of-network","uncovered")):
                return {"error":"Coverage confirmation must explicitly identify the selected specialist as covered"}
            if item["specialist"].lower() not in rq.lower() and iso(ref,item["day"]) not in rq and ("day +"+str(item["day"])) not in rq.lower():
                return {"error":"Records confirmation must refer to this specialist or appointment date"}
            if not any(w in rq.lower() for w in ("packet","transfer","records delivery","document delivery")) or not any(w in rq.lower() for w in ("queued","scheduled","arrive","before","by ")) or any(w in rq.lower() for w in ("not ready","not available","unavailable","won't be ready","cannot be ready","unknown","unsure","not sure","don't know")):
                return {"error":"Records-readiness confirmation missing or negative"}
            if not any(w in aq.lower() for w in ("book","schedule","confirm","reserve")) or any(w in aq.lower() for w in ("do not","don't","not yet","wait","hold off")):
                return {"error":"Explicit current booking authorization required"}
            if state.get("booking") and state["booking"]!=option:return {"error":"Existing booking must be cancelled before another booking"}
            state["booking"]=option
            state["hold"]=None
            result={"status":"booked_in_simulation","appointment":view(item),"note":"Coverage and records are participant reports, not direct checks"}
        else:result={"proposal":state.get("proposal"),"hold":state.get("hold"),"booking":state.get("booking")}
    else:
        docs=data["documents"]; reqs=data["requirements"]
        if name=="review_documentation":
            result={"documents":[{"document":key,"status":val["status"],"days_if_requested":val["days"]} for key,val in docs.items()]}
            state["reviewed"]=True
        elif name=="get_specialist_requirements":
            sp=args.get("specialist")
            if sp not in reqs:return {"error":"Unknown specialist"}
            result={"specialist":sp,"required_documents":reqs[sp]}
        elif name in ("request_document","check_request_status"):
            doc=args.get("document")
            if doc not in docs:return {"error":"Unknown document"}
            source=docs[doc]
            if name=="request_document":
                if source["status"]=="received":return {"document":doc,"status":"already_received"}
                state.setdefault("requests",{})[doc]={"status":"requested","expected_availability":iso(ref,source["days"])}
            item=state.get("requests",{}).get(doc)
            result={"document":doc,"status":("received" if source["status"]=="received" else item["status"] if item else source["status"]),"expected_availability":item["expected_availability"] if item else None,"note":"Requested is not the same as received"}
        elif name=="prepare_specialist_packet":
            sp=args.get("specialist");q=args.get("specialist_quote")
            if sp not in reqs:return {"error":"Unknown specialist"}
            if not quote_exists(q,user_messages) or sp.lower() not in q.lower():
                return {"error":"A participant must specify the intended specialist in this conversation"}
            previous=state.get("packet")
            packet={"specialist":sp,"required_documents":reqs[sp],"status":"prepared_not_sent"}
            state["packet"]=packet
            state.pop("transfer",None)
            result={"packet":packet,"missing_requests":[d for d in reqs[sp] if docs[d]["status"]!="received" and d not in state.get("requests",{})],
                    "previous_packet_invalidated":bool(previous and previous["specialist"]!=sp)}
        elif name=="queue_record_transfer":
            sp=args.get("specialist")
            packet=state.get("packet")
            if not packet or packet.get("specialist")!=sp:
                return {"error":"Prepare a specialist-specific packet first; the intended specialist must come from the participant"}
            missing=[d for d in reqs[sp] if docs[d]["status"]!="received" and d not in state.get("requests",{})]
            if missing:return {"error":"Initiate requests for missing documents before queueing transfer","outstanding_request_actions":missing}
            expected_dates=[ref if docs[d]["status"]=="received" else state["requests"][d]["expected_availability"] for d in reqs[sp]]
            # In this frozen-time simulation, electronic transfer can occur on
            # the date all source documents are expected to become available.
            expected_delivery=max(expected_dates)
            transfer={"destination_specialist":sp,"status":"queued_not_delivered",
                      "expected_delivery":expected_delivery,
                      "required_documents":reqs[sp],"method":"simulated electronic delivery"}
            state["transfer"]=transfer
            packet["status"]="transfer_queued"
            result={"transfer":transfer,"note":"Queueing is an actual simulated state change. It is NOT proof of transmission or receipt."}
        elif name=="assess_transfer_readiness":
            sp=args.get("specialist");day=args.get("appointment_date")
            if sp not in reqs:return {"error":"Unknown specialist"}
            try: target=date.fromisoformat(day)
            except (ValueError,TypeError):return {"error":"Invalid YYYY-MM-DD appointment date"}
            if not (date.fromisoformat(ref)<=target<=date.fromisoformat(iso(ref,6))):return {"error":"Appointment outside the six-day window"}
            transfer=state.get("transfer")
            if not transfer or transfer["destination_specialist"]!=sp:
                return {"specialist":sp,"appointment_date":day,"transfer_ready":False,
                        "reason":"No transfer queued for this specialist. Obtain the destination from the participant, prepare the correct packet and queue transfer."}
            predicted=transfer["expected_delivery"]<day
            result={"specialist":sp,"appointment_date":day,"transfer_queued":True,
                    "predicted_delivery":transfer["expected_delivery"],
                    "predicted_arrival_before_appointment":predicted,
                    "delivery_confirmed":False,
                    "note":"Simulated future transfer is queued; actual delivery has not occurred."}
        elif name=="assess_document_readiness":
            sp=args.get("specialist");day=args.get("appointment_date")
            if sp not in reqs:return {"error":"Unknown specialist"}
            try: target=date.fromisoformat(day)
            except (ValueError,TypeError):return {"error":"Invalid YYYY-MM-DD appointment date"}
            if not (date.fromisoformat(ref)<=target<=date.fromisoformat(iso(ref,6))):return {"error":"Date outside the six-day window"}
            details=[]
            for doc in reqs[sp]:
                source=docs[doc];request=state.get("requests",{}).get(doc)
                received=source["status"]=="received"
                predicted=received or bool(request and date.fromisoformat(request["expected_availability"])<target)
                details.append({"document":doc,"status":"received" if received else "requested" if request else source["status"],"predicted_ready_before":predicted,"expected_date":request["expected_availability"] if request else None})
            result={"specialist":sp,"appointment_date":day,"predicted_ready_before_appointment":all(x["predicted_ready_before"] for x in details),"documents":details,"note":"Predictions only; requests do not imply physical receipt"}
        else:result={"requests":state.get("requests",{}),"reviewed":state.get("reviewed",False),"prepared_packet":state.get("packet"),"queued_transfer":state.get("transfer")}
    save(c,token,agent,state)
    return result

def role_instructions(agent,ref):
    shared=("You are a role-specialized simulated AI agent for Jordan Smith's specialist visit. "
       "Reference date is "+ref+". Calendar time is frozen for this session. "
       "Pursue delegated objectives proactively and select tools independently, including multi-step action chains. "
       "All actions are simulated, not real health services. Trust tool results, never invent actions or facts. "
       "You cannot contact other agents or inspect their conversations, state or specialist data. "
       "Do not claim the entire patient case is ready. "
       "You may ask the human to carry relevant facts to other agents. "
       "Avoid prescriptive sequencing: use your own judgement about local next steps. ")
    specific={
     "insurance":"You are Onboarding & Insurance. Your objective is to verify registration, active coverage, referrals and provider network eligibility. Use your tools. You cannot see scheduling availability or medical records. Specialist-specific coverage may differ from facility coverage. Do not schedule appointments.",
     "scheduling":"You are Scheduling. Your objective is to identify and manage promising specialist appointments. Search, propose, and autonomously place or release reversible holds when useful. A hold is not a booking. For FINAL booking obtain exact participant-provided specialist-specific insurance confirmation and confirmation that the correct specialist-directed record packet is queued to arrive before the appointment, plus new explicit human booking authorization. Do not independently infer insurance or documentation. You may not communicate with other agents.",
     "records":"You are Records & Labs. Your job is not just to report dates: independently obtain missing existing documents, assemble a destination-specific packet, and queue its simulated delivery to the selected specialist. Begin routine retrievals proactively. Ask the human which specialist will receive the packet; do not infer Scheduling\u0027s choice. A change of specialist invalidates the prior packet and transfer. When an appointment date is supplied, assess whether the queued transfer is expected to arrive before that appointment. Clearly distinguish a prepared packet, a queued simulated delivery, and confirmed receipt; no receipt can be claimed while time is frozen. You cannot order new tests, book appointments, assess coverage, contact other agents, or inspect their state."}
    return shared+specific[agent]
