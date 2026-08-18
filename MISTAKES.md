# MISTAKES

- **2026-08-18: Chained repository state changes in one shell command.** I ran
  `git add ... && git commit ...` despite the instruction to keep commands
  separate. Use one tool invocation per state-changing Git command so each
  result is independently visible and failures cannot hide intermediate state.
- **2026-06-12: Regex block-deletion ate neighboring functions — TWICE in one
  session.** Deleting "from `def X` to the next `def`" consumed the
  `@app.route("/")` decorator of the *following* function (turning / into a
  404 that no API test caught), and later "from route X to the next
  `@app.route`" consumed 8 TTS helper functions that lived between two routes.
  Lessons: (1) delete blocks only up to an EXPLICITLY NAMED next anchor, never
  a generic pattern; (2) after any mechanical sweep, diff the file and read the
  list of deleted defs before running tests; (3) keep a rendering/route-level
  test (`GET /`) — unit suites can stay green while the app is broken.
- **2026-06-10: Vacuous mutation test.** A merge-accumulation mutation
  initially survived because the test data let the buggy path win anyway.
  Mutation checks are only as good as the data's discriminating power — design
  fixtures where the correct algorithm and the plausible-bug diverge.
