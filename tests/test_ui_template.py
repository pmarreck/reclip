"""Regression contracts for the browser-side interaction template."""
from pathlib import Path


TEMPLATE_PATH = Path(__file__).parents[1] / "templates" / "index.html"


def _copy_text_source(template):
	start = template.index("    async function copyText(idx, opKey) {")
	end = template.index("\n    function _opToSource", start)
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
