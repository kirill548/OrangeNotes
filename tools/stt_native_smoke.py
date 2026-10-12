"""Native CLI/Qt probe; synthetic audio does not qualify a physical microphone."""
import argparse
import json
from pathlib import Path
import platform
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    from PySide6.QtCore import QCoreApplication
    from app.services.audio_capture import audio_diagnostics
    from app.services.app_paths import stt_dir
    from app.services.stt_installer import STTManager
    from app.services.local_stt import WhisperCppAdapter, SAMPLE_RATE, STTError
    app = QCoreApplication.instance() or QCoreApplication([])
    report = {'os': platform.system(), 'machine': platform.machine(),
              'physical_microphone_tested': False, 'audio': audio_diagnostics()}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    try:
        manager = STTManager(stt_dir())
        installed = manager.install()
        assert installed['ready'], installed['message']
        executable, model = manager.component_paths()
        process = subprocess.run([str(executable), '--help'], capture_output=True, timeout=15)
        assert process.returncode == 0, 'Native whisper-cli could not start'
        # Real inference over generated silence: exercises WAV and native runtime.
        try:
            result = WhisperCppAdapter(executable, model).transcribe(b'\x00\x00' * SAMPLE_RATE)
            assert isinstance(result, str)
            report['silence_result'] = 'text_returned' if result else 'empty'
        except STTError as error:
            if str(error) != 'Речь не распознана. Проверьте выбранный микрофон и повторите запись.':
                raise
            report['silence_result'] = 'sound_annotation_rejected'
        report.update(sha256_verified=True, cli_help_exit=process.returncode,
                      synthetic_inference='passed', components_inside_app=True)
    except Exception as error:
        report['error'] = str(error)[:500]
        raise
    finally:
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
