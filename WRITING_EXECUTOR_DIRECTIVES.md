# Writing Executor Directives

How to write directives that executor agents can implement correctly without ambiguity.

---

## Structure

Every directive follows this template:

```
**Directive: [Name]**

**Context:** You are working on `hubspot-engine-x`. Read `CLAUDE.md` before starting.

**Scope clarification on autonomy:** You are expected to make strong engineering decisions within the scope defined below. What you must not do is drift outside this scope, run deploy commands, or take actions not covered by this directive. Within scope, use your best judgment.

**Background:** [1-3 sentences on WHY this work matters]

**Existing code to read:** [List specific files]

---

### Deliverable 1: [Name]
[Exact instructions]
Commit standalone.

### Deliverable 2: [Name]
[Exact instructions]
Commit standalone.

[... more deliverables ...]

---

**What is NOT in scope:** [Explicit exclusions]

**Commit convention:** Each deliverable is one commit. Do not push.

**When done:** Report back with: (a) ..., (b) ..., (c) ..., (d) ..., (e) anything to flag.
```

---

## Rules

1. **List every file the agent should read before building.** Include full paths. Don't assume the agent knows where things are.

2. **Be explicit about what NOT to do.** "No deploy commands", "No database migrations", "Do not change existing routers" — state these clearly.

3. **One deliverable = one commit.** This keeps the work reviewable and revertable.

4. **"Do not push"** — the chief agent pushes after review. The executor never pushes.

5. **Include the HubSpot API details** when the work involves new HubSpot interactions. Specify the exact endpoint, HTTP method, request/response shape. Don't make the agent guess.

6. **Specify file names for new files.** Example: `app/services/hubspot.py`, not "add a HubSpot service somewhere."

7. **Always request a report.** The "When done" section tells the agent what to report so the chief can verify without reading every line of code.

8. **Reference the service layer boundary.** Any directive involving HubSpot API calls must route through `app/services/hubspot.py`. Remind the agent: no `httpx` in routers.

9. **Reference the token manager.** Any directive that calls HubSpot must use `token_manager.py` to get a valid access token. The agent should never assume the stored token is still valid.

---

## Common Mistakes to Avoid

1. **Don't say "clean up whatever looks wrong."** Be specific about what to change.

2. **Don't assume the agent knows the codebase.** Always list files to read. Even if it seems obvious.

3. **Don't combine unrelated work** in one directive. One directive = one coherent piece of work.

4. **Don't forget to specify where new files go.** "Create a service" is ambiguous. "Add methods to `app/services/hubspot.py`" is not.

5. **Don't let the agent call HubSpot from a router.** Every directive involving HubSpot calls must remind the agent about the service layer boundary.

6. **Don't skip the "existing code to read" section.** The agent needs reference patterns. Without them, it invents its own conventions.

7. **Don't forget token management.** Every HubSpot API interaction needs a valid token. The directive should explicitly reference `token_manager.py`.

8. **Don't let the agent store or log tokens.** Remind in directives that touch connection data: tokens never appear in API responses, logs, or error messages.