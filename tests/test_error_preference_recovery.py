"""Regression coverage for ASR cause preservation and isolated preference defaults."""
import json
from unittest.mock import Mock

import numpy as np
import pytest

from chatterpal.core.asr.base import ASRBase
from chatterpal.core.errors import AudioInputError, ErrorHandler, SpeechRecognitionError
from chatterpal.services.chat import ChatService
from chatterpal.utils.preferences import UserPreferences


class ProviderASR(ASRBase):
    def __init__(self, failure=None):
        super().__init__({'enable_quality_check': False, 'enable_confidence_check': False})
        self.failure = failure
        self.calls = 0

    def recognize(self, audio_data, **kwargs):
        self.calls += 1
        if self.failure is not None:
            raise self.failure
        return None

    def recognize_file(self, audio_path, **kwargs):
        return self.recognize(audio_path, **kwargs)


@pytest.mark.parametrize('failure,code', [
    (RuntimeError('provider unavailable'), 'ASR_SERVICE_ERROR'),
    (TimeoutError('provider timed out'), 'ASR_TIMEOUT'),
    (PermissionError('audio access denied'), 'AUDIO_PERMISSION_DENIED'),
    (ValueError('invalid audio data'), 'AUDIO_FORMAT_ERROR'),
    (None, 'ASR_NO_SPEECH'),
])
def test_real_enhanced_asr_preserves_failure_category(failure, code, monkeypatch, tmp_path):
    import soundfile as sf
    samples = 0.3 * np.sin(2 * np.pi * 180 * np.arange(16000) / 16000)
    path = tmp_path / 'voice.wav'
    sf.write(path, samples, 16000)
    asr = ProviderASR(failure)
    monkeypatch.setattr('time.sleep', lambda _: None)

    with pytest.raises((AudioInputError, SpeechRecognitionError)) as caught:
        asr.recognize_with_error_handling(str(path), max_retries=1)

    assert caught.value.error_info.code == code
    assert asr.calls == 1
    assert path.exists()


@pytest.mark.parametrize('failure,code', [
    (PermissionError('denied'), 'AUDIO_PERMISSION_DENIED'),
    (ValueError('invalid format'), 'AUDIO_FORMAT_ERROR'),
    (TimeoutError('timeout'), 'ASR_TIMEOUT'),
])
def test_chat_maps_raw_asr_errors_without_calling_llm(failure, code):
    asr = Mock()
    asr.recognize_with_error_handling.side_effect = failure
    llm = Mock()
    service = ChatService(asr=asr, llm=llm)

    with pytest.raises((AudioInputError, SpeechRecognitionError)) as caught:
        service.chat_with_audio_result(b'unused boundary input')

    assert caught.value.error_info.code == code
    llm.chat.assert_not_called()


def test_error_instances_do_not_share_cause_metadata():
    handler = ErrorHandler()
    first = handler.create_error('ASR_SERVICE_ERROR', error_message='first provider')
    second = handler.create_error('ASR_SERVICE_ERROR')
    first.error_info.suggestions.append('first request only')

    assert 'error_message' not in second.error_info.metadata
    assert 'first request only' not in second.error_info.suggestions
    assert handler.get_error_info('ASR_SERVICE_ERROR').metadata == {}


@pytest.mark.parametrize('initial', [None, {'ui': {'theme': 'dark'}}, 'corrupt'])
def test_nested_preferences_reset_from_pristine_defaults(tmp_path, initial):
    path = tmp_path / 'user_preferences.json'
    if initial is not None:
        path.write_text('{' if initial == 'corrupt' else json.dumps(initial))
    preferences = UserPreferences(str(tmp_path))
    preferences.set_input_mode('voice')
    preferences.get('topic.preferred_categories').append('travel')
    preferences.set('chat.voice_settings.recording_timeout', 5)

    assert preferences.reset_to_defaults()
    reloaded = UserPreferences(str(tmp_path))
    assert reloaded.get_input_mode() == 'text'
    assert reloaded.get('topic.preferred_categories') == []
    assert reloaded.get('chat.voice_settings.recording_timeout') == 30


def test_merged_preferences_own_nested_values_from_both_inputs(tmp_path):
    preferences = UserPreferences(str(tmp_path))
    defaults = {'kept': {'items': []}}
    loaded = {'new': {'items': ['travel']}}
    merged = preferences._merge_preferences(defaults, loaded)
    merged['kept']['items'].append('changed')
    merged['new']['items'].append('changed')

    assert defaults == {'kept': {'items': []}}
    assert loaded == {'new': {'items': ['travel']}}


def test_exported_preference_snapshot_cannot_mutate_live_settings(tmp_path):
    preferences = UserPreferences(str(tmp_path))
    snapshot = preferences.get_all_preferences()
    snapshot['chat']['input_mode'] = 'voice'
    snapshot['topic']['preferred_categories'].append('travel')

    assert preferences.get_input_mode() == 'text'
    assert preferences.get('topic.preferred_categories') == []
