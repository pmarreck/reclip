# PLAN

## Active: YouTube extractor dependency refresh (2026-08-15)
- [x] Reproduce current locked `yt-dlp` behavior against a live YouTube URL.
      Completed 2026-08-15 11:32 EDT: default live clip did not reproduce 403;
      metadata + audio download both passed, but locked `yt-dlp` warned stale.
- [x] Refresh the Nix-pinned dependency path that provides `yt-dlp`.
      Completed 2026-08-15 11:32 EDT: nixpkgs 2026-04-09 -> 2026-08-13,
      `yt-dlp` 2026.03.17 -> 2026.07.04.
- [x] Re-run live extractor checks and the fast suite.
      Completed 2026-08-15 11:32 EDT: focused live info/audio checks passed,
      full fast suite passed, package build passed.
- [x] Report fork drift against `averygan/reclip`.
      Completed 2026-08-15 11:32 EDT: fork is ahead 60 and behind 10; upstream
      includes an auto-update `yt-dlp` launcher commit not merged here.
- [x] Rebase fork work onto upstream `main`.
      Completed 2026-08-15 15:25 EDT: rebased local `main` onto
      `averygan/reclip` upstream with the 10 upstream commits included; resolved
      the duplicate `/api/playlist` route by keeping the richer local response
      while preserving upstream's `urls` compatibility field.

## ⏸ WIND-DOWN STATE (2026-07-06 — fleet migrating to Thelio; this Mac → darwin-build appliance)
**GREEN. All work committed + pushed to `main` (origin). Working copy clean.**

Shipped this arc (all on `main`, Garnix green):
- **speakrs_ffi** sibling C FFI (github:pmarreck/speakrs_ffi) — the diarization engine
- reclip: flake input, `diarizer.py` (ctypes), `speakers.py` (merge + LLM naming), `/api/diarize` + Speakers UI
- **oMLX 0.4.4** update: Whisper word-timestamps now flow; Parakeet + Qwen3-ASR deleted (~6GB→Trash); Whisper-only STT
- **Sentence-aware merge** — assigns one speaker per sentence; killed the word-level fragmentation (109→78 clean blocks)
- `omlx-state.json` + `scripts/omlx_snapshot.py` + `LOCAL_ENV.md` — diffable record of out-of-repo oMLX state
- Configurable **actions registry** (phases 0–4): `/api/action/<id>`, `~/.config/reclip+/actions.json`, legacy routes removed
- Threads: honest "no extractor exists" handling; blank-line diarized formatting
- Abbreviation handling: **MEASURED (0 harm), not shipped**; ready constant parked below

### ⚠ Resume notes for the Thelio (CRITICAL — env changes on migration)
- **oMLX stays on the MAC** (darwin appliance), serving `localhost:8000` there. On the Thelio (linux),
  reclip's `RECLIP_STT_URL` / `RECLIP_SUMMARIZE_URL` / `RECLIP_TTS_URL` must be repointed from
  `localhost:8000` → the **Mac's tailscale IP:8000** in `~/.config/reclip+/config.ini`. (Or stand up an
  oMLX-equivalent on the Thelio.) Until then, STT/LLM/TTS 500 with connection-refused.
- **speakrs_ffi** rebuilds fine on linux (cpu mode; ORT via `ORT_DYLIB_PATH`, both wired by the flake).
- Config + secrets live in `~/.config/reclip+/{config.ini,secrets.ini,actions.json}` — NOT in the repo; recreate on Thelio.
- Only the data-centers video has word-timestamped cached segments; other cached transcripts predate 0.4.4.

### Next steps (when resumed on Thelio)
1. Repoint `RECLIP_*_URL` at the Mac's oMLX over tailscale (config.ini), verify a transcribe round-trips.
2. (optional) Re-run Speakers on a second video to get a 2nd data point for the diarization merge.
3. Actions registry backlog: Listen-from-action; per-action model/provider + API-key config (deferred).
4. Diarization (parked): ship the abbreviation constant ONLY if a transcript shows nonzero within-segment
   splits — re-run the measurement logic in the "abbreviation handling" item below.

---

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
- Source control is direct `git`; main branch is `main`

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
