"""Tests for voice I/O — must never raise, even without termux-api."""

def test_voice_availability_checks_never_raise():
    from mytermux import voice
    assert isinstance(voice.is_stt_available(), bool)
    assert isinstance(voice.is_tts_available(), bool)
    assert isinstance(voice.is_mic_available(), bool)
    assert isinstance(voice.tts_engines(), list)


def test_stt_returns_none_without_api():
    from mytermux import voice
    # In CI/sandbox, termux-speech-to-text doesn't exist, so should return None, not raise
    result = voice.stt(timeout=0.5)
    assert result is None or isinstance(result, str)


def test_tts_returns_bool_without_api():
    from mytermux import voice
    ok = voice.tts("hello test", persona="nova")
    assert isinstance(ok, bool)
    # Without API, should be False
    assert ok is False


def test_tts_cleaning():
    from mytermux.voice import _clean_for_tts, _chunk_for_tts
    raw = "<think>private reasoning</think> Hello **world**! ```code``` This is a test."
    clean = _clean_for_tts(raw)
    assert "private reasoning" not in clean
    assert "code" not in clean
    assert "Hello" in clean

    long_text = "Hello. " * 100
    chunks = _chunk_for_tts(long_text, max_len=50)
    assert len(chunks) > 1
    assert all(len(c) <= 60 for c in chunks)


def test_listen_once_without_api_returns_none():
    from mytermux import voice
    result = voice.listen_once(prompt_text="", timeout=0.5, speak_prompt=False)
    assert result is None or isinstance(result, str)


def test_clipboard_smart_never_raises():
    from mytermux import voice
    result = voice.clipboard_smart()
    assert result is None or isinstance(result, str)


def test_capture_photo_without_api_returns_none():
    from mytermux import voice
    result = voice.capture_photo()
    assert result is None or hasattr(result, "exists")


def test_vibrate_feedback_never_raises():
    from mytermux import voice
    voice.vibrate_feedback("tap")
    voice.vibrate_feedback("success")
    voice.vibrate_feedback("error")
    voice.vibrate_feedback("listen")
