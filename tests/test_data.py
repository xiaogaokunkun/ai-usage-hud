import importlib.util
import os
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

ROOT = Path(__file__).resolve().parents[1]

class DataTests(unittest.TestCase):
    def setUp(self):
        spec=importlib.util.spec_from_file_location('hud_data_test',ROOT/'ai-hud.py')
        self.data=importlib.util.module_from_spec(spec);spec.loader.exec_module(self.data)
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.directory=Path(self.temp.name)
        self.data.LOG_FILE=str(self.directory/'test.log')
        self.data.STATE_DB=str(self.directory/'usage.db')
        self.data.ENV_FILE=str(self.directory/'missing.env')
    def test_environment_key_precedes_file(self):
        file=self.directory/'local.env';file.write_text('DEEPSEEK_API_KEY=file-test-value\n',encoding='utf-8');self.data.ENV_FILE=str(file)
        with patch.dict(os.environ,{'DEEPSEEK_API_KEY':'environment-test-value'}):
            self.assertEqual(self.data.read_env_key('DEEPSEEK_API_KEY'),'environment-test-value')
    def test_database_aggregates_supported_sources_only(self):
        with sqlite3.connect(self.data.STATE_DB) as db:
            db.execute('CREATE TABLE session_model_usage (billing_provider TEXT,billing_base_url TEXT,input_tokens INTEGER,output_tokens INTEGER,cache_read_tokens INTEGER,estimated_cost_usd REAL,last_seen REAL)')
            db.executemany('INSERT INTO session_model_usage VALUES (?,?,?,?,?,?,?)',[
                ('deepseek','https://api.deepseek.com/v1',100,20,30,.3,time.time()),
                ('custom','https://api.xiaomimimo.com/v1',10,5,0,None,time.time()),
                ('other','https://example.invalid',1000,1000,1000,9,time.time()),
                ('deepseek','https://api.deepseek.com/v1',900,0,0,4,0)])
        db.close()
        usage,cost=self.data.aggregate_usage()
        self.assertEqual(usage,{'deepseek':150,'mimo':15})
        self.assertAlmostEqual(cost['deepseek'],.3);self.assertEqual(cost['mimo'],0)
    def test_missing_database_is_zero_not_an_exception(self):
        usage,cost=self.data.aggregate_usage()
        self.assertEqual(sum(usage.values()),0);self.assertEqual(sum(cost.values()),0)
    def test_expired_cookie_backs_off_and_preserves_last_balance(self):
        cookie=self.directory/'cookie.txt';cookie.write_text('session=test-only',encoding='utf-8')
        self.data.MIMO_COOKIE_FILE=str(cookie);self.data.STATE['balance']['mimo']=7.5
        error=HTTPError('https://example.invalid',401,'Unauthorized',None,None)
        with patch.object(self.data,'read_env_key',return_value=None),patch.object(self.data.urllib.request,'urlopen',side_effect=error) as request:
            self.data.refresh();self.data.refresh()
        self.assertEqual(request.call_count,1)
        self.assertEqual(self.data.STATE['source_status']['mimo'],'expired')
        self.assertEqual(self.data.STATE['balance']['mimo'],7.5)

if __name__=='__main__':unittest.main()
