"""Build the optional offline Windows AI pack from verified local assets."""
import argparse,hashlib,json,zipfile
from pathlib import Path


def build(work, destination, chat_model='qwen3:4b'):
 work=Path(work).resolve();destination=Path(destination).resolve()
 files=[]
 runtime=work/'ollama'
 if not (runtime/'ollama.exe').is_file():raise FileNotFoundError('Portable Ollama отсутствует')
 for name in ('ollama.exe','LICENSE.txt'):
  path=runtime/name
  if not path.is_file():raise FileNotFoundError(path)
  files.append((path,'runtime/ollama/'+name))
 for path in sorted((runtime/'lib').rglob('*')):
  if path.is_file():files.append((path,'runtime/ollama/'+path.relative_to(runtime).as_posix()))
 models=work/'ai_models';required=set()
 manifests=list((models/'manifests').rglob('*'))
 manifests=[p for p in manifests if p.is_file()]
 if chat_model not in ('qwen3:1.7b','qwen3:4b'):raise ValueError('Unsupported chat model')
 selected={('qwen3',chat_model.split(':')[1]),('qwen3-embedding','0.6b')}
 manifests=[p for p in manifests if (p.parent.name,p.name) in selected]
 if len(manifests)<2:raise ValueError('Нужны манифесты chat и embedding')
 for path in manifests:
  value=json.loads(path.read_text(encoding='utf-8'))
  for layer in [value['config']]+value['layers']:
   digest=layer['digest'];blob=models/'blobs'/digest.replace(':','-')
   if not blob.is_file() or blob.stat().st_size!=layer['size']:raise ValueError('Повреждён blob '+digest)
   required.add(blob)
  files.append((path,'runtime/models/'+path.relative_to(models).as_posix()))
 for blob in sorted(required):
  with blob.open('rb') as stream:
   if hashlib.file_digest(stream,'sha256').hexdigest()!=blob.name.removeprefix('sha256-'):raise ValueError('SHA256 mismatch '+blob.name)
  files.append((blob,'runtime/models/blobs/'+blob.name))
 chat_license='Qwen3-4B-GGUF-LICENSE.txt' if chat_model=='qwen3:4b' else 'Qwen3-1.7B-GGUF-LICENSE.txt'
 for name in (chat_license,'Qwen3-Embedding-0.6B-GGUF-LICENSE.txt','models_origin.json'):
  path=work/'official_qwen'/name
  if not path.is_file():raise FileNotFoundError(path)
  files.append((path,'runtime/licenses/'+name))
 destination.parent.mkdir(parents=True,exist_ok=True)
 temporary=destination.with_suffix('.building.zip')
 try:
  with zipfile.ZipFile(temporary,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=1,allowZip64=True) as archive:
   for path,name in files:archive.write(path,name)
   archive.writestr('runtime/READ-ME.txt','Orange Notes: extract this archive beside OrangeNotes.exe. All processing stays local. Models: '+chat_model+' + Qwen3 Embedding 0.6B, Apache 2.0. Runtime: Ollama Windows x64, MIT. Optional.\n')
  with zipfile.ZipFile(temporary) as archive:
   bad=archive.testzip()
   if bad:raise ValueError('ZIP CRC failed '+bad)
  temporary.replace(destination)
 except Exception:
  temporary.unlink(missing_ok=True)
  raise
 print(json.dumps({'archive':str(destination),'bytes':destination.stat().st_size,'files':len(files),'blob_count':len(required)}))
 return destination

if __name__=='__main__':
 parser=argparse.ArgumentParser();parser.add_argument('--work',default=str(Path(__file__).resolve().parents[3]/'work'));parser.add_argument('--output',default=str(Path(__file__).resolve().parents[1]/'dist/OrangeNotes-AI-Windows-x64.zip'));parser.add_argument('--chat-model',default='qwen3:4b',choices=['qwen3:1.7b','qwen3:4b']);args=parser.parse_args();build(args.work,args.output,args.chat_model)
