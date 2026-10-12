"""Native live tools qualification; no tool execution or database writes."""
import json,time,urllib.request,argparse,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.services.assistant_prompt import SYSTEM_PROMPT_V2
from app.services.tool_schema import TOOL_DEFINITIONS,validate_ollama_tool_call

def run(endpoint,output,repeats=10):
    cases=[('Напомни завтра в 15:00 купить молоко','create_reminder'),('Найди мои заметки про отчет','search_notes'),('Привет, как дела?',None)]
    rows=[]
    for iteration in range(repeats):
        for query,expected in cases:
            row={'iteration':iteration+1,'query':query,'expected':expected};start=time.monotonic()
            try:
                payload={'model':'qwen3:4b','stream':False,'messages':[{'role':'system','content':SYSTEM_PROMPT_V2+'\nСегодня 2026-10-12. Часовой пояс Asia/Yekaterinburg. Используйте инструмент для напоминания или поиска; для приветствия отвечайте текстом без инструмента.'},{'role':'user','content':query}],'tools':TOOL_DEFINITIONS,'options':{'temperature':0,'num_ctx':2048,'num_predict':512}}
                request=urllib.request.Request(endpoint+'/api/chat',data=json.dumps(payload).encode(),headers={'Content-Type':'application/json'})
                with urllib.request.urlopen(request,timeout=30) as response:message=json.load(response)['message']
                calls=message.get('tool_calls',[]);text=message.get('content','');row['text']=text
                assert not any(word in text.casefold() for word in ('органайзац','рады помочь','календар','сторонними сервисами','сохранено','сохранил'))
                import re
                assert not re.search(r'\b(?:ты|тебе|твой|тебя)\b',text.casefold())
                if expected:
                    assert len(calls)==1
                    call=validate_ollama_tool_call(calls[0]);row['call']=call;assert call['name']==expected
                    args=call['arguments']
                    if expected=='create_reminder':assert args['date']=='2026-10-13' and args['time']=='15:00' and args['recurrence'] is None and args['timezone']=='Asia/Yekaterinburg' and 'молоко' in args['title'].casefold()
                    else:assert 'отчет' in args['query'].casefold().replace('ё','е')
                else:assert not calls and text.strip()
                row['passed']=True
            except Exception as error:row.update(passed=False,error=type(error).__name__+': '+str(error))
            row['latency_seconds']=time.monotonic()-start;rows.append(row)
    report={'model':'qwen3:4b','prompt':SYSTEM_PROMPT_V2,'scope':'native tools API, temperature=0, no DB writes; lexical checks do not prove exhaustive grammar quality','cases':rows,'passed':sum(r['passed'] for r in rows),'total':len(rows),'rounds_passed':sum(all(r['passed'] for r in rows if r['iteration']==i) for i in range(1,repeats+1))}
    Path(output).parent.mkdir(parents=True,exist_ok=True);Path(output).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({k:report[k] for k in ('passed','total','rounds_passed')}));return report
if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--endpoint',default='http://127.0.0.1:11591');parser.add_argument('--output',default='work/v014/tool_stress_v2.json');args=parser.parse_args();run(args.endpoint,args.output)
