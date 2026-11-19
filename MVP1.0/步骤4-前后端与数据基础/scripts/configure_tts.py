"""Create an explicitly authorized TTS configuration; performs no API calls."""
import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from backend.tts import TTSSettings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--enable-live', action='store_true', required=True)
    parser.add_argument('--key-file', type=Path, required=True)
    parser.add_argument('--approval-id', required=True)
    parser.add_argument('--max-calls', type=int, required=True)
    parser.add_argument('--max-chars', type=int, required=True)
    parser.add_argument('--provider', choices=['minimax','doubao'], default='minimax')
    args = parser.parse_args()
    key = args.key_file.expanduser().resolve()
    if not key.is_file():
        parser.error('Credential file must already exist locally; never paste credentials into chat or command arguments')
    settings = TTSSettings(True,str(key),args.approval_id,args.max_calls,args.max_chars,provider=args.provider)
    settings.validate()
    destination = ROOT/'runtime/tts-config.json'
    payload = asdict(settings)
    if destination.exists():
        previous = json.loads(destination.read_text(encoding='utf-8'))
        if previous.get('allow_live') and previous != payload:
            parser.error('Existing authorization differs; review it explicitly before editing, no implicit reset')
    destination.parent.mkdir(parents=True,exist_ok=True)
    destination.touch(mode=0o600,exist_ok=True)
    destination.chmod(0o600)
    destination.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print('TTS configuration saved. No synthesis, credential output, or ledger reset. Restart server to load.')


if __name__ == '__main__':
    main()
