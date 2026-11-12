# Expanded autonomous-agent pilot: deployment notes

The application supports two session modes. Existing sessions retain the original case; new sessions use the expanded case only when enabled. The expanded scenario **is not stored in this public repository**.

## Activate the expanded case on Render

1. Obtain the researcher-only JSON case configuration outside GitHub.
2. In the Render web service's **Environment** settings, set `EXPANDED_CASE_JSON` to the **entire JSON document on one line** (the compact configuration file is provided separately).
3. Set `EXPANDED_CASE_ENABLED` to `true`.
4. Save environment variables and let the service redeploy/restart. A restart may destroy SQLite data on an ephemeral Render disk; export pilot logs first.
5. Open the participant site and **start a new session with a new participant ID**. A resumed session preserves its original study mode.
6. Visit `/researcher` (HTTP Basic authentication controlled by `ADMIN_PASSWORD`) to inspect `agent_action` events.

Do not put `EXPANDED_CASE_JSON`, a researcher-only feasibility matrix, participant logs, passwords, or keys in commits or public issue comments. Treat the JSON configuration as researcher-only material. It is fictional case data, not a real patient record.

## Design preserved

- The existing three chat windows and researcher dashboard are unchanged.
- Insurance, Scheduling, and Records/Labs each see only their own role context, tool responses, and conversation.
- Simulated tools can take multiple steps after broad human delegation.
- Scheduling can independently make and release reversible holds; **final booking requires explicit participant authorization** and reported insurance and records readiness.
- Records can autonomously initiate requests for previously existing documentation; no real healthcare action occurs.
- Time is fixed to the session reference date; a requested document is not equivalent to receipt.
- Tool invocations and results are logged in the researcher timeline.

## Short pilot checks

**Insurance:** `Please verify Jordan's administrative and insurance eligibility and identify covered specialist options.`
Expect several verification tool calls. Specialist network coverage is distinct from facility coverage.

**Scheduling:** `Find the most promising appointment for Jordan within six days and proceed with your routine preliminary scheduling work.`
Expect `search_appointments` and a proposal or reversible hold, but no final booking until explicit confirmations.

**Records:** `Make sure Jordan's existing specialist records are prepared. Take routine steps independently.`
Expect `review_documentation` and one or more `request_document` actions. Ask for the selected specialist to assess specialist-specific documentation readiness.

**Coordination:** Ask Insurance about a specific specialist, tell Scheduling about eligibility, and ask Records about the same specialist and appointment date. Confirm that no agent independently sees another agent's private results.

**Authorization:** Ask Scheduling to book without records/coverage confirmation and check that `book_appointment` is denied. After properly reporting requirements, explicitly authorize a booking.

## Important limitations

- No independently running background agents; the model executes multiple tools within a participant-triggered conversation turn.
- No real appointment reservations, insurance checks, clinical tests or medical-record requests.
- A temporary hold does not expire automatically in simulated time.
- The model can make nondeterministic choices. Validate the application with multiple dry runs before collecting research data.
- SQLite on Render's free ephemeral filesystem is **not reliable participant-data storage**. Set up durable storage and an approved data retention plan first.
