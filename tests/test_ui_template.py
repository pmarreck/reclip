"""Regression contracts for the browser-side interaction template."""
from pathlib import Path


TEMPLATE_PATH = Path(__file__).parents[1] / "templates" / "index.html"


def _copy_text_source(template):
	start = template.index("    async function copyText(idx, opKey) {")
	end = template.index("\n    function _opToSource", start)
	return template[start:end]


def _action_source(template):
	start = template.index("    async function runAction(idx, actionId, params) {")
	end = template.index("\n    async function speakersCard", start)
	return template[start:end]


def _op_to_source(template):
	start = template.index("    function _opToSource(opKey, op) {")
	end = template.index("\n    async function listenText", start)
	return template[start:end]


def _friendly_error_source(template):
	start = template.index("    function friendlyError(err) {")
	end = template.index("\n    document.getElementById('urls')", start)
	return template[start:end]


def test_copy_action_has_clipboard_fallback_for_firefox():
	template = TEMPLATE_PATH.read_text()
	assert "async function copyToClipboard(text)" in template
	assert "navigator.clipboard && window.isSecureContext" in template
	assert "document.execCommand('copy')" in template


def test_copy_action_targets_its_rendered_button():
    template = TEMPLATE_PATH.read_text()
    copy_source = _copy_text_source(template)
    assert 'class="card-dl-btn done copy-btn"' in template
    assert 'data-op="${op}"' in template
    assert "btn.dataset.op === opKey" in copy_source
    assert ".op-result-toggle" not in copy_source


def test_diarize_button_explains_its_extra_speaker_pass():
	template = TEMPLATE_PATH.read_text()
	assert 'onclick="speakersCard(${idx})" title="Do an extra pass to identify individual speakers when there are more than 1.">Diarize</button>' in template


def test_media_type_toggle_uses_intent_labels():
	template = TEMPLATE_PATH.read_text()
	assert 'data-format="video" onclick="setFormat(this)">Video</button>' in template
	assert 'data-format="audio" onclick="setFormat(this)">Audio</button>' in template


def test_diarize_operation_uses_its_user_facing_label():
	template = TEMPLATE_PATH.read_text()
	assert "const builtinOpLabels = { speakers: 'Diarize' };" in template
	assert "builtinOpLabels[op] || op.replace(/_/g, ' ')" in template


def test_gallery_auth_error_survives_generic_403_mapping_and_truncation():
	source = _friendly_error_source(TEMPLATE_PATH.read_text())
	auth_marker = source.index("Gallery authentication required:")
	assert auth_marker < source.index("HTTP Error 403")
	assert "err.split('\\n')[0]" in source


def test_cached_action_keeps_its_server_resolved_source_filename():
	template = TEMPLATE_PATH.read_text()
	assert "filename: data.filename" in _action_source(template)
	assert "if (op?.filename?.endsWith('.txt')) return op.filename.slice(0, -4);" in _op_to_source(template)


def test_diarized_action_outputs_are_visibly_labeled():
	template = TEMPLATE_PATH.read_text()
	assert "const sourceLabel = opState.filename?.includes('-diarized') ? ' (diarized)' : '';" in template
	assert "${label}${sourceLabel}" in template
