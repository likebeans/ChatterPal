# B2 task coaching

This worktree is `feat/task-coaching`, based on first-round recovery commit
`6d67fbc44f5e9c3dd24aa24fe1ff4cf2d7efca3d`. It is not a frozen `LEARNING_BASE`:
INT2's R0 contracts, provider adapter, operation runtime and routing dependencies
are not yet present on that baseline.

## Authored content

The catalog contains three version-1 tasks, each designed for approximately eight
minutes. The duration is a product estimate, not a learning-effect claim.

| Task ID | Content | Required information |
| --- | --- | --- |
| `weekend` | 描述周末 | A past activity, company or being alone, and a feeling or reason |
| `restaurant-order` | 在餐厅点餐 | A menu item and quantity, drink choice or refusal, and a request or question |
| `hotel-check-in` | 办理酒店入住 | Check-in intent and a reservation, the fictional booking name and stay length, and a question |

The weekend task adapts the `daily_conversation/intermediate` material at
`src/chatterpal/services/ai_correction.py` in the baseline commit above. It keeps
the opening question and hiking example, adds a second complete example, and
supplies the missing goals, hints and transfer questions. Restaurant and hotel
content are newly authored. Their menu and booking facts live in the task
snapshot; they do not create a real order or hotel reservation.

Every JSON file includes the roles, objectives, required information, two
examples, target expressions, stable criteria, one coaching step per criterion,
optional sentence starters and simpler questions, separate read-aloud and
free-response prompts, and two authored transfer questions. Other valid wording
is accepted; free response does not require a target sentence.

`TaskCatalog.get_task(task_id, content_version)` selects an exact content version.
When supplying multiple versions to a custom catalog, the last authored entry
for an ID is its current version. Duplicate ID/version pairs fail explicitly.
New content versions must not be substituted into a saved session.

## Pure B2 core

`tasks.py` and `catalog.py` validate and load immutable authored content without
initializing old services, providers, models or a database. `TaskSession` in
`sessions.py` captures that content and the correction preference, appends paired
user/coach rounds, records hint usage, checks revisions, and creates a fixed
finish snapshot. It accepts injected UTC-aware times and preserves raw machine
transcripts. Its JSON round trip is a serialization check, not a claim of
SQLite persistence or browser refresh recovery.

`coaching.py` builds provider messages from the pinned task and paired history.
Learner text stays in user messages; hints are recorded as assistance. The
response parser requires valid structured JSON, preserves a usable reply when
text advice is unverifiable, and allows at most one immediate suggestion with
an exact quote from the current text. Delayed-correction mode requests no
in-conversation corrections. These are prompt and structural checks, not a
claim that a model cannot put unsolicited advice into its reply text.

Finishing is separate from achieving a communication goal. The core records
criteria as `unverified` until a downstream grounded evaluator is connected. It
does not use keywords, turn counts, example similarity, fixed compatibility
scores or provider confidence as evidence of mastery. It makes no acoustic or
phoneme assessment.

## Required integration work

The following work requires INT2's frozen R0 interfaces and A2's atomic repository
boundary. None is simulated by a successful in-memory example:

- Inject the authenticated owner, repository, provider adapter and bounded
  operation runtime into `web/routes/learning_tasks.py`; register it in INT2's
  API entrypoint. Freeze the HTTP task/session representation before mapping
  these domain values to it.
- Reserve and deduplicate each write operation, check expected revision, and
  atomically store the whole new session snapshot together with operation
  completion. A separate generic record update followed by operation completion
  does not meet that requirement.
- Perform provider calls outside transactions. Recognize audio exactly once,
  pass the exact transcript to coaching and storage, and retain successful text
  if TTS fails. Clean uploaded copies at the owning boundary.
- Reject finish when a round operation is pending. Recheck owner existence,
  session state and revision when an asynchronous round completes so a stale
  result cannot revive a finished or deleted session.
- Bind task-condition evidence to real user turns and the fixed source revision;
  leave unavailable or unvalidated evaluations `unverified`.
- Verify temporary SQLite persistence, concurrent request deduplication, deletion
  races, owner isolation, restart recovery and actual HTTP/audio behavior after
  the dependencies are integrated.

No shared contracts, global fixtures, SQL, old services, API entrypoint,
configuration, dependencies or frontend files are modified by this worktree.

## Local checks

From this worktree root:

```bash
uv sync --frozen --python 3.12
uv run --frozen python -m pytest -o addopts='' tests/learning -q -W error::RuntimeWarning
uv run --frozen python -m black --check --workers 1 src/chatterpal/learning tests/learning
uv run --frozen python -m flake8 --max-line-length 88 src/chatterpal/learning tests/learning
uv build --wheel --out-dir dist/b2
```

The repository's full offline suite retains its default 80% coverage gate. Local
B2 tests may override global coverage options for focused verification. Packaged
JSON resources should also be checked from an installed wheel outside the
editable source tree.

After independent review and fixes, all 88 B2 cases passed with 96.72% focused
line coverage (295 / 305 statements). The reviewer separately verified 3,066
mixed round/hint sequences and 27,594 JSON save/restore transitions, rejected
27 malformed snapshot variants, and confirmed that 20 malformed-advice cases
retain their usable reply. These checks cover the pure domain layer.

The final full offline run passed 1,361 tests, with zero failures, errors or
skips, in 268.45 seconds. Full-package line coverage was 6,098 / 7,417 = 82.22%,
passing the unchanged 80% gate. The run retained 41 existing dependency and UI
warnings. Both RuntimeWarning and PytestUnhandledThreadExceptionWarning were
promoted to errors. The exact command from this worktree was:

```bash
HF_HUB_DISABLE_TELEMETRY=1 HF_HUB_OFFLINE=1 uv run --frozen python -m pytest -q \
  --cov-report=xml:dist/b2/coverage-final.xml \
  --junitxml=dist/b2/pytest-results-final.xml \
  -W error::RuntimeWarning -W error::pytest.PytestUnhandledThreadExceptionWarning
```

The initial full run passed 1,323 tests with 82.05% coverage, before the final
regressions were added. It reported 13 blocked Hugging Face telemetry threads;
the final command disables that telemetry at its source without relaxing the
network guard or ignoring thread exceptions. Final JUnit and coverage XML are
under this worktree's ignored `dist/b2/` directory. These results are local
evidence from 2026-10-24, not acceptance of the pending HTTP/SQLite flow.
