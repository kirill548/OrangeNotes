"""Local-only Ollama API. No credentials, proxies, cloud fallback or dependencies."""
import ipaddress
from copy import deepcopy
import json
import math
import re
import socket
import time
from http.client import HTTPException
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, ProxyHandler, HTTPRedirectHandler


_ANSWER_SCHEMA = {
    'type':'object','additionalProperties':False,'required':['segments'],
    'properties':{'segments':{'type':'array','minItems':1,'maxItems':16,
        'items':{'type':'object','additionalProperties':False,
            'required':['kind','text','source_ids','quotes'],
            'properties':{
                'kind':{'type':'string','enum':['fact','inference','suggestion','question']},
                'text':{'type':'string'},
                'source_ids':{'type':'array','items':{'type':'integer'}},
                'quotes':{'type':'array','items':{'type':'object','additionalProperties':False,
                    'required':['source_id','quote'],
                    'properties':{'source_id':{'type':'integer'},'quote':{'type':'string'}}}}
            }}}}}


_DIALOGUE_SCHEMA = deepcopy(_ANSWER_SCHEMA)
_dialogue_properties = _DIALOGUE_SCHEMA['properties']['segments']['items']['properties']
_dialogue_properties['kind']['enum'] = ['suggestion', 'question']
_dialogue_properties['source_ids']['maxItems'] = 0
_dialogue_properties['quotes']['maxItems'] = 0


class LocalAIError(RuntimeError):
    pass


class LocalAICancelled(LocalAIError):
    pass


class _NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise LocalAIError('Локальный сервер попытался перенаправить запрос. Запрос остановлен.')


class OllamaClient:
    def __init__(self, base_url='http://127.0.0.1:11434', model='qwen3:4b', timeout=60):
        try:
            parts=urlsplit(base_url)
            if parts.scheme!='http' or parts.username is not None or parts.password is not None or parts.query or parts.fragment or parts.path not in ('','/'):
                raise ValueError()
            host=parts.hostname
            if host=='localhost':host='127.0.0.1'
            if not host or not ipaddress.ip_address(host).is_loopback:raise ValueError()
            port=parts.port or 11434
            if not 1<=port<=65535:raise ValueError()
        except ValueError as error:
            raise LocalAIError('Разрешён только локальный HTTP-сервер на 127.0.0.1 или ::1.') from error
        self.base_url='http://'+('['+host+']' if ':' in host else host)+':'+str(port)
        self.model=self._local_model(model)
        self.timeout=max(1,min(float(timeout),30))
        self.deadline=None
        self._opener=build_opener(ProxyHandler({}),_NoRedirects())

    @staticmethod
    def _local_model(model):
        if not isinstance(model,str) or not model.strip() or 'cloud' in model.casefold() or '/' in model or '\\' in model:
            raise LocalAIError('Выберите установленную локальную модель; облачные модели отключены.')
        return model

    @staticmethod
    def _check_cancel(cancel_event):
        if cancel_event is not None and cancel_event.is_set():
            raise LocalAICancelled('Запрос отменён.')

    def _request(self,path,data=None,timeout=None,cancel_event=None):
        self._check_cancel(cancel_event)
        remaining=self.deadline-time.monotonic() if self.deadline is not None else 30
        if remaining<=0:raise LocalAIError('Модель не успела ответить за 30 секунд. Показываю поиск по заметкам; попробуйте сократить вопрос.')
        effective_timeout=min(timeout or self.timeout,remaining)
        request_deadline=time.monotonic()+effective_timeout
        request=Request(self.base_url+path,data=json.dumps(data,ensure_ascii=False).encode('utf-8') if data is not None else None,headers={'Content-Type':'application/json'},method='POST' if data is not None else 'GET')
        try:
            with self._opener.open(request,timeout=effective_timeout) as response:
                limit=4*1024*1024
                # HTTPResponse.read(n) waits until n bytes/EOF; an endless trickle
                # can therefore reset the socket idle timeout indefinitely.
                # read1 performs at most one buffered socket read per iteration.
                if callable(getattr(type(response),'read1',None)):
                    chunks=[]
                    size=0
                    while True:
                        self._check_cancel(cancel_event)
                        budget=request_deadline-time.monotonic()
                        if budget<=0:
                            raise LocalAIError('Локальный помощник отвечает слишком долго. Показываю поиск по заметкам.')
                        connection=getattr(getattr(getattr(response,'fp',None),'raw',None),'_sock',None)
                        if isinstance(connection,socket.socket):
                            connection.settimeout(budget)
                        chunk=response.read1(min(65536,limit+1-size))
                        if not chunk:break
                        chunks.append(chunk)
                        size+=len(chunk)
                        if size>limit:raise LocalAIError('Ответ локальной модели слишком большой.')
                    raw=b''.join(chunks)
                else:
                    # Small synthetic responses used by contract tests.
                    raw=response.read(limit+1)
            self._check_cancel(cancel_event)
            if self.deadline is not None and time.monotonic()>=self.deadline:
                raise LocalAIError('Модель не успела ответить за 30 секунд. Показываю поиск по заметкам; попробуйте сократить вопрос.')
            if len(raw)>4*1024*1024:raise LocalAIError('Ответ локальной модели слишком большой.')
            result=json.loads(raw)
            if not isinstance(result,dict):raise ValueError()
            if result.get('error'):raise LocalAIError('Локальная модель не выполнила запрос: '+str(result['error'])[:250])
            return result
        except HTTPError as error:
            if error.code==404:raise LocalAIError('Локальная модель не установлена. Откройте настройку помощника и загрузите модель.') from error
            raise LocalAIError('Локальный сервер вернул ошибку '+str(error.code)+'.') from error
        except (URLError,socket.timeout,TimeoutError,ConnectionError,HTTPException) as error:
            self._check_cancel(cancel_event)
            raise LocalAIError('Локальный помощник недоступен или отвечает слишком долго. Проверьте запуск Ollama.') from error
        except (ValueError,UnicodeError) as error:
            raise LocalAIError('Локальный сервер вернул некорректный ответ.') from error

    def probe(self):
        try:
            result=self._request('/api/tags',timeout=3)
            models=[entry.get('name') for entry in result.get('models',[]) if isinstance(entry,dict) and isinstance(entry.get('name'),str)]
            return {'available':True,'models':models,'model_ready':self.model in models,'model':self.model,'error':None}
        except LocalAIError as error:
            return {'available':False,'models':[],'model_ready':False,'model':self.model,'error':str(error)}

    def _ensure_installed_local(self,model):
        # A local Ollama server can proxy cloud models; reject these explicitly.
        result=self._request('/api/show',{'model':model},timeout=5)
        if result.get('remote_host') or result.get('remote_model'):
            raise LocalAIError('Эта модель работает в облаке. Разрешены только локальные модели.')

    def chat(self,messages,cancel_event=None,json_mode=False,stream=False):
        # One budget includes the local-model preflight and the entire stream.
        previous_deadline=self.deadline
        self.deadline=min(previous_deadline or time.monotonic()+30,time.monotonic()+30)
        try:
            return self._chat(messages,cancel_event,json_mode,stream)
        finally:
            self.deadline=previous_deadline

    def _chat(self,messages,cancel_event=None,json_mode=False,stream=False):
        self._check_cancel(cancel_event)
        if not isinstance(messages,list) or not messages:raise LocalAIError('Добавьте сообщение для помощника.')
        for message in messages:
            if not isinstance(message,dict) or message.get('role') not in ('system','user','assistant') or not isinstance(message.get('content'),str):
                raise LocalAIError('Некорректная история диалога.')
        self._ensure_installed_local(self.model)
        payload={'model':self.model,'messages':messages,'stream':bool(stream),'think':False,'options':{'temperature':0.2,'num_ctx':8192,'num_predict':1024}}
        if json_mode:
            payload['format']=deepcopy(_DIALOGUE_SCHEMA if json_mode=='dialogue' else _ANSWER_SCHEMA)
            if json_mode=='dialogue' and re.search(r'\b(?:список|перечень|чек.?лист)\b',messages[-1]['content'].casefold()):
                # Require actual item segments rather than a syntactically valid
                # introduction-only answer. Evidence mode keeps its own schema.
                payload['format']['properties']['segments']['minItems']=3
        result=self._stream_request(payload,cancel_event) if stream else self._request('/api/chat',payload,cancel_event=cancel_event)
        if result.get('done_reason')=='length':
            raise LocalAIError('Ответ модели оборвался из-за ограничения длины. Попросите более короткий ответ.')
        message=result.get('message')
        content=message.get('content') if isinstance(message,dict) else None
        if not isinstance(content,str) or not content.strip():raise LocalAIError('Локальная модель не вернула текст ответа.')
        return content.strip()

    def chat_stream(self,messages,cancel_event=None,json_mode=False):
        return self.chat(messages,cancel_event=cancel_event,json_mode=json_mode,stream=True)

    def _stream_request(self,payload,cancel_event):
        from app.services.ollama_stream import receive_stream
        return receive_stream(self,payload,cancel_event)

    def embed(self,texts,model='qwen3-embedding:0.6b'):
        model=self._local_model(model)
        if not isinstance(texts,list) or not texts or any(not isinstance(text,str) for text in texts):
            raise LocalAIError('Передайте непустой список текстов для поиска.')
        self._ensure_installed_local(model)
        result=self._request('/api/embed',{'model':model,'input':texts,'truncate':False})
        vectors=result.get('embeddings')
        if not isinstance(vectors,list) or len(vectors)!=len(texts):raise LocalAIError('Модель поиска вернула неверное число результатов.')
        width=None
        for vector in vectors:
            if not isinstance(vector,list) or not vector or any(type(value) not in (int,float) or not math.isfinite(value) for value in vector):raise LocalAIError('Модель поиска вернула некорректный вектор.')
            if width is None:width=len(vector)
            elif len(vector)!=width:raise LocalAIError('Размеры векторов поиска не совпадают.')
        return vectors
