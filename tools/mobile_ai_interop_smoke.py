"""Real Dart DesktopAiGateway -> Python AI relay on loopback, PUBLIC TEST keys.

Usage: python tools/mobile_ai_interop_smoke.py --flutter /path/to/flutter
Requires flutter pub get. Uses a deterministic backend, never a live model.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.services.p2p_ai_protocol import AIRelayServer
from app.services.p2p_transport import PeerIdentity, server_context

W = '10000000-0000-4000-8000-000000000001'
A = '20000000-0000-4000-8000-000000000001'
B = '20000000-0000-4000-8000-000000000002'
E = '30000000-0000-4000-8000-000000000001'
DART = r'''
import 'dart:convert';
import 'dart:io';
import 'package:flutter_test/flutter_test.dart';
import 'package:orange_notes_mobile/services/ai_gateway.dart';
import 'package:orange_notes_mobile/services/p2p_transport.dart';
void main() {
  test('real DesktopAiGateway Python mTLS relay', () async {
    final f=jsonDecode(File(Platform.environment['ORANGE_AI_FIXTURE']!).readAsStringSync()) as Map;
    final gateway=DesktopAiGateway(
      context:createTlsContext(caPem:utf8.encode(f['ca'] as String),certPem:utf8.encode(f['a']['cert'] as String),keyPem:utf8.encode(f['a']['key'] as String)),
      localActorId:'20000000-0000-4000-8000-000000000001',workspaceId:'10000000-0000-4000-8000-000000000001',
      peer:PeerIdentity('20000000-0000-4000-8000-000000000002','10000000-0000-4000-8000-000000000001',f['b']['pin'] as String),
      host:'127.0.0.1',port:int.parse(Platform.environment['ORANGE_AI_PORT']!),
    );
    final events=await gateway.ask('Что в плане?',contexts:[const AiContext(noteId:'30000000-0000-4000-8000-000000000001',title:'План 🐝',body:'Созвон в 14:00.')]).toList();
    expect(events.where((e)=>e.kind==AiEventKind.error),isEmpty);
    expect(events.where((e)=>e.kind==AiEventKind.done).length,1);
    expect(events.where((e)=>e.kind==AiEventKind.token).map((e)=>e.text).join(),'По заметке 🐝: Созвон в 14:00.');
  });
}
'''


async def smoke(flutter):
    import os
    fixtures = ROOT / 'orange_notes_mobile/test/fixtures/PUBLIC_TEST_TLS.json'
    values = json.loads(fixtures.read_text(encoding='utf-8'))
    work = ROOT / 'work/v020'
    work.mkdir(parents=True, exist_ok=True)
    calls = []
    async def backend(request):
        calls.append(request)
        assert request['actor_id'] == A and request['workspace_id'] == W
        assert request['contexts'][0]['note_id'] == E
        yield 'По заметке 🐝: '
        yield request['contexts'][0]['body']
    with tempfile.TemporaryDirectory(prefix='mobile-ai-interop-', dir=work) as folder:
        folder = Path(folder)
        for name, text in [('b.crt', values['b']['cert']), ('b.key', values['b']['key']), ('ca.crt', values['ca'])]:
            (folder / name).write_text(text, encoding='ascii')
        server = AIRelayServer(server_context(folder / 'b.crt', folder / 'b.key', folder / 'ca.crt'),
                               [PeerIdentity(A, W, values['a']['pin'])], backend)
        address = await server.start()
        # flutter_test is necessary: the real gateway also imports mobile SQLite.
        # Keep its temporary test inside the package for plugin/package resolution.
        script = ROOT / 'orange_notes_mobile/test/_generated_ai_interop_test.dart'
        if script.exists():
            raise RuntimeError('Temporary interop test already exists')
        script.write_text(DART, encoding='utf-8')
        process = None
        try:
            env = dict(os.environ, ORANGE_AI_FIXTURE=str(fixtures), ORANGE_AI_PORT=str(address[1]))
            command = [str(flutter), 'test', '--no-pub', str(script), '--reporter', 'expanded']
            if sys.platform == 'win32' and str(flutter).lower().endswith(('.bat', '.cmd')):
                command = ['cmd.exe', '/c', *command]
            process = await asyncio.create_subprocess_exec(*command, cwd=ROOT / 'orange_notes_mobile', env=env,
                       stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            out, err = await asyncio.wait_for(process.communicate(), 180)
            if process.returncode:
                raise RuntimeError(out.decode(errors='replace') + err.decode(errors='replace'))
            assert len(calls) == 1
            return {'status': 'PASS', 'transport': 'TLS 1.3 mTLS', 'client': 'actual Dart DesktopAiGateway',
                    'backend': 'deterministic fake; no live model', 'requests': len(calls),
                    'scope': 'host loopback, PUBLIC TEST certificates; not physical phone qualification'}
        finally:
            if process is not None and process.returncode is None:
                process.kill()
                await process.wait()
            script.unlink(missing_ok=True)
            await server.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--flutter', required=True, type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    result = asyncio.run(smoke(args.flutter.resolve()))
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + '\n', encoding='utf-8')
    print(text)


if __name__ == '__main__':
    main()
