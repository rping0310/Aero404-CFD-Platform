"""Real HTTP/browser integration in temporary data; NO scheduler/SSH/solver.

Run from aero404_debug: python tests/test_liquid_platform.py
Requires Playwright + Edge, FastAPI, uvicorn, httpx, PyYAML, Pillow.
"""
import json
import os
import socket
import struct
import sys
import tempfile
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import httpx
import uvicorn
import yaml
from PIL import Image
from playwright.sync_api import sync_playwright, expect

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / 'backend'))


def run():
    with tempfile.TemporaryDirectory(prefix='aero404-ui-') as tmp:
        root = Path(tmp)
        data, nfs = root / 'data', root / 'nfs'
        data.mkdir()
        os.environ['CFD_PLATFORM_DATA_DIR'] = str(data)
        from app.auth import hash_password
        config = {'nfs_root':str(nfs), 'viewer_data_path':str(nfs),
                  'simulation_type_folders':{'1':'Straight','2':'Yaw','3':'Cornering'},
                  'template_folder_name':'template_CFD','entry_macro':'gui_run.java',
                  'uploaded_file_pattern':r'CASE_\d+\.x_t', 'max_upload_size_mb':5}
        servers = [{'name':'Isolated test','host':'127.0.0.1','nfs_path':str(nfs),
                    'total_cores':16,'reserved_cores':0,'enabled':True,
                    'starccm_path':'NEVER_EXECUTE'}]
        for name, obj in [('app_settings',config),('servers',{'servers':servers}),
                          ('users',{'users':[{'name':r,'role':r,'password':hash_password('test-only-password')} for r in ['admin','user','visitor']]}),
                          ('workflow_template',{'steps':[]})]:
            (data / (name+'.yaml')).write_text(yaml.safe_dump(obj),encoding='utf-8')
        (data/'case_parameter_default.dat').write_text('simulation_type = 1\nfree_stream_velocity = 15\n')
        for folder in ['Straight','Cornering']:
            case = nfs / folder / 'CASE_001'
            (case/'z velocity').mkdir(parents=True)
            (case/'Result.dat').write_text('mean_Cla = 3.2\nAero_balance_z = 0.46\n')
            (case/'case_parameter.dat').write_text('free_stream_velocity = 15\nsimulation_type = 1\n')
            for i in range(3):
                Image.new('RGB',(320,180),(40+i*40,90,160)).save(case/'z velocity'/f'{i}.png')
            for name in ['Downforce.csv','Residuals.csv']:
                (case/name).write_text(','.join(['Iteration']+[f'Series {i}' for i in range(10)])+'\n'+
                                      '\n'.join(','.join(str(j+i) for i in range(11)) for j in range(1,31)))
            (case/'q-criterion.stl').write_bytes(b'test'.ljust(80,b'\0')+struct.pack('<I',1)+struct.pack('<12fH',0,0,1,0,0,0,1,0,0,0,1,0,0))
            (case/'workflow.log').write_text('isolated workflow log\ncompleted\n')
        template=nfs/'Straight'/'template_CFD';template.mkdir()
        (template/'template.sim').write_text('TEST FILE ONLY')
        (template/'gui_run.java').write_text('// test only')
        from app.main import app
        from app.jobs.queue_manager import JobQueueManager
        from app.jobs.models import JobStatus, CoreMode
        class NoExecute:
            def start(self,*a,**kw): raise AssertionError('Solver must never run in UI tests')
            def kill(self,*a,**kw): raise AssertionError('No process to kill')
            def read_log_page(self,*a): return ('isolated workflow log\ncompleted',1,1,2)
        class NoDispatchQueue(JobQueueManager):
            def _try_schedule(self, server_name): pass
        queue = NoDispatchQueue(NoExecute(),data/'jobs.json',nfs)
        job = queue.add_job('CASE_001','Isolated test',CoreMode.HALF,'admin',
                            work_dir=str(nfs/'Straight'/'CASE_001'),simulation_type=1)
        job.status=JobStatus.DONE
        @asynccontextmanager
        async def safe_lifespan(application):
            application.state.queue_manager=queue
            application.state.monitor=SimpleNamespace(server_metrics={'Isolated test':{'online':True,'cpu_percent':37.5}})
            yield
        app.router.lifespan_context=safe_lifespan
        sock=socket.socket();sock.bind(('127.0.0.1',0));sock.listen(128)
        port=sock.getsockname()[1]
        server=uvicorn.Server(uvicorn.Config(app,log_level='error'))
        thread=threading.Thread(target=lambda:server.run(sockets=[sock]),daemon=True)
        thread.start()
        base=f'http://localhost:{port}'
        try:
            with sync_playwright() as p:
                browser=p.chromium.launch(channel='msedge',headless=True)
                context=browser.new_context(viewport={'width':1440,'height':900})
                page=context.new_page(); errors=[]; failures=[]
                page.on('pageerror',lambda e:errors.append(str(e)))
                page.on('response',lambda r: failures.append((r.status,r.url)) if r.status>=400 and '/api/me' not in r.url else None)
                page.goto(base,wait_until='networkidle')
                expect(page.locator('.login')).to_be_visible()
                page.locator('[x-model="loginForm.name"]').fill('admin')
                page.locator('[x-model="loginForm.password"]').fill('test-only-password')
                page.get_by_role('button',name='Log In',exact=True).click()
                expect(page.locator('.server-card .cpu')).to_have_text('CPU 37.5%')
                def tab(name):
                    button=page.get_by_role('button',name=name,exact=True)
                    button.focus();button.click()
                tab('Case List')
                page.screenshot(path=str(PROJECT/'tests'/'preview-platform.png'))
                case_section=page.locator('section[x-show="tab===\'cases\'"]')
                case_section.locator('tbody tr').filter(has_text='CASE_001').first.click()
                page.get_by_role('button',name='Case List',exact=True).focus()
                page.wait_for_function("document.body.classList.contains('scene-glass-ready')",timeout=20000)
                page.get_by_role('button',name='📌 Load as Main',exact=True).click()
                page.locator('.chip').filter(has_text='Cornering').click()
                case_section.locator('tbody tr').filter(has_text='CASE_001').first.click()
                page.get_by_role('button',name='Load as Sub',exact=True).click()
                tab('Plots')
                expect(page.locator('#plot-main .main-svg').first).to_be_visible()
                expect(page.locator('#plot-sub .main-svg').first).to_be_visible()
                expect(page.locator('.series-list').first.locator('input')).to_have_count(10)
                assert page.locator('.series-list').first.evaluate('e=>e.scrollHeight>e.clientHeight')
                page.locator('.series-list').first.locator('input').last.uncheck()
                page.wait_for_function("document.getElementById('plot-main').data?.[9].visible==='legendonly'")
                page.get_by_label('Main monitor file').select_option('Residuals.csv')
                expect(page.get_by_label('Sub monitor file')).to_have_value('Downforce.csv')
                tab('Slices')
                page.wait_for_function("[...document.querySelectorAll('.slice-viewport img')].some(i=>i.complete&&i.naturalWidth>0)")
                tab('3D')
                page.wait_for_function("document.querySelector('#stl-main canvas')!==null",timeout=20000)
                tab('Gallery')
                expect(page.locator('.gallery-page .upload-card')).to_have_count(1)
                expect(page.locator('.gallery-page .panel-title button')).to_have_count(0)
                page.locator('.gallery-page input[type=file]').set_input_files(str(nfs/'Straight'/'CASE_001'/'z velocity'/'0.png'))
                expect(page.locator('.wallpaper-card')).to_have_count(2)
                shared=json.loads((data/'wallpapers'/'gallery.json').read_text(encoding='utf-8'))
                shared_id=shared['selected']
                assert (data/'wallpapers'/f'{shared_id}.jpg').is_file()
                page.locator('.gallery-controls select').select_option('light')
                assert not page.locator('body').evaluate("e=>e.classList.contains('theme-dark')")
                tab('Job Session')
                page.get_by_role('button',name='📄',exact=True).first.click()
                expect(page.locator('[x-show="logOpen"]')).to_be_visible()
                expect(page.locator('[x-show="logOpen"]')).to_contain_text('isolated workflow log')
                page.keyboard.press('Escape')
                page.get_by_role('button',name='＋ Add Case',exact=True).click()
                page.locator('[x-model="newJob.case_name"]').fill('CASE_002')
                page.locator('[x-model="newJob.note"]').fill('UI integration test')
                page.locator('[x-show="addOpen"] input[type=file]').set_input_files({'name':'CASE_002.x_t','mimeType':'application/octet-stream','buffer':b'ISOLATED TEST GEOMETRY'})
                page.locator('[x-show="addOpen"] button[type=submit]').click()
                expect(page.locator('[x-show="addOpen"]')).to_be_hidden()
                assert (nfs/'Straight'/'CASE_002'/'CASE_002.sim').exists()
                created=next(j for j in queue.list_jobs() if j.case_name=='CASE_002')
                assert created.status==JobStatus.WAITING
                assert created.note=='UI integration test'
                assert (nfs/'Straight'/'CASE_002'/'CASE_002.x_t').read_bytes()==b'ISOLATED TEST GEOMETRY'
                page.locator('section[x-show="tab===\'jobs\'"] tr').filter(has_text='CASE_002').locator('.note-cell').click()
                page.locator('[x-model="noteModalText"]').fill('Edited through real API')
                page.locator('[x-show="noteModalOpen"]').get_by_role('button',name='Save',exact=True).click()
                expect(page.locator('[x-show="noteModalOpen"]')).to_be_hidden()
                assert created.note=='Edited through real API'
                page.get_by_role('button',name='📋 Waiting List',exact=True).click()
                expect(page.locator('[x-show="waitingOpen"]')).to_contain_text('CASE_002')
                page.keyboard.press('Escape')
                # Test core permissions against the real API without browser interception.
                with httpx.Client(base_url=base) as client:
                    result=client.post('/api/login',json={'name':'visitor','password':'test-only-password'})
                    assert result.status_code==200
                    token=result.cookies.get('cfd_session')
                    headers={'Cookie':'cfd_session='+token}
                    assert client.put('/api/settings/case_parameter_default',json={'content':{}},headers=headers).status_code==403
                    assert client.delete('/api/jobs/failed',headers=headers).status_code==403
                    assert client.put(f'/api/jobs/{job.id}/note',json={'note':'blocked'},headers=headers).status_code==403
                tab('Settings')
                expect(page.locator('textarea[x-model="defaultsText"]')).to_be_visible()
                page.locator('textarea[x-model="defaultsText"]').fill(json.dumps({'simulation_type':'1','free_stream_velocity':'18'}))
                with page.expect_response(lambda r: '/api/settings/case_parameter_default' in r.url and r.request.method=='PUT') as saved:
                    page.get_by_role('button',name='💾 Save',exact=True).click()
                assert saved.value.status==200
                assert '18' in (data/'case_parameter_default.dat').read_text()
                page.reload(wait_until='networkidle')
                # Login session and imported photo both survive reload.
                expect(page.locator('.server-card .cpu')).to_have_text('CPU 37.5%')
                tab('Gallery');expect(page.locator('.wallpaper-card')).to_have_count(2)
                page.get_by_role('button',name='Log Out',exact=True).click()
                expect(page.locator('.login')).to_be_visible()
                # The readonly role also has no editor/admin controls in the UI.
                page.locator('[x-model="loginForm.name"]').fill('visitor')
                page.locator('[x-model="loginForm.password"]').fill('test-only-password')
                page.get_by_role('button',name='Log In',exact=True).click()
                expect(page.locator('.server-card .cpu')).to_have_text('CPU 37.5%')
                expect(page.get_by_role('button',name='Settings',exact=True)).to_be_hidden()
                expect(page.get_by_role('button',name='＋ Add Case',exact=True)).to_be_hidden()
                tab('Gallery')
                expect(page.locator('.wallpaper-card')).to_have_count(2)
                expect(page.locator('.upload-card')).to_be_hidden()
                page.wait_for_function("id=>document.querySelector('#photo-wallpaper').style.backgroundImage.includes(id)",arg=shared_id)
                # A second authenticated client reads the same server file and selection.
                with httpx.Client(base_url=base) as client:
                    assert client.get('/api/gallery').status_code==401
                    result=client.post('/api/login',json={'name':'visitor','password':'test-only-password'})
                    headers={'Cookie':'cfd_session='+result.cookies.get('cfd_session')}
                    state=client.get('/api/gallery',headers=headers).json()
                    assert state['selected']==shared_id and len(state['photos'])==2
                    assert client.get(f'/api/gallery/photos/{shared_id}',headers=headers).headers['content-type']=='image/jpeg'
                    assert client.put('/api/gallery/selection',json={'id':'alpine'},headers=headers).status_code==403
                    assert client.post('/api/gallery/photos',files={'file':('x.png',b'not an image','image/png')},headers=headers).status_code==403
                    result=client.post('/api/login',json={'name':'admin','password':'test-only-password'})
                    headers={'Cookie':'cfd_session='+result.cookies.get('cfd_session')}
                    assert client.post('/api/gallery/photos',files={'file':('x.png',b'not an image','image/png')},headers=headers).status_code==422
                    assert client.post('/api/gallery/photos',files={'file':('x.svg',b'<svg/>','image/svg+xml')},headers=headers).status_code==415
                    assert client.get('/api/gallery/photos/not-a-valid-id',headers=headers).status_code==404
                    assert client.put('/api/gallery/selection',json={'id':'alpine'},headers=headers).status_code==200
                # Existing clients adopt the administrator's new selection on polling.
                page.wait_for_function("document.querySelector('#photo-wallpaper').style.backgroundImage.includes('alpine-lake.jpg')",timeout=12000)
                page.set_viewport_size({'width':390,'height':844})
                assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                page.emulate_media(reduced_motion='reduce')
                assert page.locator('.sidebar').evaluate("e=>getComputedStyle(e).transitionDuration.split(',').every(v=>parseFloat(v)===0)")
                assert not errors,errors
                assert not failures,failures
                browser.close()
            print('PASS: real login/session, CPU, cases, independent CSVs, 10-series scroll/toggle, PNG, STL, server-stored/shared Gallery, admin upload permissions, invalid-file rejection, live background sync, logs, job upload/queue, notes, Settings, visitor permissions, mobile. Dispatch disabled.')
        finally:
            server.should_exit=True;thread.join(timeout=10);sock.close()


if __name__=='__main__':
    run()
