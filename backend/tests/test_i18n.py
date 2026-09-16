"""Unit tests for language_directive() — the only pipeline-touching piece of i18n."""
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017")
os.environ.setdefault("DB_NAME", "test")

import types
_stub_llm = types.ModuleType("emergentintegrations.llm.chat")
class _LlmChat:
    def __init__(self, *a, **k): pass
class _UserMessage:
    def __init__(self, *a, **k): pass
_stub_llm.LlmChat = _LlmChat
_stub_llm.UserMessage = _UserMessage
sys.modules.setdefault("emergentintegrations", types.ModuleType("emergentintegrations"))
sys.modules.setdefault("emergentintegrations.llm", types.ModuleType("emergentintegrations.llm"))
sys.modules["emergentintegrations.llm.chat"] = _stub_llm

import pipeline  # noqa: E402
from routes.analysis import AnalyzeRequest  # noqa: E402

language_directive = pipeline.language_directive
SUPPORTED_LANGUAGES = pipeline.SUPPORTED_LANGUAGES


def test_english_is_a_true_noop():
    assert language_directive("en") == ""


def test_unsupported_language_falls_back_to_noop():
    assert language_directive("fr") == ""
    assert language_directive("") == ""
    assert language_directive(None) == ""


def test_hindi_directive_names_the_language():
    d = language_directive("hi")
    assert "Hindi" in d


def test_spanish_directive_names_the_language():
    d = language_directive("es")
    assert "Spanish" in d


def test_mandarin_directive_names_the_language():
    d = language_directive("zh")
    assert "Mandarin Chinese" in d


def test_directive_protects_json_keys_and_enums():
    for lang in ("hi", "es", "zh"):
        d = language_directive(lang)
        assert "English" in d  # instructs keys/enums to stay English
        assert "BUY" in d and "SELL" in d and "HOLD" in d


def test_supported_languages_set():
    assert set(SUPPORTED_LANGUAGES.keys()) == {"en", "hi", "es", "zh"}


def test_system_prompt_constants_never_mutated():
    original_pm_sys = pipeline.PM_SYS
    language_directive("hi")
    language_directive("zh")
    assert pipeline.PM_SYS == original_pm_sys
    assert "Respond in" not in pipeline.PM_SYS


def test_analyze_request_defaults_to_english():
    req = AnalyzeRequest(symbol="AAPL")
    assert req.language == "en"


def test_analyze_request_accepts_supported_language():
    req = AnalyzeRequest(symbol="AAPL", language="hi")
    assert req.language == "hi"
