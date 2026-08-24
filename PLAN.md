# PLAN

## Completed: Corrupt cached audio recovery (2026-08-24)
- [x] Reject cached audio that ffprobe cannot decode, then reacquire it instead
      of forwarding a poisoned artifact to transcription or diarization.
      Curiosity poke: validation must accept both current M4A and legacy MP3
      artifacts without turning an absent cache entry into a subprocess call.
      Completed 2026-08-24 13:04 EDT: the malformed live M4A was rejected and
      replaced with a probed 5,875-second AAC artifact.
- [x] Publish ffmpeg extraction output only after it completes and allow long
      recordings enough time to remux; clean staging files after every failure.
      Curiosity poke: a failed lossless copy must not overwrite the last cache
      artifact or prevent the existing MP3 fallback for incompatible codecs.
      Completed 2026-08-24 13:04 EDT: ffmpeg now receives ten minutes and writes
      extension-preserving staging files which replace cache entries atomically.
      Full suite: 382 passed; `nix build` passed.

## Completed: Codec metadata preflight and RedGifs regression (2026-08-18)
- [x] Inspect the selected audio source's yt-dlp metadata before payload
      transfer and use its audio codec to choose lossless M4A remux versus MP3
      transcode; preserve the existing guarded fallback when an extractor
      reports no codec. Curiosity poke: metadata can be missing or wrong, so an
      unknown codec must not prevent the proven ffmpeg fallback from running.
      Completed 2026-08-18 12:50 EDT: live probes reported `mp4a.40.2` for
      YouTube and unknown for RedGifs; the latter still remuxed successfully.
- [x] Restore RedGifs downloads when its yt-dlp formats have dimensions but
      report unknown codecs, without weakening the progressive-video workaround
      used for YouTube transcription audio. Curiosity poke: both default video
      download and audio extraction need an unfiltered non-YouTube fallback,
      while quality choices should still expose RedGifs `sd` and `hd` formats.
      Completed 2026-08-18 12:50 EDT: live default-video and audio downloads
      succeeded against gallery-dl's upstream RedGifs fixture; ffprobe confirmed
      H.264/AAC video and the losslessly remuxed AAC audio artifact. Full suite:
      379 passed; `nix build` passed.

## Completed: Audio acquisition and diarization wording (2026-08-18)
- [x] Use YouTube's cookie-free Android player client to download the smallest
      progressive video-with-audio, then stream-copy AAC into a cached M4A;
      transcode to MP3 only when the source codec cannot be remuxed. Verified
      against `GhUExv6_ud8` with a 15.9 MB, 21:45 AAC artifact and no 403.
      Completed 2026-08-18 09:03 EDT.
      Curiosity poke: preserve normal video-download format selection while
      ensuring direct audio downloads and diarization share the same fix.
- [x] Make the media type and diarization operation labels describe user intent.
      Completed 2026-08-18 09:03 EDT.
      Curiosity poke: retain internal operation/cache identifiers so existing
      API, TTS, and cached result handling remains compatible.

## Completed: Configurable gallery authentication (2026-08-18)
- [x] Make gallery-dl browser-cookie extraction configurable for Firefox,
      Chrome, and Safari; surface clear UI errors when the site requires a
      login or the selected browser lacks usable site authentication.
      Browser-cookie access is opt-in for fresh configurations, and an explicit
      blank overrides a service environment value. Completed 2026-08-18 09:09 EDT.
      Curiosity poke: browser-cookie access must remain opt-in when the server
      is reachable off-loopback, and video/yt-dlp requests must not inherit it.

## Next: RAM-first audio acquisition (2026-08-18)
- [ ] Replace the temporary progressive-video file with a direct `yt-dlp`
      stdout to `ffmpeg` stdin pipe, leaving only the final cached audio on disk.
      Reuse the shipped metadata preflight; use a short `--download-sections`
      sample only when an extractor omits or misreports codec compatibility.
      Curiosity poke: a failed M4A stream copy consumes the pipe, so the rare
      MP3 fallback must deliberately redownload.

## Active: Nix evaluation health (2026-08-17)
- [x] Replace the deprecated Darwin platform predicate in the flake and verify
      the package and test checks evaluate without warnings.
      Curiosity poke: preserve the current platform-specific dynamic-library
      filename on Darwin and Linux while changing only the Nix attribute path.
      Completed 2026-08-17 12:47 EDT: updated ReClip and its pinned
      `speakrs_ffi` input to `stdenv.hostPlatform.isDarwin`; the consumer flake
      evaluation, full test suite, and package build are warning-free.

## Active: Source-aware actions and raw-transcript readability (2026-08-17)
- [x] Rename the explicit diarization control and add its requested tooltip.
      Curiosity poke: the label must make the extra work opt-in without
      obscuring that existing diarized cache entries remain reusable.
      Completed 2026-08-17 12:35 EDT: the control is now **Diarize** with the
      requested description; endpoint, cache, and CLI compatibility remain
      unchanged.
- [x] Prefer an already-cached diarized transcript for the four shipped text
      actions, while retaining raw transcription as the no-extra-work default.
      Cache raw and diarized action variants separately so a later diarization
      never reuses output generated from mixed speaker text.
      Curiosity poke: action chains such as Translate Summary must retain the
      same source lineage as their upstream summary.
      Completed 2026-08-17 12:35 EDT: Summary, both Translate actions, and
      Counterargue select existing diarization without triggering it; their
      separate cache artifacts and UI labels preserve source lineage through
      summary translation and TTS.
- [x] Paragraphize non-diarized transcripts using timestamp pauses and sentence
      boundaries, inserting layout only and preserving transcription content.
      Curiosity poke: timestamp-free backend output still needs a deterministic
      sentence-length fallback; semantic topic segmentation remains a separate,
      language-sensitive evaluation.
      Completed 2026-08-17 12:35 EDT: a pure formatter uses long pauses at
      sentence boundaries, has a sentence-length fallback, and rejects a
      timestamp-derived layout if its word sequence differs from STT output.
- [ ] Evaluate optional semantic topic segmentation on multilingual, noisy
      transcripts before making it an automatic formatting source.
      Keep it separate from lossless paragraphization: lexical segmentation is
      language-sensitive, while an LLM approach adds latency and may rewrite
      user content.

## Active: YouTube speaker-workflow audio fetch (2026-08-17)
- [x] Prefer a verified progressive MP4 source for audio/transcription and
      retain adaptive DASH as a fallback.
      Curiosity poke: a host without a progressive source must still retain
      the prior fallback behavior and expose its final yt-dlp error.
      Completed 2026-08-17 11:44 EDT: `qSlDXTfszT0` downloaded successfully
      through progressive format 18; a deterministic 403 regression test
      verifies retrying the prior adaptive selector only after that fails.

## Active: CLI media-workflow parity (2026-08-17)
- [x] Define and test a stable CLI command surface for every web media workflow.
      Includes download, transcription, speaker diarization, registry actions,
      and text-to-speech. Curiosity poke: the CLI must use the same cached
      artifacts and backend configuration as the web app, rather than reviving
      the previously broken direct audio-only yt-dlp path.
      Completed 2026-08-17 11:15 EDT: contract covers Download, Transcribe,
      Speakers, action registry/actions, Speak, and retained shortcuts.
- [x] Implement the missing CLI commands as thin adapters over shared workflow
      behavior, then preserve the established `info`, `summarize`, and
      `translate` shortcuts. Install the checkout command at `bin/reclip` so
      Peter's existing `~/Code/*/bin` PATH discovery exposes it automatically.
      Curiosity poke: parameterized custom actions need deterministic
      `NAME=VALUE` parsing and the same required-parameter validation as the
      web UI.
      Completed 2026-08-17 11:15 EDT: CLI is a thin adapter over the web
      workflow facade; `bin/reclip` enters the checkout's Nix environment.
- [x] Add hermetic CLI regression coverage for help, media workflows, shared
      output, and error propagation; run the full suite and build.
      Curiosity poke: command tests must not contact yt-dlp, oMLX, or a running
      Flask server.
      Completed 2026-08-17 11:15 EDT: four direct CLI contracts plus 341-test
      full suite and Nix package build passed.
- [x] Update sharing documentation and expected-backend error guidance based on
      the completed readiness review.
      Curiosity poke: a clean machine needs a concrete oMLX/model bootstrap
      path, while non-loopback serving must remain explicitly single-user.
      Completed 2026-08-17 11:15 EDT: README documents oMLX bootstrap,
      `secrets.ini`, current action configuration, and unauthenticated remote
      access boundaries. Missing-model/endpoint errors name the settings to
      correct, and the package now includes all app runtime modules.

## Active: Transcription reliability + summary copy (2026-08-17)
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
- [x] Fix YouTube audio-only 403 regression.
      Completed 2026-08-17 12:42 EDT: direct yt-dlp audio/progressive extraction
      (`-x` / format 18) reproduced HTTP 403 on the live fixture while the normal
      video merge path succeeded; the initial mitigation downloaded the
      `bestvideo+bestaudio/best` source, extracted MP3 locally with ffmpeg, then
      discarded the temporary video source. Revised later the same day when a
      fresh regression proved a progressive source worked where DASH did not.
- [x] Diagnose repeated-token and repeated-segment transcription failure before
      adding any output cleanup.
      Completed 2026-08-17 10:37 EDT: cached `KwOUnk9tjUM` segment diagnostics
      identify a pathological `test` segment (compression ratio 21.33) and a
      repeated phrase loop with mean word probabilities 0.11-0.14; source audio
      is non-silent after the loop begins, so this is an STT inference failure.
- [x] Compare the installed Whisper Turbo candidate against both observed bad
      regions, including the diarization timestamp contract.
      Completed 2026-08-17 10:37 EDT: `whisper-large-v3-turbo-8bit` produced no
      repeated `test` run or duplicate phrase segment, ran about 6x faster on
      the clip tests, and returned word timestamps for all 12 sampled segments.
- [x] Run a context-preserving longer comparison and choose the STT migration
      strategy (Turbo default versus a non-Whisper backend); do not ship a
      transcript cleanup heuristic as the primary fix.
      Completed 2026-08-17 10:42 EDT: Turbo cleanly transcribed the original
      first 480s (77 consecutive word-timestamped segments, including both
      prior failure regions). It is now the tested/default model; Cohere is
      deferred because it lacks timestamps and would break diarization.
- [x] Change the live `config.ini` override to Turbo, reinstall the LaunchAgent,
      and verify the running service sees the new model.
      Completed 2026-08-17 10:42 EDT: `RECLIP_STT_MODEL` now resolves to Turbo
      and `/api/service` confirmed the LaunchAgent is installed and running.
      Cached transcripts remain historical output and require a deliberate
      re-transcription rather than silent cache mutation.
- [x] Fix the Firefox summary Copy action with a browser-compatible clipboard
      fallback and a corrected button-state selector.
      Completed 2026-08-17 10:45 EDT: Copy uses the secure Clipboard API when
      available, falls back to `textarea` plus `execCommand('copy')`, and gives
      feedback on the corresponding Copy button. Browser automation was not
      available on this machine; template regression coverage is deterministic.

## Future product and architecture
- [ ] Multi-user capability with authentication and authorization.
      Scope: identity/session handling, per-user cache/config isolation,
      ownership checks on every media/action endpoint, and a secure remote
      deployment model. Do not expose the current single-user server beyond a
      trusted network before this exists.
      Curiosity poke: gallery-dl browser cookies and user-configured backend
      credentials must never become cross-user ambient authority.
- [ ] Evaluate a Phoenix/Elixir replacement for the Flask application.
      Treat this as a behavior-preserving replacement, not an incremental port:
      first establish black-box API/cache/service contracts, then compare
      Phoenix/LiveView, Oban-style jobs, and per-user persistence against the
      existing local-first workflow. Keep Python only at unavoidable ML/FFI
      adapter boundaries, or replace those with stable external services.
      Curiosity poke: changing runtime should not silently weaken yt-dlp,
      oMLX, speaker-diarization, or cache compatibility.

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
