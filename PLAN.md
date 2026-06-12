# PLAN

## Configurable actions migration (phases 1–4 of 5; phase 0 done)
- [x] Phase 0: actions.py registry + default_actions seeding + 22 tests (2026-06-05 EST)
- [x] Phase 1: generic engine, legacy routes wrapped (2026-06-12 EST)
      summarize/translate/counterargue routes become thin wrappers (must stay
      byte-identical — tests compare against current behavior)
- [x] Phase 2: /api/actions + /api/action/<id> (2026-06-12 EST)
      cache gains `actions: {id: {...}}` map with dual-write to legacy flat fields
- [x] Phase 3: registry-driven UI buttons + params panels + error banner (2026-06-12 EST)
- [x] Phase 4: legacy routes/prompt-knobs deleted; prompts live in actions.json (2026-06-12 EST)

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
- [x] "diarized" is a terminal action source (auto-runs pipeline) (2026-06-12 EST)
- [x] word_timestamps + word-level merge + STT metadata bias prompt (2026-06-12 EST)

## Notes
- TTS voice quality unsatisfying with current Qwen3-TTS VoiceDesign — revisit
  model options later (Listen-from-action idea also deferred)
- jj is now colocated in this repo (was plain git); main branch is `main`
