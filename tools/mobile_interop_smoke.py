"""Real Python/Dart mutual-TLS smoke using PUBLIC TEST identities only.

Usage: python tools/mobile_interop_smoke.py --dart /absolute/path/to/dart
Requires flutter pub get first. No microphone, model, user database or LAN access.
"""
from __future__ import annotations
import argparse
import asyncio
import json
from pathlib import Path
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from app.services.p2p_sync import Replica
from app.services.p2p_transport import TLS13Server,TLS13Client,PeerIdentity,server_context,client_context

W='10000000-0000-4000-8000-000000000001'
A='20000000-0000-4000-8000-000000000001'
B='20000000-0000-4000-8000-000000000002'
E='30000000-0000-4000-8000-000000000001'
DART=r'''
import 'dart:convert';
import 'dart:io';
import 'package:orange_notes_mobile/services/p2p_transport.dart';
import 'package:orange_notes_mobile/services/crdt_engine.dart';
Future<void> main(List<String> args) async {
  final f=jsonDecode(File(args[1]).readAsStringSync()) as Map;
  const w='10000000-0000-4000-8000-000000000001',a='20000000-0000-4000-8000-000000000001',b='20000000-0000-4000-8000-000000000002',e='30000000-0000-4000-8000-000000000001';
  SecurityContext context(String who)=>createTlsContext(caPem:utf8.encode(f['ca'] as String),certPem:utf8.encode(f[who]['cert'] as String),keyPem:utf8.encode(f[who]['key'] as String));
  PeerIdentity peer(String who,String actor)=>PeerIdentity(actor,w,f[who]['pin'] as String);
  if(args[0]=='client') {
    final wire=Replica(w,a).makeDelta(e,{'title':'Dart → Python 🐝','body':'<p>План [[Отчёт|на завтра]]</p>','lifecycle':'active'});
    final client=Tls13Client(context('a'),a,w,peer('b',b));
    final first=await client.sendDelta('127.0.0.1',int.parse(args[2]),wire);
    final second=await client.sendDelta('127.0.0.1',int.parse(args[2]),wire);
    if(canonicalJson(first)!=canonicalJson(second))throw StateError('ACK mismatch');
    stdout.writeln(jsonEncode({'status':'pass','ack':first}));
  } else {
    final replica=Replica(w,b);var commits=0;
    final server=Tls13Server(context('b'),[peer('a',a)],(actor,workspace,wire)async{if(replica.applyDelta(wire))commits++;},localActorId:b);
    try {
      stdout.writeln(jsonEncode({'port':await server.start()}));await stdout.flush();
      await stdin.transform(utf8.decoder).transform(const LineSplitter()).first;
      stdout.writeln(jsonEncode({'commits':commits,'state':replica.get(e)}));await stdout.flush();
    }finally{await server.close();}
  }
}
'''

async def smoke(dart):
    fixtures=ROOT/'orange_notes_mobile/test/fixtures/PUBLIC_TEST_TLS.json'
    f=json.loads(fixtures.read_text(encoding='utf-8'))
    packages=ROOT/'orange_notes_mobile/.dart_tool/package_config.json'
    if not packages.is_file():raise RuntimeError('Run flutter pub get first')
    results=[]
    work=ROOT/'work/v020';work.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='mobile-interop-',dir=work) as temp:
        temp=Path(temp);script=temp/'smoke.dart';script.write_text(DART,encoding='utf-8')
        for who in ['a','b']:
            (temp/f'{who}.crt').write_text(f[who]['cert'],encoding='ascii')
            (temp/f'{who}.key').write_text(f[who]['key'],encoding='ascii')
        (temp/'ca.crt').write_text(f['ca'],encoding='ascii')
        command=[str(dart),'--packages='+str(packages),str(script)]
        replica=Replica(W,B);commits=0
        async def receive(actor,workspace,wire):
            nonlocal commits
            if replica.apply_delta(wire):commits+=1
        server=TLS13Server(server_context(temp/'b.crt',temp/'b.key',temp/'ca.crt'),[PeerIdentity(A,W,f['a']['pin'])],receive,local_actor_id=B)
        await server.start()
        process=None
        try:
            process=await asyncio.create_subprocess_exec(*command,'client',str(fixtures),str(server.address[1]),stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
            out,err=await asyncio.wait_for(process.communicate(),30)
            if process.returncode:raise RuntimeError(err.decode(errors='replace'))
            value=json.loads(out)
            assert value['status']=='pass' and commits==1
            assert replica.get(E)=={'title':'Dart → Python 🐝','body':'<p>План [[Отчёт|на завтра]]</p>','lifecycle':'active'}
            results.append({'direction':'Dart client → Python server','status':'PASS','deliveries':2,'commits':commits,'transport':'TLS 1.3 mTLS'})
        finally:
            if process and process.returncode is None:process.kill();await process.wait()
            await server.close()
        process=await asyncio.create_subprocess_exec(*command,'server',str(fixtures),stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
        try:
            line=await asyncio.wait_for(process.stdout.readline(),30)
            if not line:raise RuntimeError((await process.stderr.read()).decode(errors='replace'))
            port=json.loads(line)['port']
            client=TLS13Client(client_context(temp/'a.crt',temp/'a.key',temp/'ca.crt'),A,W,PeerIdentity(B,W,f['b']['pin']))
            wire=Replica(W,A).make_delta(E,{'title':'Python → Dart 🐝','body':'<p>Заметка [[План]]</p>','lifecycle':'active'})
            first=await client.send_delta('127.0.0.1',port,wire)
            second=await client.send_delta('127.0.0.1',port,wire)
            assert first==second
            out,err=await asyncio.wait_for(process.communicate(b'done\n'),30)
            if process.returncode:raise RuntimeError(err.decode(errors='replace'))
            value=json.loads(out)
            assert value['commits']==1 and value['state']=={'title':'Python → Dart 🐝','body':'<p>Заметка [[План]]</p>','lifecycle':'active'}
            results.append({'direction':'Python client → Dart server','status':'PASS','deliveries':2,'commits':value['commits'],'transport':'TLS 1.3 mTLS'})
        finally:
            if process.returncode is None:process.kill();await process.wait()
    return results

def main():
    if hasattr(sys.stdout, 'reconfigure'):sys.stdout.reconfigure(encoding='utf-8')
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dart',type=Path,required=True)
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    result=asyncio.run(smoke(args.dart.resolve()))
    text=json.dumps({'scope':'host loopback, PUBLIC TEST certificates, in-memory CRDT; no physical devices or SQLite exchange','results':result},ensure_ascii=False,indent=2)
    if args.output:args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(text+'\n',encoding='utf-8')
    print(text)
if __name__=='__main__':main()
