"""
Tests for Assessment (Pronunciation Assessment) modules.
"""

import io
import tempfile
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np
import pytest

from chatterpal.core.assessment.base import (
    AssessmentBase, AssessmentError, AssessmentResult, 
    WordAnalysis, PhonemeAnalysis, ProsodyFeatures
)
from chatterpal.core.assessment.prosody import ProsodyAnalyzer
from chatterpal.core.assessment.phoneme import PhonemeAnalyzer
from chatterpal.core.assessment.corrector import PronunciationCorrector


@pytest.fixture
def assessment_audio(tmp_path):
    """Use a valid local WAV for decoding and feature extraction."""
    sample_rate = 16000
    times = np.arange(sample_rate) / sample_rate
    samples = (0.4 * np.sin(2 * np.pi * 180 * times)).astype(np.float32)
    audio_buffer = io.BytesIO()
    with wave.open(audio_buffer, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        output.writeframes((samples * 32767).astype(np.int16).tobytes())
    audio_bytes = audio_buffer.getvalue()
    audio_path = tmp_path / "assessment.wav"
    audio_path.write_bytes(audio_bytes)
    return SimpleNamespace(
        path=str(audio_path), audio_bytes=audio_bytes, gradio=(sample_rate, samples)
    )


@pytest.fixture
def prosody_sdk(monkeypatch):
    """Replace myprosody's Praat entry points, retaining feature mapping."""
    from chatterpal.core.assessment import prosody as module

    sdk = SimpleNamespace(
        myspsyl=Mock(return_value=7),
        mysppaus=Mock(return_value=2),
        myspsr=Mock(return_value=137.0),
        myspatc=Mock(return_value=163.0),
    )
    monkeypatch.setattr(module, "mysp", sdk)
    return sdk


class MockAssessment(AssessmentBase):
    """Mock Assessment implementation for testing base class functionality."""
    
    def __init__(self, config=None, should_fail=False):
        super().__init__(config)
        self.should_fail = should_fail
        self.assess_calls = []
        
    def assess(self, audio_data, target_text="", **kwargs):
        self.assess_calls.append((audio_data, target_text, kwargs))
        if self.should_fail:
            raise AssessmentError("Mock assessment failure")
        
        return AssessmentResult(
            overall_score=0.8,
            fluency_score=0.7,
            pronunciation_score=0.8,
            prosody_score=0.9,
            accuracy_score=0.75,
            prosody_features=ProsodyFeatures(),
            recognized_text="mock recognized text",
            target_text=target_text,
            feedback="Mock assessment feedback"
        )


class TestWordAnalysis:
    """Test the WordAnalysis dataclass."""
    
    def test_initialization(self):
        """Test WordAnalysis initialization."""
        analysis = WordAnalysis(
            target_word="hello",
            recognized_word="helo",
            is_correct=False,
            confidence_score=0.8
        )
        
        assert analysis.target_word == "hello"
        assert analysis.recognized_word == "helo"
        assert analysis.is_correct is False
        assert analysis.confidence_score == 0.8
        assert analysis.phonetic_info == {}
        assert analysis.correction_tips == []


class TestPhonemeAnalysis:
    """Test the PhonemeAnalysis dataclass."""
    
    def test_initialization(self):
        """Test PhonemeAnalysis initialization."""
        analysis = PhonemeAnalysis(
            phoneme="æ",
            error_type="substitution",
            description="Vowel substitution error",
            severity="high"
        )
        
        assert analysis.phoneme == "æ"
        assert analysis.error_type == "substitution"
        assert analysis.description == "Vowel substitution error"
        assert analysis.severity == "high"
        assert analysis.correction_method == []


class TestProsodyFeatures:
    """Test the ProsodyFeatures dataclass."""
    
    def test_initialization_defaults(self):
        """Test ProsodyFeatures initialization with defaults."""
        features = ProsodyFeatures()
        
        assert all(value is None for value in vars(features).values())
    
    def test_initialization_custom(self):
        """Test ProsodyFeatures initialization with custom values."""
        features = ProsodyFeatures(
            speaking_rate=140.0,
            f0_mean=180.0,
            fluency_score=0.9
        )
        
        assert features.speaking_rate == 140.0
        assert features.f0_mean == 180.0
        assert features.fluency_score == 0.9


class TestAssessmentResult:
    """Test the AssessmentResult dataclass."""
    
    def test_initialization(self):
        """Test AssessmentResult initialization."""
        prosody_features = ProsodyFeatures(speaking_rate=130.0)
        word_analysis = [WordAnalysis("hello", "hello", True)]
        
        result = AssessmentResult(
            overall_score=0.85,
            fluency_score=0.8,
            pronunciation_score=0.9,
            prosody_score=0.8,
            accuracy_score=0.85,
            prosody_features=prosody_features,
            word_analysis=word_analysis,
            feedback="Good pronunciation"
        )
        
        assert result.overall_score == 0.85
        assert result.fluency_score == 0.8
        assert result.prosody_features.speaking_rate == 130.0
        assert len(result.word_analysis) == 1
        assert result.feedback == "Good pronunciation"
    
    def test_to_dict(self):
        """Test AssessmentResult to_dict conversion."""
        word_analysis = [WordAnalysis("hello", "hello", True, confidence_score=0.9)]
        phoneme_analysis = [PhonemeAnalysis("æ", "correct", "Good pronunciation")]
        
        result = AssessmentResult(
            overall_score=0.85,
            fluency_score=0.8,
            pronunciation_score=0.9,
            prosody_score=0.8,
            accuracy_score=0.85,
            prosody_features=ProsodyFeatures(),
            word_analysis=word_analysis,
            phoneme_analysis=phoneme_analysis,
            feedback="Good job"
        )
        
        result_dict = result.to_dict()
        
        assert result_dict["overall_score"] == 0.85
        assert "detailed_scores" in result_dict
        assert "prosody_features" in result_dict
        assert "word_analysis" in result_dict
        assert "phoneme_analysis" in result_dict
        assert len(result_dict["word_analysis"]) == 1
        assert len(result_dict["phoneme_analysis"]) == 1


class TestAssessmentBase:
    """Test the Assessment base class functionality."""
    
    def test_initialization(self):
        """Test Assessment base class initialization."""
        assessment = MockAssessment()
        assert assessment.config == {}
        assert assessment.logger is not None
        
        config = {"test": "value"}
        assessment_with_config = MockAssessment(config)
        assert assessment_with_config.config == config
    
    def test_validate_audio_data_bytes(self, assessment_audio):
        """Test audio data validation with valid WAV bytes."""
        assessment = MockAssessment()
        assert assessment.validate_audio_data(assessment_audio.audio_bytes) is True
        assert assessment.validate_audio_data(b"") is False
        assert assessment.validate_audio_data(None) is False

    def test_validate_audio_data_file_path(self, assessment_audio, tmp_path):
        """Validate existing, missing, and empty audio files."""
        assessment = MockAssessment()
        assert assessment.validate_audio_data(assessment_audio.path) is True
        assert assessment.validate_audio_data(str(tmp_path / "missing.wav")) is False
        empty_path = tmp_path / "empty.wav"
        empty_path.touch()
        assert assessment.validate_audio_data(str(empty_path)) is False
    
    def test_validate_audio_data_gradio_tuple(self):
        """Test audio data validation with Gradio tuple."""
        assessment = MockAssessment()
        
        # Valid tuple
        audio_array = np.array([0.1, 0.2, 0.3])
        audio_data = (16000, audio_array)
        assert assessment.validate_audio_data(audio_data) is True
        
        # Invalid tuple format
        assert assessment.validate_audio_data((16000,)) is False
        assert assessment.validate_audio_data((16000, None)) is False
        assert assessment.validate_audio_data((16000, [])) is False
    
    def test_validate_audio_data_invalid_type(self):
        """Test audio data validation with invalid type."""
        assessment = MockAssessment()
        
        assert assessment.validate_audio_data(123) is False
        assert assessment.validate_audio_data([1, 2, 3]) is False
    
    def test_convert_audio_to_file_bytes(self, assessment_audio, tmp_path):
        """Convert bytes to a real, decodable file without mocking file I/O."""
        assessment = MockAssessment()
        output_path = tmp_path / "output.wav"
        result = assessment.convert_audio_to_file(assessment_audio.audio_bytes, str(output_path))
        assert result is True
        assert output_path.read_bytes() == assessment_audio.audio_bytes
        with wave.open(str(output_path), "rb") as output:
            assert output.getframerate() == 16000
            assert output.getnframes() == 16000

    def test_convert_audio_to_file_tuple(self, assessment_audio, tmp_path):
        """Convert Gradio samples and inspect the encoded PCM frames."""
        assessment = MockAssessment()
        output_path = tmp_path / "tuple.wav"
        result = assessment.convert_audio_to_file(assessment_audio.gradio, str(output_path))
        assert result is True
        with wave.open(str(output_path), "rb") as output:
            assert output.getframerate() == assessment_audio.gradio[0]
            assert output.getnframes() == len(assessment_audio.gradio[1])
            encoded_samples = np.frombuffer(output.readframes(-1), dtype=np.int16) / 32768
        np.testing.assert_allclose(encoded_samples, assessment_audio.gradio[1], atol=1 / 32768)
    
    def test_convert_audio_to_file_invalid(self):
        """Test converting invalid audio data to file."""
        assessment = MockAssessment()
        
        result = assessment.convert_audio_to_file("invalid", "output.wav")
        assert result is False
    
    def test_detect_language(self):
        """Test language detection."""
        assessment = MockAssessment()
        
        assert assessment.detect_language("Hello world") == "en"
        assert assessment.detect_language("你好世界") == "zh"
        assert assessment.detect_language("Hello 世界") == "zh"  # Mixed, Chinese detected
        assert assessment.detect_language("") == "en"  # Default
        assert assessment.detect_language("123456") == "en"  # Default
    
    def test_calculate_text_similarity(self):
        """Test text similarity calculation."""
        assessment = MockAssessment()
        
        # Identical texts
        assert assessment.calculate_text_similarity("hello world", "hello world") == 1.0
        
        # Completely different texts
        assert assessment.calculate_text_similarity("hello", "goodbye") == 0.0
        
        # Partially similar texts
        similarity = assessment.calculate_text_similarity("hello world", "hello there")
        assert 0 < similarity < 1
        
        # Empty texts
        assert assessment.calculate_text_similarity("", "") == 1.0
        assert assessment.calculate_text_similarity("hello", "") == 0.0
    
    @patch('librosa.get_duration')
    def test_estimate_audio_duration_file(self, mock_get_duration):
        """Test audio duration estimation from file."""
        mock_get_duration.return_value = 3.5
        
        assessment = MockAssessment()
        duration = assessment.estimate_audio_duration("test.wav")
        assert duration == 3.5
    
    def test_estimate_audio_duration_tuple(self):
        """Test audio duration estimation from Gradio tuple."""
        assessment = MockAssessment()
        
        audio_array = np.zeros(16000)  # 1 second at 16kHz
        audio_data = (16000, audio_array)
        
        duration = assessment.estimate_audio_duration(audio_data)
        assert duration == 1.0
    
    def test_estimate_audio_duration_fallback(self):
        """Test audio duration estimation fallback."""
        assessment = MockAssessment()
        
        # Should return default value for unsupported types
        duration = assessment.estimate_audio_duration(b"bytes")
        assert duration == 1.0
    
    def test_create_default_result(self):
        """Test creating default assessment result."""
        assessment = MockAssessment()
        
        result = assessment.create_default_result("Test error")
        assert isinstance(result, AssessmentResult)
        assert result.overall_score is None
        assert all(item['status'] == 'unavailable' and item['score'] is None for item in result.dimensions.values())
        assert result.feedback == "Test error"
        assert len(result.suggestions) > 0
    
    def test_test_functionality_success(self):
        """Run the real synthetic-audio functionality check."""
        assessment = MockAssessment()
        assert assessment.test_functionality() is True
        audio_data, target_text, _ = assessment.assess_calls[0]
        assert audio_data[0] == 16000
        assert audio_data[1].shape == (16000,)
        assert target_text == "test"

    def test_test_functionality_failure(self):
        """The functionality probe reports assessment failure."""
        assessment = MockAssessment(should_fail=True)
        assert assessment.test_functionality() is False
        assert len(assessment.assess_calls) == 1


class TestProsodyAnalyzer:
    """Test the Prosody Analyzer implementation."""
    
    def test_initialization(self):
        """Test ProsodyAnalyzer initialization."""
        config = {"sample_rate": 16000}
        analyzer = ProsodyAnalyzer(config)
        assert analyzer.config == config
    
    def test_analyze_prosody_success(self, assessment_audio, prosody_sdk):
        """Map the actual myprosody calls into a ProsodyFeatures result."""
        analyzer = ProsodyAnalyzer()
        result = analyzer.assess(assessment_audio.audio_bytes, "test text")
        assert result["analysis_success"] is True
        assert result["error"] is None
        features = result["prosody_features"]
        assert isinstance(features, ProsodyFeatures)
        assert features.speaking_rate == 137.0
        assert features.articulation_rate == 163.0
        assert features.syllable_count == 7
        assert features.pause_count == 2
        prosody_sdk.myspsr.assert_called_once()
        name, directory = prosody_sdk.myspsr.call_args.args
        assert not name.endswith(".wav")
        assert not (Path(directory) / f"{name}.wav").exists()
        # The fixture contains a measured 180 Hz tone, not the default 150 Hz.
        assert features.f0_mean == pytest.approx(180.0, abs=2.0)

    def test_analyze_prosody_failure(self, assessment_audio, prosody_sdk):
        """An unusable backend must not be reported as successful analysis."""
        for function in vars(prosody_sdk).values():
            function.side_effect = RuntimeError("Analysis failed")
        analyzer = ProsodyAnalyzer()
        result = analyzer.assess(assessment_audio.audio_bytes)
        assert result['analysis_status'] == 'partial'
        assert result['prosody_features'].f0_mean == pytest.approx(180.0, abs=2.0)
        assert result['prosody_features'].speaking_rate is None
        assert result['feature_metadata']['speaking_rate']['status'] == 'unavailable'
        assert result['feature_metadata']['f0_mean']['status'] == 'measured'
        assert 'Analysis failed' in result['error']

    @pytest.mark.parametrize("analyzer_type", [ProsodyAnalyzer, PhonemeAnalyzer])
    def test_assess_with_file_path(self, assessment_audio, prosody_sdk, analyzer_type):
        """A caller-owned WAV survives analysis even under the system temp root."""
        analyzer = analyzer_type()
        # Preserve tempfile's spelling: macOS /var and /private/var aliases
        # otherwise bypass the production prefix-based deletion branch.
        with tempfile.TemporaryDirectory(dir=analyzer.temp_dir) as directory:
            audio_path = Path(directory) / "caller.wav"
            audio_path.write_bytes(assessment_audio.audio_bytes)
            result = analyzer.assess(str(audio_path), "test text")
            assert isinstance(result, dict)
            assert result["analysis_success"] is True
            if analyzer_type is ProsodyAnalyzer:
                assert isinstance(result["prosody_features"], ProsodyFeatures)
                prosody_sdk.myspsr.assert_called_once_with("caller", directory)
            assert audio_path.exists(), "Analyzer deleted the caller's WAV"
            assert audio_path.read_bytes() == assessment_audio.audio_bytes


class TestPhonemeAnalyzer:
    """Test the Phoneme Analyzer implementation."""
    
    def test_initialization(self):
        """Test PhonemeAnalyzer initialization."""
        config = {"language": "en"}
        analyzer = PhonemeAnalyzer(config)
        assert analyzer.config == config
    
    def test_analyze_phonemes_success(self, assessment_audio):
        """Exercise actual local Praat analysis through the current assess API."""
        analyzer = PhonemeAnalyzer()
        analysis = analyzer.assess(assessment_audio.audio_bytes, "hello world")
        assert isinstance(analysis, dict)
        assert analysis["analysis_success"] is True
        assert analysis["duration"] == pytest.approx(1.0)
        assert analysis["sample_rate"] == 16000
        assert analysis["pitch"]["mean"] == pytest.approx(180.0, abs=2.0)
        assert analysis["quality_score"] is None
        assert analysis["dimensions"]["quality"]["status"] == "unavailable"
        assert {"formants", "intensity", "spectral"} <= analysis.keys()

    def test_assess_with_gradio_tuple(self, assessment_audio):
        """Convert real Gradio samples and analyze them without a model or cloud."""
        analyzer = PhonemeAnalyzer()
        result = analyzer.assess(assessment_audio.gradio, "test")
        assert isinstance(result, dict)
        assert result["analysis_success"] is True
        assert result["duration"] == pytest.approx(1.0)
        assert result["sample_rate"] == 16000
        assert result["pitch"]["mean"] == pytest.approx(180.0, abs=2.0)


class TestPronunciationCorrector:
    """Test the Pronunciation Corrector implementation."""
    
    def test_initialization(self):
        """Test PronunciationCorrector initialization."""
        config = {"correction_threshold": 0.7}
        corrector = PronunciationCorrector(config)
        assert corrector.config == config
    
    def test_assess_comprehensive(self, assessment_audio):
        """Comprehensive assessment must retain actual phoneme analysis."""
        corrector = PronunciationCorrector()
        # The corrector has no component-analyzer attributes to patch. Exercise
        # its public API so its currently unimplemented integration stays visible.
        result = corrector.assess(assessment_audio.path, "hello world")
        assert isinstance(result, AssessmentResult)
        assert result.overall_score is None
        assert result.dimensions["overall"]["status"] == "unavailable"
        assert result.feature_metadata["f0_mean"]["source"] == "parselmouth"
        assert result.target_text == "hello world"
        assert result.audio_duration == pytest.approx(1.0)
        assert len(result.phoneme_analysis) > 0, "Comprehensive assessment returned no phoneme analysis"
    
    def test_generate_feedback(self):
        """Test feedback generation."""
        corrector = PronunciationCorrector()
        
        # Corrector feedback formats and compares scores on a 0-100 scale.
        result = AssessmentResult(
            overall_score=70.0,
            fluency_score=60.0,
            pronunciation_score=80.0,
            prosody_score=70.0,
            accuracy_score=75.0,
            prosody_features=ProsodyFeatures()
        )

        feedback = corrector._generate_comprehensive_feedback(result)
        assert isinstance(feedback, str)
        assert len(feedback) > 0
    
    def test_generate_suggestions(self):
        """Test suggestion generation."""
        corrector = PronunciationCorrector()
        
        result = AssessmentResult(
            overall_score=60.0,
            fluency_score=50.0,
            pronunciation_score=70.0,
            prosody_score=60.0,
            accuracy_score=65.0,
            prosody_features=ProsodyFeatures(speaking_rate=80.0)
        )

        suggestions = corrector._generate_improvement_suggestions(result)
        assert isinstance(suggestions, list)
        assert len(suggestions) > 0
        # A raw rate without units/provenance cannot support a speed diagnosis.
        assert any("识别错误" in suggestion for suggestion in suggestions)
        assert all("过慢" not in suggestion for suggestion in suggestions)

if __name__ == "__main__":
    pytest.main([__file__])







