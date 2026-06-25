from __future__ import annotations

import numpy as np
import soundfile as sf

from guitar_helper.playback.audio_buffer import AudioBuffer


def test_mono_buffer_from_file(make_wav):
    path = make_wav(duration_s=2.0, sr=22050)
    buf = AudioBuffer.from_file(path)
    assert buf.sr == 22050
    assert buf.n_channels == 1
    assert buf.data.dtype == np.float32
    assert buf.data.ndim == 2  # (frames, channels)
    assert abs(buf.duration_ms - 2000) <= 5


def test_stereo_native_sr_preserved(tmp_path):
    sr = 44100
    n = sr  # 1 second
    stereo = np.zeros((n, 2), dtype=np.float32)
    stereo[:, 0] = 0.3
    stereo[:, 1] = -0.3
    path = tmp_path / "stereo.wav"
    sf.write(str(path), stereo, sr)

    buf = AudioBuffer.from_file(path)
    assert buf.sr == 44100
    assert buf.n_channels == 2
    assert buf.n_frames == n
    assert np.allclose(buf.data[:, 0], 0.3, atol=1e-3)
    assert np.allclose(buf.data[:, 1], -0.3, atol=1e-3)
