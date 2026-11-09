import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
ROOT=Path(__file__).resolve().parents[1]
class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.config=Path(self.temp.name)/'settings.json'
        self.folder=Path(self.temp.name)/'videos'
    def call(self,action,**kwargs):
        result=subprocess.run([sys.executable,str(ROOT/'python/worker.py')],input=json.dumps(dict(action=action,**kwargs))+'\n',capture_output=True,text=True,env={**os.environ,'H2C_CONFIG_FILE':str(self.config)})
        return json.loads(result.stdout.strip().splitlines()[-1])
    def settings(self,**extra):
        return dict(host='printer.local',port=990,remoteDir='/ipcam',downloadFolder=str(self.folder),sessionGap=20,timezone='America/New_York',filenameUtcOffset=8,accessCode='test-secret',**extra)
    def test_save_redacts_password_and_restricts_permissions(self):
        result=self.call('save-settings',data=self.settings())
        self.assertEqual(result['type'],'result');self.assertNotIn('test-secret',json.dumps(result))
        self.assertEqual(self.config.stat().st_mode&0o777,0o600)
        self.assertTrue(self.call('settings')['data']['hasAccessCode'])
    def test_blank_code_preserves_existing_secret(self):
        self.call('save-settings',data=self.settings());data=self.settings();data['accessCode']=''
        self.assertEqual(self.call('save-settings',data=data)['type'],'result')
        self.assertEqual(json.loads(self.config.read_text())['access_code'],'test-secret')
    def test_changed_printer_requires_its_code(self):
        self.call('save-settings',data=self.settings());data=self.settings();data.update(host='other.local',accessCode='')
        self.assertEqual(self.call('save-settings',data=data)['type'],'error')
        self.assertEqual(json.loads(self.config.read_text())['last_host'],'printer.local')
    def test_validation_does_not_write_invalid_settings(self):
        for key,value in [('port',0),('sessionGap',-1),('timezone','Not/A_Zone'),('downloadFolder','relative'),('filenameUtcOffset',100),('remoteDir','relative')]:
            data=self.settings();data[key]=value
            self.assertEqual(self.call('save-settings',data=data)['type'],'error',key)
        self.assertFalse(self.config.exists())
    def test_library_only_lists_real_mp4_files(self):
        self.call('save-settings',data=self.settings());self.folder.mkdir()
        (self.folder/'job_test.mp4').write_bytes(b'test')
        (self.folder/'thumb.jpg').write_bytes(b'test');(self.folder/'ignored.mp4').symlink_to(self.folder/'job_test.mp4')
        data=self.call('library')['data'];self.assertEqual(len(data),1);self.assertEqual(data[0]['name'],'job_test.mp4')
if __name__=='__main__':unittest.main()
