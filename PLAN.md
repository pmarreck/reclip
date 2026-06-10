# PLAN

## Configurable actions migration (phases 1–4 of 5; phase 0 done)
- [x] Phase 0: actions.py registry + default_actions seeding + 22 tests (2026-06-05 EST)
- [ ] Phase 1: generic `_run_action(url, action_id, params)` engine; existing
      summarize/translate/counterargue routes become thin wrappers (must stay
      byte-identical — tests compare against current behavior)
- [ ] Phase 2: `GET /api/actions` + `POST /api/action/<id>` + SSE stream route;
      cache gains `actions: {id: {...}}` map with dual-write to legacy flat fields
- [ ] Phase 3: frontend renders action buttons dynamically from /api/actions;
      params spec drives inline inputs; accordion per-action output panels;
      surface actions.last_error as a UI banner (hot-reload kept last-good)
- [ ] Phase 4: delete legacy routes + flat cache fields + RECLIP_*_PROMPT config knobs

## Speaker diarization + naming (engine shipped as sibling project speakrs_ffi)
- [x] speakrs_ffi sibling project: C FFI around speakrs, Garnix-built
      (2026-06-10 EST: 21.5-min video → 5.8s warm, 8 speakers, 207 turns)
- [x] reclip flake input `speakrs-ffi` + dylib/ORT env wiring (2026-06-10 EST)
- [x] diarizer.py ctypes binding, 13 tests incl. real-dylib tier (2026-06-10 EST)
- [x] llm_client.transcribe returns segments; cached as transcript_segments.json (2026-06-10 EST)
- [x] speakers.py merge/format/label-map, mutation-validated (2026-06-10 EST)
- [x] naming via configured chat endpoint, confidence-gated (2026-06-10 EST)
- [x] /api/diarize + Speakers button; live e2e verified: 21.5-min video → 8 speakers,
      2 confidently named (Herndon/Winder), 6 correctly left generic (2026-06-10 EST)
- [ ] diarized transcript as an actions-registry `source` (after actions phase 1-2)
- [ ] consider word_timestamps for finer merge alignment (interjections sometimes
      land in the neighboring speaker's block at segment granularity)

## Notes
- TTS voice quality unsatisfying with current Qwen3-TTS VoiceDesign — revisit
  model options later (Listen-from-action idea also deferred)
- jj is now colocated in this repo (was plain git); main branch is `main`
