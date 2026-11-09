#!/usr/bin/env python3
"""JSON-line bridge for the local Bun server. Credentials never enter argv."""
import contextlib
import hashlib
import json
import math
import os
from pathlib import Path
import signal
import sys
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import h2c_recordings as core

OUT = sys.stdout

def emit(value):
    OUT.write(json.dumps(value, allow_nan=False) + '\n')
    OUT.flush()

def public_settings(config):
    return dict(host=config.get('last_host',''), port=config.get('port',990),
                remoteDir=config.get('remote_dir','/ipcam'),
                downloadFolder=config.get('last_folder',str(Path.home()/'Downloads'/'H2C Recordings')),
                sessionGap=config.get('session_gap',20), timezone=config.get('timezone','America/New_York'),
                filenameUtcOffset=config.get('filename_utc_offset',8),
                hasAccessCode=bool(config.get('access_code')))

def save_settings(data):
    config=core.read_config()
    host=data.get('host','').strip()
    if not host or len(host)>253 or any(c.isspace() or c in '/\\\r\n' for c in host):
        raise ValueError('Enter a valid printer IP address or hostname.')
    port=data.get('port',990)
    if type(port) is not int or not 1<=port<=65535:raise ValueError('Port must be 1–65535.')
    gap=data.get('sessionGap',20)
    if type(gap) not in (int,float) or not math.isfinite(gap) or gap<=0:raise ValueError('Session gap must be positive.')
    offset=data.get('filenameUtcOffset',8)
    if type(offset) not in (int,float) or not math.isfinite(offset) or not -14<=offset<=14:raise ValueError('Invalid filename UTC offset.')
    zone=data.get('timezone','America/New_York')
    try:ZoneInfo(zone)
    except (ValueError,TypeError,ZoneInfoNotFoundError):raise ValueError('Enter a valid IANA timezone, such as America/New_York.')
    remote=data.get('remoteDir','/ipcam').strip()
    if not remote.startswith('/') or any(c in remote for c in '\r\n'):raise ValueError('Remote folder must be an absolute FTP path.')
    folder=data.get('downloadFolder','').strip()
    if not folder or not Path(folder).expanduser().is_absolute():raise ValueError('Download folder must be an absolute path.')
    code=data.get('accessCode','')
    if not isinstance(code,str):raise ValueError('Invalid access code.')
    code=code.strip()
    if host != config.get('last_host') and not code:
        raise ValueError('Enter the access code when changing printers.')
    if not code and not config.get('access_code'):raise ValueError('Enter the printer LAN access code.')
    if code:config['access_code']=code
    config.update(last_host=host,port=port,remote_dir=remote,last_folder=str(Path(folder).expanduser()),
                  session_gap=gap,timezone=zone,filename_utc_offset=offset)
    core.save_config(config)
    return public_settings(config)

def configure(config):
    core.DISPLAY_TIMEZONE=ZoneInfo(config.get('timezone','America/New_York'))
    core.RECORDING_TIMEZONE=core.timezone(core.timedelta(hours=config.get('filename_utc_offset',8)))

def connect(config):
    if not config.get('last_host') or not config.get('access_code'):
        raise ValueError('Add your printer address and LAN access code in Settings first.')
    ftp=core.ImplicitFTPS(timeout=25)
    try:
        ftp.connect(config['last_host'],config.get('port',990))
        ftp.login('bblp',config['access_code']);ftp.prot_p();ftp.set_pasv(True)
        return ftp
    except BaseException:
        ftp.close();raise

def descriptor(job):
    identity=[(c.path,c.size) for c in job.chunks]
    return dict(id=hashlib.sha256(json.dumps(identity).encode()).hexdigest()[:16],
                start=job.modified.isoformat() if job.modified else None,
                lastChunk=job.chunks[-1].modified.isoformat() if job.chunks[-1].modified else None,
                count=len(job.chunks),size=job.size,label=job.name,
                chunks=[dict(path=c.path,size=c.size,modified=c.modified.isoformat() if c.modified else None) for c in job.chunks])

def handle(request):
    action=request.get('action')
    if action=='settings':return public_settings(core.read_config())
    if action=='save-settings':return save_settings(request['data'])
    if action=='library':
        folder=Path(core.read_config().get('last_folder',str(Path.home()/'Downloads'/'H2C Recordings'))).expanduser()
        videos=[]
        if folder.is_dir():
            for file in folder.glob('*.mp4'):
                if file.is_symlink() or not file.is_file():continue
                stat=file.stat()
                videos.append(dict(id=hashlib.sha256(str(file).encode()).hexdigest()[:24],
                                   name=file.name,size=stat.st_size,modified=stat.st_mtime,path=str(file.resolve())))
        return sorted(videos,key=lambda video:video['modified'],reverse=True)
    config=core.read_config();configure(config)
    if action=='download':core.find_ffmpeg() # fail before downloading if installation is incomplete
    ftp=connect(config)
    try:
        base=config.get('remote_dir','/ipcam')
        if action in ('list','test'):
            files=core.remote_files(ftp,base)
            jobs=core.group_recordings(files,config.get('session_gap',20))
            if action=='test':return dict(ok=True,files=len(files),videos=sum(len(j.chunks) for j in jobs),jobs=len(jobs))
            return [descriptor(j) for j in jobs]
        if action=='download':
            # Recheck the full snapshot, preventing a changed recording from silently merging.
            wanted=request['job']
            current={f.path:f for f in core.remote_files(ftp,base)}
            chunks=[]
            for old in wanted['chunks']:
                file=current.get(old['path'])
                if file is None or file.size!=old['size']:
                    raise ValueError('A recording changed or is no longer on the printer. Refresh the list and retry after recording stops.')
                chunks.append(file)
            folder=Path(config.get('last_folder',str(Path.home()/'Downloads'/'H2C Recordings'))).expanduser()
            folder.mkdir(parents=True,exist_ok=True)
            size=sum(c.size or 0 for c in chunks)
            if core.shutil.disk_usage(folder).free < size*2+16*1024*1024:
                raise OSError('Not enough free space: allow approximately twice the job size for downloading and merging.')
            result=core.download_job(ftp,core.RecordingJob(chunks),base,folder,core.find_ffmpeg(),on_event=emit)
            return dict(result=result)
        raise ValueError('Unknown worker action')
    finally:
        try:ftp.quit()
        except Exception:ftp.close()

def cancelled(signum, frame):
    raise KeyboardInterrupt('Download cancelled. Completed chunks are retained for retry.')

if __name__=='__main__':
    signal.signal(signal.SIGTERM,cancelled)
    try:
        request=json.loads(sys.stdin.readline())
        with contextlib.redirect_stdout(sys.stderr):result=handle(request)
        emit(dict(type='result',data=result))
    except BaseException as exc:
        # A password is never included in diagnostics returned to the browser.
        message=str(exc) or type(exc).__name__
        secret=core.read_config().get('access_code')
        if secret:message=message.replace(secret,'[redacted]')
        emit(dict(type='error',message=message))
        sys.exit(1)
