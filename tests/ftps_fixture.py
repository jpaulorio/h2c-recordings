#!/usr/bin/env python3
"""Optional local TLS printer fixture. Uses only synthetic videos and test credentials."""
import json
import os
from pathlib import Path
import socket
import socketserver
import ssl
import subprocess
import sys
import tempfile
import time
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'python'))
import h2c_recordings as core

class Printer(socketserver.BaseRequestHandler):
    def handle(self):
        conn=CONTEXT.wrap_socket(self.request,server_side=True)
        control=conn.makefile('rb');listener=None
        def reply(text):conn.sendall((text+'\r\n').encode())
        reply('220 Synthetic H2C test fixture')
        try:
            while line:=control.readline():
                command,_,argument=line.decode().strip().partition(' ');command=command.upper()
                if command=='USER':reply('331 Password required')
                elif command=='PASS':reply('230 Logged in' if argument=='fixture-only' else '530 Invalid test password')
                elif command in ('PBSZ','PROT','TYPE','NOOP'):reply('200 OK')
                elif command=='CWD':reply('250 Directory changed')
                elif command=='PWD':reply('257 "/ipcam"')
                elif command=='MLSD':reply('500 Not supported')
                elif command=='SIZE':reply('213 '+str(len(FILES.get(Path(argument).name,b''))))
                elif command=='MDTM':reply('213 20261108120000')
                elif command=='PASV':
                    if listener:listener.close()
                    listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen(1)
                    port=listener.getsockname()[1];reply(f'227 Entering Passive Mode (127,0,0,1,{port//256},{port%256})')
                elif command in ('LIST','RETR'):
                    if listener is None:reply('425 Use PASV');continue
                    reply('150 Opening encrypted data connection')
                    raw,_=listener.accept();data=CONTEXT.wrap_socket(raw,server_side=True)
                    if command=='LIST':
                        payload=''.join(f'-rw-r--r-- 1 owner group {len(content)} Nov 08 2026 {name}\r\n' for name,content in FILES.items()).encode()
                        data.sendall(payload)
                    else:
                        payload=FILES[Path(argument).name]
                        for start in range(0,len(payload),2048):
                            data.sendall(payload[start:start+2048]);time.sleep(DELAY)
                    try:data.unwrap().close()
                    except (OSError,ssl.SSLError):data.close()
                    listener.close();listener=None;reply('226 Transfer complete')
                elif command=='QUIT':reply('221 Goodbye');break
                else:reply('502 Command unsupported')
        except (OSError,KeyError,ssl.SSLError):pass
        finally:
            if listener:listener.close()
            control.close();conn.close()

if __name__=='__main__':
    folder=Path(sys.argv[1] if len(sys.argv)>1 else tempfile.mkdtemp(prefix='h2c-fixture-')).resolve();folder.mkdir(parents=True,exist_ok=True)
    cert=folder/'cert.pem';key=folder/'key.pem'
    subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-keyout',str(key),'-out',str(cert),'-days','1','-subj','/CN=localhost'],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    key.chmod(0o600)
    CONTEXT=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);CONTEXT.load_cert_chain(cert,key)
    DELAY=float(os.environ.get('H2C_FIXTURE_DELAY','.1'))
    FILES={}
    for index,stamp in enumerate(['2026-11-08_20-00-00','2026-11-08_20-08-00','2026-11-08_22-00-00','2026-11-08_22-08-00']):
        file=folder/f'fixture-{index}.mp4'
        subprocess.run([core.find_ffmpeg(),'-nostdin','-loglevel','error','-y','-f','lavfi','-i',f'testsrc2=size=320x180:rate=15:duration=2','-c:v','libx264','-pix_fmt','yuv420p',str(file)],check=True)
        FILES[f'ipcam-record.{stamp}.{index}.mp4']=file.read_bytes()
    FILES['thumbnail.jpg']=b'not a video';FILES['index']=b'1234'
    port=int(os.environ.get('H2C_FIXTURE_PORT','1990'))
    config=folder/'settings.json';config.write_text(json.dumps(dict(last_host='127.0.0.1',port=port,access_code='fixture-only',last_folder=str(folder/'videos'),remote_dir='/ipcam',timezone='America/New_York',session_gap=20)));config.chmod(0o600)
    class Server(socketserver.ThreadingTCPServer):allow_reuse_address=True;daemon_threads=True
    with Server(('127.0.0.1',port),Printer) as server:
        print(json.dumps(dict(port=port,config=str(config),videos=str(folder/'videos'))),flush=True)
        server.serve_forever()
