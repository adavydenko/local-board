# Dogfood evaluation protocol

This directory is the durable memory for Local Board dogfood evaluations. Scores describe the released artifact tested in a clean consumer repository, not unreleased fixes in this source tree.

## Fixed scenario

Each release is evaluated by a fresh coordinator agent that must:

1. create a new directory and Git repository;
2. create an isolated Python environment and install the exact release from the package index;
3. run Local Board initialization without copying state from an earlier run;
4. define a three-part, dependency-ordered mini-project with explicit success criteria;
5. represent the plan as Local Board issues with descriptions and checklists;
6. use MCP—not SQLite or private HTTP APIs—to discover, claim, update, link, and complete the work;
7. implement and commit the mini-project;
8. run its automated tests and confirm every issue reaches the intended terminal state.

Use the same Focus Board project unless a release makes it technically impossible. Focus Board consists of atomic JSON persistence, an HTTP API and browser UI, and automated tests/documentation.

## Scoring

Score every dimension from 1 to 5. Half-points are allowed.

| Dimension | Weight | A score of 5 means |
| --- | ---: | --- |
| Package installation | 10% | Exact released version installs without source checkout or workaround. |
| Agent discovery/onboarding | 10% | A fresh agent automatically discovers complete, actionable instructions. |
| MCP connection bootstrap | 10% | Connection and authentication are machine-readable and require no undocumented client knowledge. |
| Planning/modeling | 10% | Issues, acceptance criteria, checklists, and dependencies are natural to create and inspect. |
| Execution lifecycle | 15% | Claim, progress, review, completion, conflicts, and dependencies behave predictably. |
| Interaction efficiency | 10% | Routine work needs few calls and little revision bookkeeping or response parsing. |
| Multi-agent orchestration | 10% | A coordinator can safely provision, delegate to, and revoke subagents without SQLite access. |
| Credential/security ergonomics | 10% | Least privilege and secret handling are safe by default and hard to misuse. |
| Runtime operations | 5% | Server startup, readiness, diagnostics, shutdown, and recovery are automation-friendly. |
| Traceability | 10% | Board history, checklist, dependencies, branches, and commits give a complete audit trail. |

The weighted score is descriptive, not a release gate. Preserve raw observations and failures because the same total can hide different product risks.

## Comparison rules

- Do not edit a completed baseline to make a later release look better; append a correction note if evidence was wrong.
- Record the exact package version, Python version, clean repository path, commands, task outcomes, and notable workarounds.
- Separate artifact behavior from changes made in the Local Board source repository after the experiment.
- For the next run, capture elapsed setup time, number of MCP tool calls per issue, authentication/bootstrap steps, warnings, and any secret exposure near-misses.
- Generate release notes from the actual version-to-version Git diff and verified behavior, not from the intended feature list alone.

