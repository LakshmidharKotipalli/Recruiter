Run the recruiting pipeline as native Claude Code sub-agents with three human-in-the-loop checkpoints.

**IMPORTANT — User Interaction Rules:**
1. **Always use `AskUserQuestion`** for all user interactions (collecting input, HITL checkpoints, edit requests, option selection) — never output a question as plain text and wait.
2. **Context-first questions.** Before asking any question, review what has happened in the pipeline so far — the data produced, counts, issues found, quality scores — and frame your question to reflect that context. Do NOT paste template text verbatim. Instead, summarize the relevant state (e.g. "I found 8 profiles but 3 had dead URLs, leaving 5 usable candidates") and then present options that make sense given that state. Tailor option descriptions to the actual situation (e.g. if location was already "Remote", don't offer "relax location"). The templates below are structural guides — adapt the wording to match what actually happened.

## What this does

1. Extracts and rewrites the job description → **HITL-1: recruiter reviews before search begins**
2. Determines fetch mode (API vs Web) based on `.env` keys
3. Generates queries → fetches profiles from job boards + LinkedIn → deduplicates
4. If deduped pool is below requested size → **HITL-2: recruiter decides how to expand**
5. Scores and ranks candidates
6. If final shortlist is below requested size → **HITL-3: recruiter decides before output is served**
7. Renders final shortlist

---

## Step 1 — Collect Job Description and Preferences

After the skill is invoked, immediately prompt the user for the job description. Use `AskUserQuestion` to ask them to enter/paste the job description text or provide a file path. Keep the prompt simple and direct, e.g.:

> Please enter the job description (paste the text, or provide a file path like `./jd.txt`):

**Do NOT expect the JD as a command argument.** Always ask interactively after the command is run.

When the user responds:
- If their response looks like a file path (starts with `/`, `~`, `./`, or ends with `.txt`, `.md`, `.pdf`, `.json`) → read that file using the Read tool and use its contents as the JD text.
- Otherwise → use their response as the raw JD text directly.

Then use `AskUserQuestion` to ask for preferences:
- Desired shortlist size (default 10)
- Whether they want advanced config (weights/thresholds) — they can just press Enter / say "no" for defaults

Use defaults for all other settings:
- `requested_count` = 10
- weights: must_have_skills=0.50, nice_to_have_skills=0.25, seniority_level=0.15, location_match=0.10
- `accepted_min_score` = 0.70, `close_min_score` = 0.50

If the user types "advanced", use `AskUserQuestion` to present the current default weights and thresholds, and ask which values they'd like to change. Show the defaults clearly so they can just confirm or override specific ones. Store the response values.

---

## Step 2 — Initialize the run

```bash
python3 -c "import uuid; print(uuid.uuid4())"
```

```bash
mkdir -p /tmp/recruiting-pipeline/<run_id>
```

```bash
date +%s > /tmp/recruiting-pipeline/<run_id>/.start_time
```

Write `/tmp/recruiting-pipeline/<run_id>/00_input.json`:
```json
{
  "raw_jd": "<JD text>",
  "requested_count": 10,
  "weights": { "must_have_skills": 0.50, "nice_to_have_skills": 0.25, "seniority_level": 0.15, "location_match": 0.10 },
  "thresholds": { "accepted_min_score": 0.70, "close_min_score": 0.50 }
}
```

Print: `✓ Run ID: <run_id> — initialized`

**Timing:** After each stage completes, compute elapsed seconds:
```bash
echo $(( $(date +%s) - $(cat /tmp/recruiting-pipeline/<run_id>/.start_time) ))
```
Append `(<N>s)` to every stage completion message.

---

## Step 3 — JD Extraction (Stage 1)

Invoke the `jd-extraction` sub-agent:
```
Task: Run JD extraction for run_id=<run_id>. Read /tmp/recruiting-pipeline/<run_id>/00_input.json, extract SearchSpec, write 01_search_spec.json.
```

After the agent returns, read `/tmp/recruiting-pipeline/<run_id>/01_search_spec.json`. If it contains `"error"`, print the error and stop.

---

## ⏸ HITL-1 — JD Review (after extraction, before search)

Read `01_search_spec.json` and present the extracted spec to the recruiter:

```
╔══════════════════════════════════════════════════════════╗
║  HITL-1 · JD REVIEW — Please confirm before we search   ║
╚══════════════════════════════════════════════════════════╝

Role:       <role_title> (<seniority_level>)
Location:   <location>
JD Quality: <jd_quality_score>/10
Issues:     <jd_issues joined by ", " or "none">

Must-have skills:
  <bullet list of must_have_skills>

Nice-to-have skills:
  <bullet list of nice_to_have_skills>

Inferred skills (added by agent):
  <bullet list of inferred_skills>

Cleaned JD summary:
  <first 300 chars of cleaned_jd>...
```

Use `AskUserQuestion`. Before asking, review the extracted spec — note the quality score, any issues flagged, the inferred skills added, and whether anything looks unusual (e.g. very few must-haves, or location missing). Frame the question around what you actually found:
- If quality is high and spec looks clean, a brief "This looks solid — confirm or edit?" is fine
- If there are issues (low quality score, missing fields, questionable inferences), call them out specifically: "The JD scored 4/10 — it was missing location and seniority. I inferred 'senior' from '5+ years' and set location to 'Not specified'. You may want to edit these."
- Always offer: confirm (yes), edit, or abort

**If "abort"**: stop immediately.

**If "edit"**: use `AskUserQuestion` to ask what to change. Suggest specific editable fields based on what seems weak (e.g. "Want to add a location? Move any inferred skills to must-haves?"). Apply edits to `01_search_spec.json`, re-display the updated spec, and use `AskUserQuestion` again for confirmation. Repeat until confirmed.

**If "yes" / Enter**: print `✓ JD confirmed — proceeding to search` and continue.

---

## Step 4 — Determine fetch mode

```bash
cat .env 2>/dev/null
```

If any of `GOOGLE_CSE_KEY`, `NAUKRI_KEY`, `PDL_KEY` are real (non-empty, not "mock-key") → use `profile-fetching-api` and set `fetch_mode=api`. Otherwise → use `profile-fetching-web` and set `fetch_mode=web`.

Print: `ℹ Fetch mode: <API | Web Search>`

---

## Step 5 — Query Generation (Stage 2)

Invoke the `query-generation` sub-agent, passing the determined fetch mode:
```
Task: Run query generation for run_id=<run_id>, mode=<fetch_mode>. Read /tmp/recruiting-pipeline/<run_id>/01_search_spec.json, generate Boolean queries for job boards (naukri/indeed as primary, others as optional) and LinkedIn, write 02_query_set.json.
```

After the agent returns, read `02_query_set.json`. If error, print and stop.
Print: `✓ Stage 2 complete — queries generated (<N>s)`

---

## Step 6 — Profile Fetching (Stage 3)

**If API mode**, invoke `profile-fetching-api`:
```
Task: Run API profile fetching for run_id=<run_id>.
- Read /tmp/recruiting-pipeline/<run_id>/02_query_set.json for queries
- Read .env for API keys
- Call real APIs via curl — do not use mock data
- Every profile must come from a live API response in this session
- Write /tmp/recruiting-pipeline/<run_id>/03_raw_profiles.json
```

**If Web mode**, invoke `profile-fetching-web`:
```
Task: Run web profile fetching for run_id=<run_id>.
- Read /tmp/recruiting-pipeline/<run_id>/02_query_set.json for queries
- Run job board searches first (naukri and indeed as primary, others as optional), then LinkedIn
- You MUST call WebSearch before building any profile — log raw results, then extract
- You MUST call WebFetch on every URL returned by WebSearch
- Every profile must be built from WebSearch/WebFetch tool output — not from memory
- Write /tmp/recruiting-pipeline/<run_id>/03_raw_profiles.json
```

After the agent returns, read `03_raw_profiles.json`. If error, print and stop.
Print: `✓ Stage 3 complete — <count> raw profiles fetched (<N>s)`

---

## Step 7 — Deduplication (Stage 4)

Invoke the `candidate-deduplication` sub-agent:
```
Task: Run candidate deduplication for run_id=<run_id>. Read /tmp/recruiting-pipeline/<run_id>/03_raw_profiles.json. Apply P1-P4 dedup rules, prefer job board URLs over LinkedIn URLs. Write 04_deduped_profiles.json.
```

After the agent returns, read `04_deduped_profiles.json`. If error, print and stop.

Store `deduped_count = len(deduped_profiles)`.

If `requested_count` is not in your current context (e.g. after context compression), re-read it from `/tmp/recruiting-pipeline/<run_id>/00_input.json`.

Print: `✓ Stage 4 complete — <deduped_count> unique profiles (<N>s)`

---

## ⏸ HITL-2 — Pool Size Check (after dedup, before ranking)

The minimum viable pool to reliably produce `requested_count` shortlisted candidates is `requested_count × 2`.

> **Note:** This differs from the Python pipeline, which triggers HITL-2 when `raw_count < 15` (before dedup, fixed threshold). The subagent version triggers after dedup using a dynamic threshold based on `requested_count`. This is an intentional divergence — the subagent approach better reflects the user's actual goal.

**If `deduped_count >= requested_count × 2`**: skip HITL-2, print `✓ Pool adequate — proceeding to ranking` and continue.

**If `deduped_count < requested_count × 2`**: pause and use `AskUserQuestion`. Before asking, analyze the situation:
- How many profiles came from each source? (read `03_raw_profiles.json` source_availability)
- How many were lost to deduplication?
- What is the current location constraint?
- Were any sources unavailable?

Frame the question with this context. For example: "We found 12 raw profiles (8 from Naukri, 4 from LinkedIn) but after removing duplicates, only 6 unique candidates remain. You asked for 10 — this pool likely won't produce enough accepted candidates."

Then present **only the options that make sense**:
- **broaden** — always offer (run fallback queries)
- **relax location** — only offer if the spec has a specific city/region (skip if already "Remote", "India", or "Not specified")
- **proceed** — always offer (rank what we have)
- **abort** — always offer

Tailor option descriptions to actuals: e.g. "Broaden: run wider queries — Naukri returned the most results, so fallbacks there may help" or "Relax location: currently filtering for 'Bangalore' — removing this opens the pool to all of India".

**If "abort"**: stop.

**If "proceed" / 3**: print `⚠ Proceeding with small pool — shortlist may be shorter than requested` and continue to Step 8.

**If "broaden" / 1**:
- First, copy the existing raw profiles to a backup: `cp /tmp/recruiting-pipeline/<run_id>/03_raw_profiles.json /tmp/recruiting-pipeline/<run_id>/03_raw_profiles_primary.json`
- Re-invoke `profile-fetching-web` (or api) with additional instruction: `Also run fallback queries from job_boards.naukri.fallback, job_boards.indeed.fallback, and google_cse.fallback in 02_query_set.json — these were not run in the first pass. Write results to /tmp/recruiting-pipeline/<run_id>/03_raw_profiles_fallback.json`
- After the agent returns, merge both files: read `03_raw_profiles_primary.json` and `03_raw_profiles_fallback.json`, combine their `profiles[]` arrays, and write the merged result to `03_raw_profiles.json`
- Re-invoke `candidate-deduplication` to deduplicate the combined set
- Print the new `deduped_count` and continue to Step 8 (do not loop HITL-2 again)

**If "relax" / 2**:
- Read `01_search_spec.json`, set `location: "India"` (or remove city constraint), write it back
- Re-invoke `query-generation` with the updated spec, passing `mode=<fetch_mode>`
- Re-invoke profile fetching + deduplication
- Print the new `deduped_count` and continue to Step 8 (do not loop HITL-2 again)

---

## Step 8 — Ranking (Stage 5)

Invoke the `candidate-ranking` sub-agent:
```
Task: Run candidate ranking for run_id=<run_id>.
- Read /tmp/recruiting-pipeline/<run_id>/04_deduped_profiles.json and 01_search_spec.json
- Score and rank all profiles
- accepted_min_score=<accepted_min_score>, close_min_score=<close_min_score>
- Verify all URLs (WebFetch), prefer job board URLs, exclude profiles with dead/null URLs from accepted+close
- Write 05_pipeline_output.json
```

After the agent returns, read `05_pipeline_output.json`. If error, print and stop.

Store:
- `accepted_count = len(accepted)`
- `close_count = len(close)`
- `shortlist_count = accepted_count + close_count`

Print: `✓ Stage 5 complete — accepted=<n>, close=<n>, rejected=<n> (<N>s)`

---

> **Note:** The Python pipeline uses an automated `QualityChecker` (accept/refine_broader/refine_location_relaxed/force_accept) instead of HITL-3. The subagent pipeline intentionally replaces this with human control. This is a documented design divergence.

## ⏸ HITL-3 — Shortlist Size Check (after ranking, before serving output)

**If `shortlist_count >= requested_count`**: skip HITL-3, print `✓ Shortlist meets requested size — serving output` and continue to Step 9.

**If `shortlist_count < requested_count`**: pause and use `AskUserQuestion`. Before asking, analyze the ranking results:
- How many were accepted vs close vs rejected?
- What was the score distribution? (top accepted score, lowest close score, common rejection reason)
- How many were rejected by the must-have gate vs low score vs dead URLs?
- What's the gap between accepted_min_score and the highest-scoring close candidate?

Frame the question with this context. For example: "You asked for 10 candidates. Ranking produced 4 accepted (scores 78-92%) and 3 close (scores 55-68%). The 3 close candidates each missed 1 must-have skill. 5 were rejected outright (missing 2+ must-haves)."

Then present **only the options that make sense**:
- **lower threshold** — only offer if there are close candidates whose scores are near the current threshold (explain what it would change: "Lowering from 70% to 60% would move 2 close candidates into accepted")
- **include_close** — only offer if there are close candidates (skip if close_count = 0)
- **proceed** — always offer
- **abort** — always offer

Skip options that won't help (e.g. don't offer "lower threshold" if all close candidates scored 30%, or "include_close" if there are no close candidates).

**If "abort"**: stop.

**If "proceed" / 3**: continue to Step 9 with current output.

**If "include_close" / 2**:
- Re-read `05_pipeline_output.json`
- Treat all close candidates as part of the final report (no re-ranking needed)
- Print: `⚠ Including <close_count> close candidates in final output`
- Continue to Step 9

**If "lower" / 1**:
- New `accepted_min_score = accepted_min_score - 0.10` (floor at 0.40)
- Re-invoke `candidate-ranking` with the updated threshold:
  ```
  Task: Re-run candidate ranking for run_id=<run_id> with accepted_min_score=<new_threshold>, close_min_score=<close_min_score>. Read 04_deduped_profiles.json and 01_search_spec.json. Overwrite 05_pipeline_output.json.
  ```
- After re-ranking, print new counts
- If still below `requested_count`, proceed anyway (do not loop HITL-3 again — serve what we have)
- Continue to Step 9

---

## Step 9 — Render Final Report

Read `/tmp/recruiting-pipeline/<run_id>/05_pipeline_output.json`.

Render:

```markdown
# Recruiting Pipeline — Final Shortlist

**Run ID:** `<run_id>`
**Role:** <role_title> (<seniority_level>)
**Location:** <location>
**JD Quality:** <jd_quality_score>/10
**Requested:** <requested_count> candidates | **Delivered:** <shortlist_count>

---

## ✅ Accepted Candidates (<accepted_count>)

For each accepted candidate (sorted by score desc):

---
**<rank>. <Name>** — <score_pct>% | <title> @ <company> | <location>
Profile: <apply url_status rules>
  live          → plain URL
  login_wall    → `<url>  (requires login)`
  partial       → `<url>  (limited view)`
  dead/null     → `No working URL`

<reasoning>

---

## 🟡 Close Candidates (<close_count>)

Same card format. Each card notes the specific gap that kept them out of accepted.

---

## Summary

| Metric | Value |
|--------|-------|
| Requested | <requested_count> |
| Delivered (accepted + close) | <shortlist_count> |
| Accepted | <accepted_count> |
| Close | <close_count> |
| Rejected | <rejected_count> |
| Profiles evaluated | <total> |
| URL coverage | live=<n> · login_wall=<n> · partial=<n> · no URL=<n> |
| URL sources | job_board=<n> · linkedin=<n> · other=<n> |

## HITL Decisions

<For each HITL checkpoint that was exercised, log the decision made:>
- HITL-1: <approved as-is | edits applied: "...">
- HITL-2: <skipped (pool adequate) | broaden | relax | proceed with small pool>
- HITL-3: <skipped (target met) | lower threshold to X | include_close | proceed as-is>

## Pipeline Files

All data: `/tmp/recruiting-pipeline/<run_id>/`
```

Fill all values from actual pipeline output data.
