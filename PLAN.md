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

- [x] word_timestamps + sentence-aware merge (2026-06-20): oMLX 0.4.4 emits
      word timestamps; merge assigns one speaker per SENTENCE (majority overlap),
      changing speaker only at sentence boundaries — kills mid-utterance
      fragmentation. data-centers re-render: 78 clean blocks vs 109 shredded.
- [x] (2026-06-21) abbreviation handling — MEASURED, NOT NEEDED. Scanned the
      469-segment data-centers transcript: within-segment speaker splits = 0,
      abbreviation-induced splits = 0. Whisper segments are sentence-grained
      (avg 9.3 words, 78% end in terminal punctuation, only 1 multi-sentence
      segment), so the within-segment split where an abbreviation could mislead
      essentially never fires. Ready-to-ship constant parked below if long-
      segment content (rambling podcasts) ever shows within-segment splits.
- [ ] (future, parked) sentences spanning Whisper segments; explicit
      mid-sentence-split detector. Revisit only if a transcript shows nonzero
      within-segment speaker splits (re-run the measurement in /tmp/measure_abbrev.py).

  Parked ABBREVIATIONS constant (drop into speakers.py + guard in _ends_sentence
  only if measurement turns nonzero):
    frozenset({"mr.","mrs.","ms.","dr.","prof.","sr.","jr.","st.","rev.","hon.",
      "gen.","sen.","rep.","gov.","lt.","sgt.","col.","capt.","inc.","corp.",
      "ltd.","co.","llc.","dept.","e.g.","i.e.","etc.","vs.","al.","cf.","approx.",
      "u.s.","u.k.","u.s.a.","d.c.","n.j.","n.y.","a.m.","p.m.","jan.","feb.","mar.",
      "apr.","jun.","jul.","aug.","sep.","sept.","oct.","nov.","dec.","no.","vol."})
    plus regex for acronyms (^([a-z]\.)+$) and single initials (^[a-z]\.$).
