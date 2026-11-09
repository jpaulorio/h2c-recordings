"""Offline regression checks. Run: python3 validate_h2c.py [program_path]."""
import contextlib
import errno
import importlib.util
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch, Mock

source = str(Path(__file__).resolve().parents[1] / 'python' / 'h2c_recordings.py')
spec = importlib.util.spec_from_file_location('h2c', source)
m = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = m
spec.loader.exec_module(m)

class Checks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.out = self.root / 'downloads'
        self.out.mkdir()
        self.ftp = Mock()
        self.ftp.retrbinary.side_effect = lambda cmd, cb, **kw: cb(b'abc')
        self.entry = m.RemoteFile('/ipcam/day/video.mp4', 3, None)
        self.quiet = contextlib.redirect_stdout(io.StringIO())
        self.quiet.__enter__()
        self.addCleanup(self.quiet.__exit__, None, None, None)
    def download(self):
        return m.download_file(self.ftp, self.entry, '/ipcam', self.out)
    def test_selection(self):
        self.assertEqual(m.parse_selection('1,3-5,3', 5), [0,2,3,4])
        for raw in ('', '0', '6', '4-2', 'a', '1,'):
            with self.assertRaises(ValueError): m.parse_selection(raw,5)
    def test_dates(self):
        self.assertEqual(m.timestamp_in_name('2026-11-08_12-30-45.mp4').isoformat(), '2026-11-08T12:30:45+08:00')
        self.assertIsNone(m.timestamp_in_name('2026-99-08_12-30-45.mp4'))
        self.assertEqual(m.parse_mlsd_modify('20261108123045.123').year, 2026)
        self.assertIsNone(m.parse_mlsd_modify(None))
    def test_list_parser(self):
        row = m.parse_list_line('-rw-r--r-- 1 owner group 3 Nov 08 2026 video name.mp4')
        self.assertEqual(row[:3], ('video name.mp4',False,3))
        self.assertIsNone(m.parse_list_line('lrwxrwxrwx 1 o g 3 Nov 08 2026 link -> x'))
    def test_mlsd(self):
        self.ftp.mlsd.return_value = iter([('video.mp4',{'type':'file','size':'3','modify':'20261108123045'}),('..',{'type':'dir'})])
        self.assertEqual(m.list_directory(self.ftp,'/ipcam')[0][:3],('video.mp4',False,3))
    def test_list_fallback(self):
        self.ftp.mlsd.side_effect = m.ftplib.error_perm('500 unsupported')
        self.ftp.retrlines.side_effect = lambda cmd,cb: cb('-rw-r--r-- 1 o g 3 Nov 08 2026 video.mp4')
        self.assertEqual(len(m.list_directory(self.ftp,'/ipcam')),1)
    def test_recursive_sort(self):
        rows = {'/ipcam':[('day',True,None,None),('old.mp4',False,3,None)], '/ipcam/day':[('2026-11-08_12-30-45.mp4',False,3,None)]}
        with patch.object(m,'list_directory',side_effect=lambda ftp,d:rows[d]):
            files=m.remote_files(self.ftp,'/ipcam')
        self.assertEqual(files[0].name,'2026-11-08_12-30-45.mp4')
        self.assertEqual(len(files),2)
    def test_path_traversal(self):
        with self.assertRaises(ValueError): m.safe_relative_path('/elsewhere/file','/ipcam')
    def test_download(self):
        self.assertEqual(self.download(),'downloaded')
        self.assertEqual((self.out/'day/video.mp4').read_bytes(),b'abc')
        self.assertFalse(list(self.out.rglob('.h2c-download-*')))
    def test_different_size_collision(self):
        (self.out/'day').mkdir()
        (self.out/'day/video.mp4').write_bytes(b'x')
        self.assertEqual(self.download(),'downloaded')
        self.assertEqual((self.out/'day/video (2).mp4').read_bytes(),b'abc')
    def test_incomplete_cleanup(self):
        self.entry.size=4
        with self.assertRaises(OSError): self.download()
        self.assertEqual(list((self.out/'day').iterdir()),[])
    def test_config(self):
        with patch.object(m,'APP_DIR',self.root/'config'), patch.object(m,'CONFIG_FILE',self.root/'config/settings.json'):
            m.save_config({'last_host':'example'})
            self.assertEqual(m.read_config(),{'last_host':'example'})
            self.assertEqual(m.CONFIG_FILE.stat().st_mode & 0o777,0o600)
    def test_connection_mode_flow(self):
        with patch.object(sys,'argv',['h2c','--host','example','--test-connection']), patch.object(m,'read_config',return_value={}), patch.object(m,'save_config') as save, patch.object(m.getpass,'getpass',return_value='secret'), patch.object(m,'ImplicitFTPS',return_value=self.ftp), patch.object(m,'remote_files',return_value=[]):
            self.assertEqual(m.main(),0)
            self.ftp.prot_p.assert_called_once()
            self.ftp.retrbinary.assert_not_called()
            save.assert_called_once_with({'last_host':'example'})

    def test_same_size_wrong_content_is_preserved(self):
        (self.out/'day').mkdir()
        dest=self.out/'day/video.mp4';dest.write_bytes(b'BAD')
        self.assertEqual(self.download(),'downloaded')
        self.assertEqual(dest.read_bytes(),b'BAD')
        self.assertEqual((dest.parent/'video (2).mp4').read_bytes(),b'abc')
    def test_identical_content_is_skipped(self):
        self.download()
        self.assertEqual(self.download(),'skipped')
        self.assertFalse((self.out/'day/video (2).mp4').exists())
    def test_symlink_parent_is_rejected(self):
        outside=self.root/'outside';outside.mkdir()
        (self.out/'day').symlink_to(outside,target_is_directory=True)
        with self.assertRaises(ValueError):self.download()
        self.assertEqual(list(outside.iterdir()),[])
    def test_dangling_symlink_collision(self):
        (self.out/'day').mkdir()
        dest=self.out/'day/video.mp4';dest.symlink_to(self.root/'missing')
        self.assertEqual(self.download(),'downloaded')
        self.assertTrue(dest.is_symlink())
        self.assertEqual((dest.parent/'video (2).mp4').read_bytes(),b'abc')
    def test_no_hardlink_support_fallback(self):
        with patch.object(m.os,'link',side_effect=OSError(errno.ENOTSUP,'unsupported')):
            self.assertEqual(self.download(),'downloaded')
        self.assertEqual((self.out/'day/video.mp4').read_bytes(),b'abc')
        self.assertFalse(list(self.out.rglob('.h2c-download-*')))
    def test_huge_selection_rejected(self):
        with self.assertRaises(ValueError):m.parse_selection('1-999999999999',2)
    def test_transfer_failure_cleanup(self):
        def fail(cmd,cb,**kw):
            cb(b'a')
            raise OSError('connection lost')
        self.ftp.retrbinary.side_effect=fail
        with self.assertRaises(OSError):self.download()
        self.assertEqual(list((self.out/'day').iterdir()),[])
    def test_destination_created_during_publish(self):
        real_link=m.os.link
        def collide(src,dest):
            if not Path(dest).exists():
                Path(dest).write_bytes(b'existing')
                raise FileExistsError()
            real_link(src,dest)
        # Only the first attempted publication collides.
        calls=[0]
        def once(src,dest):
            calls[0]+=1
            if calls[0]==1:
                Path(dest).write_bytes(b'existing')
                raise FileExistsError()
            real_link(src,dest)
        with patch.object(m.os,'link',side_effect=once):self.download()
        self.assertEqual((self.out/'day/video.mp4').read_bytes(),b'existing')
        self.assertEqual((self.out/'day/video (2).mp4').read_bytes(),b'abc')

    def test_fallback_copy_failure_removes_partial_file(self):
        with patch.object(m.os,'link',side_effect=OSError(errno.ENOTSUP,'unsupported')), patch.object(m.shutil,'copyfileobj',side_effect=OSError('disk full')):
            with self.assertRaises(OSError):self.download()
        self.assertEqual(list((self.out/'day').iterdir()),[])
    def test_unexpected_link_error_is_not_hidden(self):
        with patch.object(m.os,'link',side_effect=OSError(errno.EIO,'disk error')):
            with self.assertRaises(OSError):self.download()
        self.assertEqual(list((self.out/'day').iterdir()),[])

    def test_saved_credentials_no_prompts(self):
        config={'last_host':'192.0.2.10','access_code':'test-only'}
        with patch.object(sys,'argv',['h2c','--test-connection']), patch.object(m,'read_config',return_value=config), patch.object(m,'save_config'), patch.object(m.getpass,'getpass',side_effect=AssertionError('unexpected password prompt')), patch('builtins.input',side_effect=AssertionError('unexpected input prompt')), patch.object(m,'ImplicitFTPS',return_value=self.ftp), patch.object(m,'remote_files',return_value=[]):
            self.assertEqual(m.main(),0)
            self.ftp.login.assert_called_once_with(user='bblp',passwd='test-only')
    def test_changed_host_does_not_reuse_saved_code(self):
        config={'last_host':'old-printer','access_code':'old-secret'}
        with patch.object(sys,'argv',['h2c','--host','new-printer','--test-connection']), patch.object(m,'read_config',return_value=config), patch.object(m,'save_config'), patch.object(m.getpass,'getpass',return_value='new-secret') as prompt, patch.object(m,'ImplicitFTPS',return_value=self.ftp), patch.object(m,'remote_files',return_value=[]):
            self.assertEqual(m.main(),0)
            prompt.assert_called_once()
            self.ftp.login.assert_called_once_with(user='bblp',passwd='new-secret')

    def select_keys(self, keys, size=(12,100)):
        import curses
        screen=Mock()
        screen.getmaxyx.return_value=size
        screen.getch.side_effect=keys
        files=[m.RemoteFile(f'/ipcam/{i}.mp4',3,None) for i in range(30)]
        with patch.object(curses,'curs_set'):
            result=m.keyboard_file_selector(screen,files,curses)
        return result,files,screen
    def test_keyboard_toggle_and_deselect(self):
        import curses as c
        result,files,_=self.select_keys([32,c.KEY_DOWN,32,c.KEY_UP,32,10])
        self.assertEqual(result,[files[1]])
    def test_keyboard_selection_survives_pages(self):
        import curses as c
        result,files,_=self.select_keys([32,c.KEY_RIGHT,32,c.KEY_LEFT,c.KEY_DOWN,32,10])
        self.assertEqual(result,[files[0],files[1],files[7]])
    def test_keyboard_empty_enter_then_select(self):
        result,files,screen=self.select_keys([10,32,10])
        self.assertEqual(result,[files[0]])
        self.assertTrue(any('Select at least' in str(call) for call in screen.addnstr.call_args_list))
    def test_keyboard_all_clear_and_end(self):
        import curses as c
        result,files,_=self.select_keys([ord('a'),ord('c'),c.KEY_END,32,10])
        self.assertEqual(result,[files[-1]])
    def test_keyboard_cancel(self):
        self.assertIsNone(self.select_keys([32,ord('q')])[0])
    def test_keyboard_small_terminal(self):
        self.assertIsNone(self.select_keys([ord('q')],size=(3,20))[0])

    def test_eastern_winter_and_summer(self):
        self.assertEqual(m.display_timestamp(m.parse_mlsd_modify('20261109000916')),
                         '2026-11-08 19:09:16 EST')
        self.assertEqual(m.display_timestamp(m.parse_mlsd_modify('20260709120000')),
                         '2026-07-09 08:00:00 EDT')
    def test_live_filename_matches_utc_metadata(self):
        name=m.timestamp_in_name('ipcam-record.2026-11-09_08-09-16.56.jpg')
        metadata=m.parse_mlsd_modify('20261109000916')
        self.assertEqual(name,metadata)
        self.assertEqual(m.display_timestamp(name),'2026-11-08 19:09:16 EST')
    def test_dst_fall_back(self):
        self.assertEqual(m.display_timestamp(m.parse_mlsd_modify('20261101053000')),
                         '2026-11-01 01:30:00 EDT')
        self.assertEqual(m.display_timestamp(m.parse_mlsd_modify('20261101063000')),
                         '2026-11-01 01:30:00 EST')
    def test_list_date_is_utc(self):
        value=m.parse_list_date('Nov','8','2026')
        self.assertEqual(value.utcoffset(),m.timedelta(0))

class JobChecks(unittest.TestCase):
    def file(self,name,stamp):
        return m.RemoteFile('/ipcam/'+name,3,m.parse_mlsd_modify(stamp) if stamp else None)
    def test_video_only_gap_and_order(self):
        a=self.file('a.mp4','20261108120000');b=self.file('b.mp4','20261108121000')
        c=self.file('c.mp4','20261108130000');jpg=self.file('thumb.jpg','20261108120100')
        jobs=m.group_recordings([c,b,jpg,a])
        self.assertEqual([j.chunks for j in jobs],[[c],[a,b]])
        self.assertEqual(jobs[1].size,6)
    def test_unknown_dates_standalone(self):
        jobs=m.group_recordings([self.file('a.mp4',None),self.file('b.mp4',None)])
        self.assertEqual(len(jobs),2)
    def test_gap_adjustment_and_midnight(self):
        files=[self.file('a.mp4','20261108235500'),self.file('b.mp4','20261109000500')]
        self.assertEqual(len(m.group_recordings(files,20)),1)
        self.assertEqual(len(m.group_recordings(files,5)),2)
    def test_gap_exact_boundary(self):
        files=[self.file('a.mp4','20261108120000'),self.file('b.mp4','20261108122000')]
        self.assertEqual(len(m.group_recordings(files,20)),1)

    def test_main_all_downloads_jobs_only(self):
        video=self.file('a.mp4','20261108120000');jpg=self.file('a.jpg','20261108120000')
        with tempfile.TemporaryDirectory() as local:
            with patch.object(sys,'argv',['h2c','--all']), patch.object(m,'read_config',return_value={'last_host':'printer','access_code':'test'}), patch.object(m,'save_config'), patch.object(m,'ImplicitFTPS',return_value=Mock()), patch.object(m,'remote_files',return_value=[jpg,video]), patch.object(m,'find_ffmpeg',return_value='ffmpeg'), patch.object(m,'choose_local_folder',return_value=Path(local)), patch('builtins.input',return_value='y'), patch.object(m,'download_job',return_value='downloaded') as download, contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(m.main(),0)
                self.assertEqual(download.call_args.args[1].chunks,[video])

class MergeIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import subprocess
        cls.ffmpeg=m.find_ffmpeg()
        cls.temp=tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        cls.media=[]
        for i,color in enumerate(('red','blue')):
            target=Path(cls.temp.name)/f'{i}.mp4'
            subprocess.run([cls.ffmpeg,'-nostdin','-loglevel','error','-f','lavfi','-i',
                            f'color=c={color}:s=64x64:r=10:d=1','-c:v','libx264','-pix_fmt','yuv420p',str(target)],check=True)
            cls.media.append(target.read_bytes())
    def setUp(self):
        self.tempdir=tempfile.TemporaryDirectory();self.addCleanup(self.tempdir.cleanup)
        self.out=Path(self.tempdir.name)
        self.files=[m.RemoteFile(f'/ipcam/{i}.mp4',len(data),m.parse_mlsd_modify(f'2026110812{i:02}00')) for i,data in enumerate(self.media)]
        self.job=m.RecordingJob(self.files)
        self.ftp=Mock()
        self.ftp.retrbinary.side_effect=lambda cmd,cb,**kw:cb(self.media[int(Path(cmd[5:]).stem)])
        self.quiet=contextlib.redirect_stdout(io.StringIO());self.quiet.__enter__()
        self.addCleanup(self.quiet.__exit__,None,None,None)
    def run_job(self):return m.download_job(self.ftp,self.job,'/ipcam',self.out,self.ffmpeg)
    def test_download_merge_decode_and_cleanup(self):
        import subprocess,re
        self.assertEqual(self.run_job(),'downloaded')
        outputs=list(self.out.glob('job_*.mp4'));self.assertEqual(len(outputs),1)
        info=subprocess.run([self.ffmpeg,'-i',str(outputs[0]),'-f','null','-'],capture_output=True,text=True)
        self.assertEqual(info.returncode,0)
        self.assertIn('Duration: 00:00:02.',info.stderr)
        self.assertEqual(self.ftp.retrbinary.call_count,2)
        self.assertEqual(list((self.out/'.h2c-parts').iterdir()),[])
    def test_merge_failure_retains_chunks_and_retry_uses_cache(self):
        with patch.object(m.subprocess,'run',return_value=Mock(returncode=1,stderr='test failure')):
            with self.assertRaises(RuntimeError):self.run_job()
        self.assertEqual(len(list((self.out/'.h2c-parts').rglob('chunk-*.mp4'))),2)
        self.assertEqual(list(self.out.glob('job_*.mp4')),[])
        self.ftp.retrbinary.reset_mock()
        self.assertEqual(self.run_job(),'downloaded')
        self.ftp.retrbinary.assert_not_called()
    def test_incomplete_chunk_not_merged(self):
        self.ftp.retrbinary.side_effect=lambda cmd,cb,**kw:cb(b'incomplete')
        with self.assertRaises(OSError):self.run_job()
        self.assertEqual(list(self.out.glob('job_*.mp4')),[])
        self.assertEqual(list((self.out/'.h2c-parts').rglob('.partial-*')),[])
    def test_existing_merged_file_preserved(self):
        self.run_job();dest=next(self.out.glob('job_*.mp4'));dest.write_bytes(b'existing')
        self.assertEqual(self.run_job(),'downloaded')
        self.assertEqual(dest.read_bytes(),b'existing')
        self.assertEqual(len(list(self.out.glob('job_*.mp4'))),2)

class ProgressChecks(unittest.TestCase):
    def test_job_percent_rate_and_eta(self):
        with patch.object(m.time,'monotonic',return_value=10) as clock, contextlib.redirect_stdout(io.StringIO()):
            progress=m.DownloadProgress(1000)
            clock.return_value=12
            progress.update(200)
            done,percent,rate,eta=progress.snapshot()
            self.assertEqual((done,percent,rate,eta),(200,20,100,8))
    def test_cached_bytes_do_not_inflate_transfer_rate(self):
        with patch.object(m.time,'monotonic',return_value=10) as clock, contextlib.redirect_stdout(io.StringIO()):
            progress=m.DownloadProgress(1000,500)
            clock.return_value=12
            progress.update(100)
            self.assertEqual(progress.snapshot(),(600,60,50,8))
    def test_unknown_size_and_zero_size(self):
        with contextlib.redirect_stdout(io.StringIO()):
            progress=m.DownloadProgress(None)
            progress.update(100)
            self.assertIsNone(progress.snapshot()[1])
            self.assertIsNone(progress.snapshot()[3])
            self.assertEqual(m.DownloadProgress(0).snapshot()[1::2],(100,0))
    def test_completion_eta_zero(self):
        with patch.object(m.time,'monotonic',return_value=10), contextlib.redirect_stdout(io.StringIO()):
            progress=m.DownloadProgress(100)
            progress.update(100)
            self.assertEqual(progress.snapshot()[1::2],(100,0))
    def test_duration_format(self):
        self.assertEqual(m.duration_label(3661),'01:01:01')
        self.assertEqual(m.duration_label(None),'calculating...')
    def test_output_throttled_but_counts_every_block(self):
        output=io.StringIO()
        with patch.object(m.time,'monotonic',return_value=0) as clock, contextlib.redirect_stdout(output):
            progress=m.DownloadProgress(100)
            progress.update(force=True)
            clock.return_value=.5
            progress.update(25)
            self.assertEqual(len(output.getvalue().splitlines()),1)
            clock.return_value=1
            progress.update(25)
            self.assertEqual(len(output.getvalue().splitlines()),2)
            self.assertIn('50.0%',output.getvalue())
            self.assertIn('remaining 00:00:01',output.getvalue())

if __name__=='__main__':unittest.main(verbosity=2)
