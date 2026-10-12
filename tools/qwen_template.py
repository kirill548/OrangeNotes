"""Explicit ChatML import recipe for Qwen2.5 Instruct GGUF qualification.

Source: https://github.com/QwenLM/Qwen3/blob/main/docs/source/run_locally/ollama.md
This is the no-tools chat subset. Orange Notes supplies its own grounding system
message; the model recipe deliberately adds no canned refusal or benchmark answer.
"""
import argparse
from pathlib import Path

QWEN_CHAT_TEMPLATE = '''{{ if .Messages }}{{ if .System }}<|im_start|>system
{{ .System }}<|im_end|>
{{ end }}{{ range .Messages }}{{ if ne .Role "system" }}<|im_start|>{{ .Role }}
{{ .Content }}<|im_end|>
{{ end }}{{ end }}<|im_start|>assistant
{{ else }}{{ if .System }}<|im_start|>system
{{ .System }}<|im_end|>
{{ end }}{{ if .Prompt }}<|im_start|>user
{{ .Prompt }}<|im_end|>
{{ end }}<|im_start|>assistant
{{ end }}{{ .Response }}'''


def build_modelfile(gguf, *, num_ctx=2048, device='auto'):
    """Generate only an import recipe; never mutate any existing Ollama tag."""
    source = Path(gguf).resolve(strict=True)
    if not source.is_file() or source.suffix.lower() != '.gguf':
        raise ValueError('An existing GGUF file is required')
    if not isinstance(num_ctx, int) or isinstance(num_ctx, bool) or not 512 <= num_ctx <= 32768:
        raise ValueError('num_ctx must be an integer between 512 and 32768')
    if device not in ('auto', 'cpu'):
        raise ValueError('device must be auto or cpu')
    name = source.as_posix()
    if any(character in name for character in ('"', '\n', '\r')):
        raise ValueError('Unsafe Modelfile source path')
    return (f'FROM "{name}"\nTEMPLATE """{QWEN_CHAT_TEMPLATE}"""\n'
            'PARAMETER stop "<|im_start|>"\n'
            'PARAMETER stop "<|im_end|>"\n'
            'PARAMETER stop "<|endoftext|>"\n'
            f'PARAMETER num_ctx {num_ctx}\n'
            + ('PARAMETER num_gpu 0\n' if device == 'cpu' else ''))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--gguf', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--num-ctx', type=int, default=2048)
    parser.add_argument('--device', choices=('auto', 'cpu'), default='auto')
    args = parser.parse_args()
    content = build_modelfile(args.gguf, num_ctx=args.num_ctx, device=args.device)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(content, encoding='utf-8')


if __name__ == '__main__':
    main()
