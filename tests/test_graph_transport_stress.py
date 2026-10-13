"""Real loopback transport: cancellation, wall deadlines and resource cleanup."""
import socket
import json
from unittest.mock import patch
import threading
import time
from contextlib import contextmanager
import pytest
from app.services.local_ai import OllamaClient, LocalAIError, LocalAICancelled


@contextmanager
def server(mode):
    listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen(1)
    listener.settimeout(.1)
    entered=threading.Event();stop=threading.Event()
    def serve():
        connection=None
        try:
            while not stop.is_set():
                try:connection,_=listener.accept();break
                except socket.timeout:continue
            if connection is None:return
            connection.settimeout(.1)
            request=connection.recv(65536)
            while b'\r\n\r\n' not in request:
                chunk=connection.recv(65536)
                if not chunk:return
                request+=chunk
            headers,body=request.split(b'\r\n\r\n',1)
            length=next((int(line.split(b':',1)[1]) for line in headers.split(b'\r\n')
                         if line.lower().startswith(b'content-length:')),0)
            while len(body)<length:
                chunk=connection.recv(65536)
                if not chunk:return
                body+=chunk
            if mode=='success':
                connection.sendall(b'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 2\r\n\r\n{}')
                entered.set();return
            if mode!='headers':
                connection.sendall(b'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 1000000\r\n\r\n')
            entered.set()
            while not stop.wait(.03):
                if mode=='trickle':connection.sendall(b' ')
        except OSError:pass
        finally:
            if connection is not None:connection.close()
    worker=threading.Thread(target=serve,name='GraphStressHTTPServer');worker.start()
    try:yield listener.getsockname()[1],entered
    finally:
        stop.set();listener.close();worker.join(2)
        assert not worker.is_alive()


@pytest.mark.parametrize('mode',['headers','body','trickle'])
def test_cancel_interrupts_blocking_native_http_without_worker_leak(mode):
    baseline={t.ident for t in threading.enumerate() if t.name=='OllamaRequestWatchdog'}
    with server(mode) as (port,entered):
        event=threading.Event()
        def cancel():
            assert entered.wait(2)
            time.sleep(.08);event.set()
        interrupter=threading.Thread(target=cancel);interrupter.start()
        client=OllamaClient(f'http://127.0.0.1:{port}',timeout=30)
        started=time.monotonic()
        try:
            with pytest.raises(LocalAICancelled):client._request('/api/chat',{},cancel_event=event)
        finally:interrupter.join(2)
        assert time.monotonic()-started<1.5
    assert {t.ident for t in threading.enumerate() if t.name=='OllamaRequestWatchdog'}==baseline
    print('TRANSPORT_STRESS_METRICS='+json.dumps({'case':'cancel','mode':mode,'seconds':round(time.monotonic()-started,4),'worker_threads_created':0}))


@pytest.mark.parametrize('mode',['headers','body','trickle'])
def test_shared_wall_deadline_beats_socket_idle_timeout(mode):
    with server(mode) as (port,entered):
        client=OllamaClient(f'http://127.0.0.1:{port}',timeout=30)
        started=time.monotonic();client.deadline=started+.22
        with pytest.raises(LocalAIError):client._request('/api/chat',{})
        elapsed=time.monotonic()-started
        assert .15<=elapsed<1.5
        print('TRANSPORT_STRESS_METRICS='+json.dumps({'case':'deadline','mode':mode,'budget_seconds':.22,'seconds':round(elapsed,4)}))


def test_repeated_cancel_and_success_leave_no_watchdog_threads():
    from app.services import local_ai
    readers=[];original=local_ai._PollingSocketReader
    def capture(*args,**kwargs):
        value=original(*args,**kwargs);readers.append(value);return value
    for _ in range(20):
        with server('body') as (port,entered):
            event=threading.Event()
            def cancel_after_headers():
                assert entered.wait(2)
                time.sleep(.04);event.set()
            timer=threading.Thread(target=cancel_after_headers);timer.start()
            try:
                with pytest.raises(LocalAICancelled):
                    with patch.object(local_ai,'_PollingSocketReader',side_effect=capture):
                        OllamaClient(f'http://127.0.0.1:{port}')._request('/api/chat',{},cancel_event=event)
            finally:timer.join()
    assert not [t for t in threading.enumerate() if t.name=='OllamaRequestWatchdog']

    assert len(readers)==20 and all(reader.closed and reader.sock.fileno()==-1 for reader in readers)
    print('TRANSPORT_STRESS_METRICS='+json.dumps({'case':'repeat_cancel','iterations':20,'socket_readers_closed':len(readers),'worker_threads_created':0}))


def real_deadline_experiment(seconds=30):
    """Manual wall-clock experiment; intentionally outside recurring unit tests."""
    with server('trickle') as (port,entered):
        client=OllamaClient(f'http://127.0.0.1:{port}',timeout=30)
        start=time.monotonic();client.deadline=start+seconds
        try:client._request('/api/chat',{})
        except LocalAIError as error:kind=type(error).__name__
        else:raise AssertionError('Endless trickle escaped wall deadline')
        elapsed=time.monotonic()-start
        assert seconds-.1<=elapsed<seconds+2
    result={'case':'real_wall_deadline','mode':'trickle','budget_seconds':seconds,
            'elapsed_seconds':round(elapsed,4),'error':kind,'worker_threads_created':0}
    print('TRANSPORT_STRESS_METRICS='+json.dumps(result))
    return result


if __name__=='__main__':
    real_deadline_experiment()


@pytest.mark.parametrize('raw',[b'{"message":'+b'['*1500+b'0'+b']'*1500+b'}',
    b'{"message":'+b'"'+b'x'*(4*1024*1024)+b'"}'],ids=['deep-json','oversize-json'])
def test_hostile_json_depth_and_payload_size_raise_domain_error(raw):
    from unittest.mock import MagicMock
    client=OllamaClient()
    response=MagicMock();response.read.return_value=raw
    client._opener=MagicMock();client._opener.open.return_value.__enter__.return_value=response
    with pytest.raises(LocalAIError):client._request('/api/chat',{})


def test_successful_native_transport_closes_readers():
    from app.services import local_ai
    readers=[];original=local_ai._PollingSocketReader
    def capture(*args,**kwargs):
        value=original(*args,**kwargs);readers.append(value);return value
    for _ in range(20):
        with server('success') as (port,entered),patch.object(local_ai,'_PollingSocketReader',side_effect=capture):
            assert OllamaClient(f'http://127.0.0.1:{port}')._request('/api/chat',{})=={}
    assert len(readers)==20 and all(reader.closed and reader.sock.fileno()==-1 for reader in readers)
    print('TRANSPORT_STRESS_METRICS='+json.dumps({'case':'repeat_success','iterations':20,'socket_readers_closed':20,'worker_threads_created':0}))


def test_json_depth_counter_ignores_escaped_string_punctuation():
    from app.services.local_ai import _check_json_depth
    _check_json_depth(json.dumps({'text':'"['*1000+'\\'}).encode('utf-8'))


@pytest.mark.parametrize('mode',['headers','body','trickle'])
def test_headless_stream_cancellation_has_same_transport_contract(mode):
    from app.services.ollama_stream import receive_stream
    with server(mode) as (port,entered):
        client=OllamaClient(f'http://127.0.0.1:{port}')
        event=threading.Event()
        def cancel():
            assert entered.wait(2)
            time.sleep(.05);event.set()
        worker=threading.Thread(target=cancel);worker.start()
        start=time.monotonic()
        try:
            with patch('PySide6.QtCore.QCoreApplication.instance',return_value=None),pytest.raises(LocalAICancelled):
                receive_stream(client,{'model':'qwen3:4b'},event)
        finally:worker.join(2)
        assert time.monotonic()-start<1.5


def test_probe_and_preflight_share_request_cancellation():
    client=OllamaClient();client.cancel_event=threading.Event();client.cancel_event.set()
    with pytest.raises(LocalAICancelled):client.probe()
    with pytest.raises(LocalAICancelled):client._ensure_installed_local(client.model)



def test_deep_stream_frame_aborts_private_model_buffer():
    from app.services.ollama_stream import NDJSONAnswer
    parser=NDJSONAnswer()
    with pytest.raises(LocalAIError):parser.feed(b'{"message":'+b'['*1500+b'0'+b']'*1500+b'}\n')
    assert parser.aborted and not parser.parts and not parser.pending


def test_deep_final_answer_cannot_escape_grounding_error_handler():
    from app.services.companion import CompanionEngine
    with pytest.raises(LocalAIError):
        CompanionEngine._validate('{"segments":'+'['*1500+'0'+']'*1500+'}',[])



@pytest.mark.parametrize('failure',['headers','send','connect','success'])
def test_retained_exception_tracebacks_do_not_keep_native_sockets_open(failure):
    from app.services import local_ai
    originals=[];connections=[];errors=[]
    original_connect=local_ai.HTTPConnection.connect
    original_handler=local_ai._InterruptibleHTTPHandler
    def connect(connection):
        original_connect(connection)
        originals.append(connection.sock)
    def handler(*args,**kwargs):
        value=original_handler(*args,**kwargs);connections.append(value);return value
    def broken_send(connection,data):
        connection.connect()
        raise LocalAIError('Deliberate send failure')
    if failure=='connect':
        listener=socket.socket();listener.bind(('127.0.0.1',0));port=listener.getsockname()[1];listener.close()
        client=OllamaClient(f'http://127.0.0.1:{port}')
        with patch.object(local_ai,'_InterruptibleHTTPHandler',side_effect=handler):
            try:client._request('/api/chat',{})
            except LocalAIError as error:errors.append(error)
        assert errors
    else:
        with server('success' if failure=='success' else 'headers') as (port,entered):
            client=OllamaClient(f'http://127.0.0.1:{port}');client.deadline=time.monotonic()+.15
            with patch.object(local_ai,'_InterruptibleHTTPHandler',side_effect=handler),patch.object(local_ai.HTTPConnection,'connect',new=connect):
                if failure=='send':
                    with patch.object(local_ai.HTTPConnection,'send',new=broken_send):
                        try:client._request('/api/chat',{})
                        except LocalAIError as error:errors.append(error)
                else:
                    try:client._request('/api/chat',{})
                    except LocalAIError as error:errors.append(error)
            if failure!='success':assert errors
            else:assert not errors
    # Keep exception tracebacks alive intentionally; cleanup must not rely on GC.
    assert all(sock.fileno()==-1 for sock in originals)
    assert all(connection.sock is None for value in connections for connection in value.connections)
    assert all(reader.closed and reader.sock.fileno()==-1 for value in connections for reader in value.readers)
