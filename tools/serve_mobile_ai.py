"""Explicit opt-in read-only AI service. No cloud, database, or key provisioning.

python tools/serve_mobile_ai.py --config /private/path/ai-relay.json
The administrative file holds certificate paths and separately approved ai_peers.
"""
import argparse
import asyncio
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services.p2p_transport import PeerIdentity, server_context
from app.services.p2p_ai_protocol import AIRelayServer
from app.services.p2p_ai_backend import GroundedOllamaBackend


async def serve(config):
    peers = [PeerIdentity(p['actor_id'], p['workspace_id'], p['fingerprint_sha256'])
             for p in config['ai_peers']]
    if not peers:
        raise ValueError('Explicit AI grants required; sync grants do not authorize AI')
    relay = AIRelayServer(server_context(config['certificate'], config['private_key'], config['ca']),
        peers, GroundedOllamaBackend(model=config.get('model', 'qwen3:4b'),
                                    base_url=config.get('ollama_url', 'http://127.0.0.1:11434')))
    try:
        await relay.start(config.get('host', '127.0.0.1'), config.get('port', 7444))
        print('Orange Notes: read-only AI relay enabled. Stop with Ctrl+C.', flush=True)
        await asyncio.Event().wait()
    finally:
        await relay.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    args = parser.parse_args()
    if args.config.stat().st_size > 65536:
        raise ValueError('Configuration too large')
    try:
        asyncio.run(serve(json.loads(args.config.read_text(encoding='utf-8'))))
    except KeyboardInterrupt:
        pass
