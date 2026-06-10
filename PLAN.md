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
- [ ] reclip flake input `speakrs-ffi` + wire dylib path & ORT_DYLIB_PATH into env
- [ ] diarizer.py: ctypes binding (ffmpeg → f32le mono 16k PCM → JSON turns);
      unit tests against a stub dylib, gated integration test with real models
- [ ] llm_client.transcribe: stop discarding `segments` (oMLX already returns them)
- [ ] merge step (pure function): transcript segments × speaker turns → max-overlap
      assignment → diarized transcript ("SPEAKER_00: text" lines + timestamps)
- [ ] naming step: chat-completion prompt (diarized transcript + video metadata →
      JSON {SPEAKER_NN: {name, confidence, evidence}}); threshold gating; works
      against any OpenAI-compatible endpoint (oMLX now, Ollama via URL config)
- [ ] UI: "Speakers" toggle on transcript card; diarized transcript becomes a
      `source` the actions registry can chain from

## Notes
- TTS voice quality unsatisfying with current Qwen3-TTS VoiceDesign — revisit
  model options later (Listen-from-action idea also deferred)
- jj is now colocated in this repo (was plain git); main branch is `main`
