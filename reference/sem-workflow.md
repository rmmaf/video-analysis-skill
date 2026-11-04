# Without the Workflow tool: the same loop with the Agent tool

Use this procedure only when the Workflow tool does not exist in this session, not even through
`ToolSearch` (`select:Workflow`). It is the loop of `workflow/video-analysis.js` driven by you, with the
same prompts, word for word (the script keeps them mirrored here). The values come from the
`workflow_args` that `prepare.py` printed, also on resume.

## Rules

- **Do not read any file** of the run or of the cache: no prompt, no ledger, no report. The agents
  read; you only assemble the prompts below and check the responses.
- Each Agent response is one JSON line (the footer asks for it). Keep only a compact table per video:
  batch, state (`ok`, `partial`, `failed`), images seen/expected and warnings.
- Do not show prompts or JSON to the user. At the end, present as in step 7 of `SKILL.md`.
- The manual loop spends your context, about 1k tokens per batch. Long video: prefer a session with the
  Workflow tool, or split with `--trecho`. Above 800 estimated agents, do not run it: split.

## How to call the Agent tool

| role | `subagent_type` | `model` | `prompt` |
|---|---|---|---|
| curator: INIT, NEXT, FINAL, CROSS | `video-analysis-curator`; in `inline` mode, `general-purpose` | `opus` | [preamble, only in `inline`] + mode text + [2nd-attempt note] + footer |
| analyst | `video-analysis-analyst`; in `inline` mode, `general-purpose` | `sonnet` | [preamble, only in `inline`] + analyst text + [2nd-attempt note] + footer |

- The mode comes from `roles_mode` in the args. The parts of the prompt are joined with a blank line
  between them.
- `description`: the Workflow label, `plan · <slug>`, `curation · <slug> 3/7`, `analysis · <slug> 3/7`,
  `report · <slug>` or `synthesis`, with ` · 2nd` on the second attempt.
- **One video:** pass `run_in_background: false`, if the parameter exists; the next step depends on the
  result. **Several videos:** up to `options.max_parallel_videos` at the same time; send in the same
  message the next call of each active video and advance each one with its own response. The batches of a
  video are always in series.
- **Without the `model` parameter**, in `inline` mode the analyst runs on the session model (Opus, much
  more expensive). For long work, run the setup and reopen Claude Code first, to use `agents` mode.
- **Effort:** INIT, FINAL and CROSS at `high`; NEXT at `options.curator_effort`; analyst at
  `options.analyst_effort` (both `high` by default). Without an effort parameter in the Agent tool, the
  `effort` of the role's frontmatter applies (`agents` mode), or the session's (`inline`).
- **Response:** the last line of the response is the JSON. A response that is not valid JSON is a lost
  attempt.
- **Reason for a failure:** the `warnings` of the response joined by `; ` (in FINAL and CROSS, the
  `summary`), up to 160 characters. In the warnings below, `{reason}` goes in as ` (<reason>)` and is left
  out when there is none.

## The loop, per video

For each video in `videos`:

0. **Already done** (`final_exists: true`): no call. Status `skipped`, report
   `{out_dir}/relatorio.md`.
1. **Counts.** `B` = number of items in `batch_images`. `last` = `B`, or `options.max_batches` if it is
   smaller and greater than 0 (test mode). `start` = `start_batch` if `ledger_exists`, else 1. `pending` =
   empty list (analysed batches that no curation has integrated yet). `resumed` = `ledger_exists` and
   `start` > 1. Counters of consecutive failures of the curator and of the analyst at 0.
2. **Plan.** Without a ledger (`ledger_exists: false`): INIT, which succeeds with `status: "ok"`; otherwise,
   repeat it once with the curator's 2nd-attempt note. Failed both times → video `failed`, warning
   `plan (INIT) failed twice{reason}`, end of the video. With a good INIT, batch 1 already has a prompt.
   With a ledger, batch `start` has a prompt if `start_prompt_exists`.
3. **Batches**, for `n` from `start` to `last`, in series:
   1. **Curation**, only if `options.curation` is `batches` and batch `n` has no prompt yet: NEXT `n`, which
      succeeds with `status: "ok"` (2 attempts, like the INIT). Without `curation` or with `plan`, there is
      no NEXT: the batch without a prompt goes straight to the contingency analyst (text 4).
      - Succeeded: `pending` goes back to empty, `resumed` = false, curator failures = 0. For each item of
        `coverage` with a `state` other than `complete`, demote the batch (`incomplete` → `partial`,
        `missing` → `failed`) and warn `batch {batch}: report incomplete` or `… report missing`. If
        `integrated_through` < `n` − 1, warn `ledger integrated only up to batch {integrated_through} when
        preparing batch {n}`.
      - Failed both times: warn `curation of batch {n} failed{reason}: contingency prompt` and go on with
        the contingency analyst. If it was the second curation in a row to fail → video `failed`, warning
        `curator failed in 2 batches in a row: video interrupted`, end of the video.
   2. **Analysis:** the analyst of batch `n`, with text 3 (there is a curator prompt) or 4 (contingency).
      Expected = `batch_images[n-1]`. **Wrong path:** a response with `status` ≠ `failed` whose
      `report_path` differs from `{out_dir}/reports/batch_{NNN}.md` even when read with `\` as `/`,
      repeated slashes as one, without spaces at the ends and without telling upper from lower case. It
      succeeds if `status` ≠ `failed`, the path is not wrong, `frames_seen` ≥ expected and `frames_missing`
      is empty. Otherwise, second attempt: the same text + note 5, or 5b if the first came with a wrong
      path. In the second, `frames_seen` already adds up both attempts.
      - Succeeded in either → `ok`. Otherwise, the response with the most `frames_seen` among those with
        `status` ≠ `failed`, the right path and `frames_seen` > 0 → `partial`; none → `failed`, with the
        warning `batch {n}: report written to the wrong path ({where})` if any came with a wrong path
        (`{where}` as in note 5b, from the second if it was that one), otherwise
        `batch {n}: analysis failed{reason}` if there is a reason.
      - Images seen = min(`frames_seen`, expected), 0 if `failed`; add up the seen and expected of the
        video.
      - Add the batch to `pending`, with state and count.
      - Second batch in a row `failed` → video `failed`, warning
        `analyst failed in 2 batches in a row: video interrupted`, end of the video. A batch that did not
        fail resets the counter.
4. **Report:** FINAL, which succeeds with `status` ≠ `failed` (2 attempts). Failed both times → video
   `failed`, warning `final report failed twice{reason}: run /video-analysis retomar`. Succeeded →
   keep `report_path` (empty: `{out_dir}/relatorio.md`), `summary` and `answered`. Status of the video:
   `partial` if there was a `partial` or `failed` batch, if the FINAL came back `partial` or if `last` <
   `B`; otherwise `ok`.

After all the videos: **CROSS**, if two or more videos have a report and a status other than `failed`
(`ok`, `partial` or `skipped`) and `combined_exists` is false. It succeeds with `status` ≠ `failed` (2
attempts): `combined` = `{report_path, answered, summary}` (empty path: `{run_dir}/relatorio.md`); failed
both times, `combined` = `{error: "synthesis failed twice{reason}"}`.

Warnings: up to 6 per video; with more, the first 4, `+N warnings omitted` and the last one (in an
interrupted video, the reason). An interrupted video leaves the
files for `/video-analysis retomar`.

## Prompts

Copy each line as it is. `{…}` is a value to substitute: the names equal to those of the args (`{slug}`,
`{duration}`, `{objective_path}`, `{batches_dir}`, `{out_dir}`, `{language}`, `{run_id}`, `{run_dir}`,
`{roles_dir}`) come from them. `{n}` is the batch number without zeros and `{NNN}`, with 3 digits (`003`).
The others are defined below each block; when the condition does not hold, they go in empty.

### Preamble (only in inline mode), before the mode text

```text
Before any other action, use Read to read the file {roles_dir}/{role}.md. It defines your role and your rules: follow it as your system instructions (ignore the block between --- at the top). If you cannot read it, return status "failed" with warnings ["unreadable role"].
```

- `{role}`: `video-analysis-curator` or `video-analysis-analyst`.

### Curator's 2nd-attempt note (INIT, NEXT, FINAL and CROSS), after the mode text

```text
SECOND ATTEMPT: the previous one failed. If any output file already exists, read it before overwriting (Write requires it) and complete what is missing.
```

### 1. INIT

```text
MODE: INIT
Video: {slug} · {duration} · {B} batches{test}
Objective (verbatim) and options: {objective_path}
Summary of the video and the batches: {batches_dir}/summary.json
Transcript: {batches_dir}/transcript.md
Overview (if it exists): {batches_dir}/overview.jpg
Batch 1: timeline {batches_dir}/batch_001.md · contact sheet {batches_dir}/sheet_001.jpg · {batch_images[0]} images
Language of the reports: {language}
Write: the ledger to {out_dir}/ledger.md; then the prompt of batch 1 to {out_dir}/prompts/batch_001.md.
```

- `{duration}`: the `duration` of the video, or `?` if it comes empty.
- `{test}`: ` (this run analyses only batches 1–{last}; plan all of them and mark the others as outside this run)`
  when `last` < `B`.

### 2. NEXT

```text
MODE: NEXT
Video: {slug} · prepare batch {n}/{last}{last_of_run}
Objective: {objective_path}
Ledger: {out_dir}/ledger.md
Reports to integrate:
- batch {PPP}: {state} → {out_dir}/reports/batch_{PPP}.md{speech}
Check "Integrado até" in the ledger and integrate the existing reports in {out_dir}/reports up to batch {MMM}.
Next batch: timeline {batches_dir}/batch_{NNN}.md · contact sheet {batches_dir}/sheet_{NNN}.jpg · {batch_images[n-1]} images
Write: the updated ledger to {out_dir}/ledger.md; then the prompt to {out_dir}/prompts/batch_{NNN}.md.
```

- `{last_of_run}`: ` (last of this run)` when `n` = `last`.
- `Reports to integrate:` and the `- batch …` lines go in only if `pending` is not empty: one line per
  pending batch, in order. `{PPP}` is its number with 3 digits.
  - `{state}`: `ok, {seen}/{expected} images`, `PARTIAL, {seen}/{expected} images` or
    `FAILED, no reliable report`.
  - `{speech}`: ` (speech of the segment: {batches_dir}/batch_{PPP}.md)` only for a batch that failed.
- The line `Check "Integrado até"…` goes in only when `n` > 1 and (`resumed` or `pending` empty).
  `{MMM}` = `n` − 1 with 3 digits.

### 3. Analyst, with a curator prompt

```text
Batch {n}/{last} of video {slug}.
Curator prompt: {out_dir}/prompts/batch_{NNN}.md (read it first and follow it).
Timeline: {batches_dir}/batch_{NNN}.md
Report: write it to {out_dir}/reports/batch_{NNN}.md
Images in the batch: {batch_images[n-1]} · Language of the report: {language}
```

### 4. Contingency analyst, without a curator prompt

```text
Batch {n}/{last} of video {slug}. There is no curator prompt for this batch: follow the default contract of your role.
Objective: {objective_path}. Context: sections 4 and 5 of {out_dir}/ledger.md, if it exists.
Timeline: {batches_dir}/batch_{NNN}.md
Report: write it to {out_dir}/reports/batch_{NNN}.md
Images in the batch: {batch_images[n-1]} · Language of the report: {language}
```

### 5. Analyst's 2nd-attempt note, after text 3 or 4

```text
SECOND ATTEMPT. The previous one {result}. If {out_dir}/reports/batch_{NNN}.md exists, read it before overwriting; open the images that were missing (when in doubt, all of them) and write the complete report.
```

- `{result}`: `opened {frames_seen} of {batch_images[n-1]} images{missing}` if the first attempt returned
  JSON; `did not finish` if it did not.
- `{missing}`: ` (missing: {frames_missing})`, with the ids separated by `, `, when the list is not
  empty.

### 5b. Analyst's 2nd-attempt note when the previous one came with a wrong path, instead of 5

```text
SECOND ATTEMPT. The previous one {result}, but wrote the report to {where}, not to the requested path. Write the complete report exactly to {out_dir}/reports/batch_{NNN}.md, copying the path character by character, and return that same path in report_path. First, read whatever exists at those two paths and keep what is right; open the images that were missing (when in doubt, all of them).
```

- `{result}`: `opened {frames_seen} of {batch_images[n-1]} images{missing}`, as in note 5.
- `{where}`: the `report_path` that the previous one returned, with `/` in place of `\` and without spaces
  at the ends; `?` if it came empty.

### 6. FINAL

```text
MODE: FINAL
Video: {slug} · batches analysed: {planned} of {B}{partial_coverage}
Objective: {objective_path}
Ledger: {out_dir}/ledger.md
Batch reports: {out_dir}/reports/batch_001.md … batch_{LLL}.md
Not yet integrated into the ledger (in this run): {not_integrated}; also check "Integrado até".
Partial: {partial_batches} · Failed: {failed_batches}
{images_seen}
Transcript (for spot checks): {batches_dir}/transcript.md
Language: {language}
Write the final report to {out_dir}/relatorio.md.
```

- `{planned}` = `last`; `{LLL}` = `last` with 3 digits.
- `{partial_coverage}`: ` (partial coverage: say so in the report)` when `last` < `B`.
- `{not_integrated}`: each batch in `pending` as `{PPP} ({state})`, with state `ok`, `partial` or
  `failed`, separated by `, `; or `none`.
- `{partial_batches}` and `{failed_batches}`: the numbers of the `partial` and `failed` batches, without
  zeros, separated by `, `; or `none`.
- `{images_seen}`, with `{seen}` and `{expected}` added up in this run, `{SSS}` = `start` and `{RRR}` =
  `start` − 1, both with 3 digits:
  - `start` = 1: `Images seen: {seen} of {expected}`
  - 1 < `start` ≤ `last`: `Images seen: {seen} of {expected} in batches {SSS}–{LLL} of this run; batches 001–{RRR}: previous run (see the ledger)`
  - `start` > `last` (only the FINAL): `Images seen: no new batch in this run; batches 001–{LLL}: previous run (see the ledger)`

### 7. CROSS

```text
MODE: CROSS
Run: {run_id}
Objective: {objective_path}
Reports per video:
- {slug}: {report_path} ({status})
Language: {language}
Write the combined report to {run_dir}/relatorio.md.
```

- One `- …` line per video with a report and a status other than `failed`, in the order of `videos`.

### Footer (only in this procedure), after everything

The Workflow validates the responses by schema. Here there is no StructuredOutput, so each call ends with
the role's footer:

```text
There is no StructuredOutput in this call: when you finish, reply only with one JSON line with the fields status ("ok" or "failed"), ledger_path, prompt_path, video_type ("screen", "meeting", "slides", "mixed" or "other"), focus and, if any, warnings (list of texts).
```

```text
There is no StructuredOutput in this call: when you finish, reply only with one JSON line with the fields status ("ok" or "failed"), integrated_through (integer), coverage (list of {"batch": integer, "state": "complete", "incomplete" or "missing"}), prompt_path, focus and, if any, carried and warnings (list of texts).
```

```text
There is no StructuredOutput in this call: when you finish, reply only with one JSON line with the fields status ("ok", "partial" or "failed"), report_path, frames_seen (integer), frames_missing (list of ids; [] if none), notable and, if any, warnings (list of texts).
```

```text
There is no StructuredOutput in this call: when you finish, reply only with one JSON line with the fields status ("ok", "partial" or "failed"), report_path, answered ("yes", "partly" or "no"), summary (up to 120 words; on failure, the reason) and, if any, gaps (list of texts).
```

In order: INIT, NEXT, analyst (texts 3 and 4), FINAL and CROSS.
