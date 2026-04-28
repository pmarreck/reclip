# Upstream issue draft — jundot/omlx

> Save this somewhere local; copy into the GitHub issue body when filing.
> The PR (against `pyproject.toml` / `requirements`) should be a one-liner.

## Title suggestion

`STT: WhisperProcessor.from_pretrained() always fails because bundled mistral_common is too old for transformers 5.x (real cause behind closed #800)`

## Body

### Summary

oMLX 0.3.8rc1 bundles `transformers==5.6.2` and `mistral_common==1.9.1`. Loading **any** Whisper model via `/v1/audio/transcriptions` fails because `WhisperProcessor.from_pretrained()` triggers an `ImportError` deep in `transformers/tokenization_mistral_common.py`. The error is silently swallowed and the user sees a misleading "missing preprocessor_config.json" message — even on models that have all the processor files in place.

This is the actual root cause behind issue #800 (closed by PR #826). PR #826 only improved the error message; the underlying load still fails.

### Root cause

`transformers/tokenization_mistral_common.py` line 42:

```python
from mistral_common.protocol.instruct.request import ChatCompletionRequest, ReasoningEffort
```

`ReasoningEffort` was added to `mistral_common` in **1.10**. Bundled `mistral_common` is 1.9.1, so the import raises:

```
ImportError: cannot import name 'ReasoningEffort' from 'mistral_common.protocol.instruct.request'
```

`WhisperProcessor.from_pretrained()` walks the transformers module map (via `_class_to_module` -> lazy `__getattr__`) to resolve feature-extractor and tokenizer classes. That walk loads `tokenization_mistral_common.py`, hits the broken import, and propagates up. `mlx_audio/stt/models/whisper/whisper.py` `post_load_hook` catches the exception, sets `model._processor = None`, and emits a generic warning. oMLX's `_validate_stt_processor` then raises the now-canonical "missing preprocessor_config.json / tokenizer files" message — which is wrong: the files are present, the import is broken.

### Reproduction

Any pure-mlx Whisper repo, e.g.:

- `mlx-community/whisper-large-v3-fp16` (with all HF processor files) → fails
- `mlx-community/whisper-large-v3-turbo-8bit` (manually populated with upstream processor files) → fails
- Models that ship complete HF processor configs → still fails

```bash
PYBIN=/Applications/oMLX.app/Contents/Python/cpython-3.11/bin/python3.11
SP=/Applications/oMLX.app/Contents/Python/framework-mlx-framework/lib/python3.11/site-packages
PYTHONPATH="$SP" "$PYBIN" -c "
from transformers import WhisperProcessor
WhisperProcessor.from_pretrained('/Users/.../whisper-large-v3-fp16')
"
# ImportError: cannot import name 'ReasoningEffort' from 'mistral_common.protocol.instruct.request'
```

### Versions installed in the bundle

- `transformers` 5.6.2 (requires `mistral_common>=1.10` for `ReasoningEffort`)
- `mistral_common` 1.9.1 (predates `ReasoningEffort`)
- `mlx-audio` 0.4.3
- oMLX 0.3.8rc1

### Suggested fix

Pin `mistral_common>=1.10` in the `audio` (and/or LLM) optional-dependencies block of `pyproject.toml`. Since `mlx-audio[stt]` already pulls in `mistral_common[audio]` transitively, an explicit floor here ensures no resolver picks 1.9.x:

```diff
 audio = [
     "mlx-audio[tts,stt,sts] @ git+https://github.com/Blaizzy/mlx-audio@5175326...",
     "python-multipart>=0.0.5",
+    # transformers 5.x's tokenization_mistral_common.py imports
+    # ReasoningEffort (added in mistral_common 1.10), so an older
+    # 1.9.x will break WhisperProcessor.from_pretrained().
+    "mistral_common>=1.10",
 ]
```

(Same may be needed in `mlx-audio`'s own `pyproject.toml` so other consumers don't hit this — happy to file a parallel issue at `Blaizzy/mlx-audio`.)

### Workaround for current users (until the bundle ships a fix)

Patch `tokenization_mistral_common.py` to make the `ReasoningEffort` import optional with a stub:

```python
if is_mistral_common_available():
    from mistral_common.protocol.instruct.request import ChatCompletionRequest
    try:
        from mistral_common.protocol.instruct.request import ReasoningEffort
    except ImportError:
        class ReasoningEffort:  # stub for mistral_common<1.10
            pass
    from mistral_common.protocol.instruct.validator import ValidationMode
    ...
```

Confirmed restores `WhisperProcessor.from_pretrained()` and full STT functionality. Mistral-specific reasoning_effort features remain unavailable on the older `mistral_common`, but no oMLX user code I could find references that flag.

### Why the existing diagnostic is misleading

`omlx/engine/stt.py::_missing_processor_hint()` (added in PR #826) tells users to copy `preprocessor_config.json` / `tokenizer.json` / `special_tokens_map.json` from upstream HF. I did this on multiple Whisper variants and the failure persisted, because the actual exception comes from a bundled-package version mismatch, not from missing files. Suggestion: when `_processor` is None, surface the captured `ImportError` instead of attributing it to missing files. Patch sketch:

```python
# in mlx_audio/stt/models/whisper/whisper.py post_load_hook:
except Exception as e:
    model._processor = None
    model._processor_load_error = e   # keep it for later
    warnings.warn(f"Could not load WhisperProcessor: {e}.")

# in omlx/engine/stt.py _validate_stt_processor:
err = getattr(model, "_processor_load_error", None)
if err:
    raise RuntimeError(f"WhisperProcessor failed to load: {err!r}")
```

### Environment

- macOS 15.x, Apple Silicon (M-series)
- oMLX 0.3.8rc1
- Models tested: whisper-large-v3-fp16, whisper-large-v3-turbo-8bit, whisper-large-v3-mlx
