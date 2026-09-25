# -*- coding: utf-8 -*-
from __future__ import annotations
import os,shutil,subprocess,tempfile
from pathlib import Path
from .http import Http
from .preflight import run_preflight,assert_preflight

SAFE_ROUTES={
 'главн':'/dashboard/command-center','сообщ':'/messages','объяв':'/dashboard/campaign','аналит':'/dashboard/analytics',
 'маркет':'/dashboard/marketing','директ':'/dashboard/director','crm':'/dashboard/crm','продаж':'/dashboard/crm',
 'соц':'/social','база':'/memory','видео':'/dashboard/video-factory','аккаун':'/dashboard/connect-avito'
}

def readiness():
    try:
        from playwright.sync_api import sync_playwright
        playwright=True
    except Exception: playwright=False
    needed=('BORIS_VIDEO_DEMO_EMAIL','BORIS_VIDEO_DEMO_PASSWORD','BORIS_VIDEO_DEMO_ACCOUNT_ID')
    configured=all(bool(os.environ.get(k)) for k in needed)
    return {'playwright':playwright,'demo_isolation_configured':configured,'capture_ready':playwright and configured,'safe_routes':sorted(set(SAFE_ROUTES.values())),'blocked_reason':None if configured else 'Не настроена изолированная demo-личность Video Factory.'}

def route_from_prompt(prompt):
    low=(prompt or '').lower()
    for key,route in SAFE_ROUTES.items():
        if key in low:return route
    return '/dashboard/command-center'

def _spotlight(page,selector,label=''):
    try:
        el=page.locator(selector).first
        if not el.is_visible(): return False
        el.scroll_into_view_if_needed(); page.wait_for_timeout(350)
        box=el.bounding_box()
        if not box:return False
        page.evaluate("""([x,y,w,h,label])=>{let o=document.getElementById('__vf_focus');if(o)o.remove();o=document.createElement('div');o.id='__vf_focus';o.style.cssText=`position:fixed;left:${x-8}px;top:${y-8}px;width:${w+16}px;height:${h+16}px;border:3px solid #2f6fed;border-radius:16px;box-shadow:0 0 0 9999px rgba(10,18,32,.20),0 0 0 6px rgba(47,111,237,.14);z-index:2147483646;pointer-events:none;transition:.2s`;document.body.appendChild(o);if(label){let b=document.createElement('div');b.id='__vf_label';b.textContent=label;b.style.cssText='position:fixed;z-index:2147483647;background:#172033;color:white;padding:8px 12px;border-radius:10px;font:700 14px system-ui;left:'+Math.max(18,x)+'px;top:'+Math.max(18,y-48)+'px;pointer-events:none';document.body.appendChild(b)}}""",[box['x'],box['y'],box['width'],box['height'],label])
        return True
    except Exception:return False

def _clear_focus(page):
    page.evaluate("document.getElementById('__vf_focus')?.remove();document.getElementById('__vf_label')?.remove()")

def _video_page_demo(page):
    # Real, readable lesson: every pause is intentional so the viewer can see the UI.
    page.wait_for_timeout(1800)
    _spotlight(page,'main','1. Здесь создаются ролики в BORIS'); page.wait_for_timeout(2200); _clear_focus(page)
    cards=page.locator('button.boris-campaign-prompt-card')
    if cards.count()>1:
        _spotlight(page,'button.boris-campaign-prompt-card:nth-of-type(2)','2. Выбираем рекламный ролик'); page.wait_for_timeout(1800); _clear_focus(page)
        cards.nth(1).click(); page.wait_for_timeout(1000)
    ta=page.locator('#video-prompt textarea').first
    if ta.is_visible():
        _spotlight(page,'#video-prompt textarea','3. Ставим задачу одним промтом'); page.wait_for_timeout(900)
        ta.fill('Сделай 30-секундный продающий ролик про ремонт фар в Санкт-Петербурге. Гарантия 1 год, цена от 3 500 ₽. Покажи проблему, результат и призыв написать.')
        page.wait_for_timeout(2300); _clear_focus(page)
    selects=page.locator('#video-prompt select')
    if selects.count()>=5:
        _spotlight(page,'#video-prompt label:nth-of-type(1)','4. Выбираем формат площадки'); page.wait_for_timeout(1000); selects.nth(0).select_option('youtube'); page.wait_for_timeout(900); _clear_focus(page)
        _spotlight(page,'#video-prompt label:nth-of-type(3)','5. Выбираем стиль'); page.wait_for_timeout(900); selects.nth(2).select_option('dynamic'); page.wait_for_timeout(900); _clear_focus(page)
        _spotlight(page,'#video-prompt label:nth-of-type(4)','6. Выбираем озвучку'); page.wait_for_timeout(900); selects.nth(3).select_option('none'); page.wait_for_timeout(900); _clear_focus(page)
    btn=page.locator('#video-prompt button').first
    if btn.is_visible():
        _spotlight(page,'#video-prompt button','7. Дальше BORIS собирает ролик сам'); page.wait_for_timeout(2100); _clear_focus(page)
    page.mouse.wheel(0,720); page.wait_for_timeout(1500)
    ready=page.locator('#ready-videos').first
    if ready.count() and ready.is_visible():
        _spotlight(page,'#ready-videos','8. Готовые ролики сохраняются здесь'); page.wait_for_timeout(1800); _clear_focus(page)
    page.wait_for_timeout(700)

def _generic_demo(page):
    page.wait_for_timeout(1400); page.mouse.move(760,340); page.wait_for_timeout(800)
    page.mouse.wheel(0,520);page.wait_for_timeout(1600);page.mouse.wheel(0,-260);page.wait_for_timeout(1300)

def capture(data,output_dir):
    ready=readiness()
    if not ready['capture_ready']: raise RuntimeError('tutorial_demo_isolation_not_configured')
    from playwright.sync_api import sync_playwright
    email=os.environ['BORIS_VIDEO_DEMO_EMAIL'];password=os.environ['BORIS_VIDEO_DEMO_PASSWORD'];account=os.environ['BORIS_VIDEO_DEMO_ACCOUNT_ID']
    api=os.environ.get('BORIS_VIDEO_API','http://127.0.0.1:8000');web=os.environ.get('BORIS_VIDEO_WEB','http://127.0.0.1:3002')
    http=Http(timeout=30,label='video-demo');ok,report=run_preflight(http,api,email,password,[account],web,[web]);assert_preflight(ok,report)
    logged,code,payload=http.login(api+'/api/auth/login',email,password)
    if not logged:raise RuntimeError(f'tutorial_demo_login_failed:{code}')
    token=http.token;route=route_from_prompt((data.get('plan') or {}).get('prompt',''));outdir=Path(output_dir);outdir.mkdir(parents=True,exist_ok=True);tmp=Path(tempfile.mkdtemp(prefix='tutorial_',dir=str(outdir)))
    try:
        with sync_playwright() as p:
            browser=p.chromium.launch(headless=True,args=['--no-sandbox','--disable-dev-shm-usage'])
            ctx=browser.new_context(viewport={'width':1920,'height':1080},record_video_dir=str(tmp),record_video_size={'width':1920,'height':1080})
            page=ctx.new_page();page.add_init_script(f"localStorage.setItem('boris_token',{token!r});localStorage.setItem('token',{token!r});")
            page.goto(web+route,wait_until='domcontentloaded',timeout=45000);page.wait_for_timeout(2500)
            if route=='/dashboard/video-factory':_video_page_demo(page)
            else:_generic_demo(page)
            _clear_focus(page);page.wait_for_timeout(700);video=page.video;ctx.close();browser.close();src=Path(video.path())
        # Keep Playwright's native WebM as the raw source. Re-encoding here doubled
        # render time and could leave a 48-byte MP4 if the capture job was interrupted.
        # The canonical renderer performs the single final H.264 encode afterwards.
        out=outdir/'tutorial_capture.webm'; shutil.copy2(src,out)
        return {'path':str(out),'route':route,'preflight':{'ok':report.get('ok'),'role':report.get('role'),'visible_account_count':len(report.get('visible_account_ids') or [])}}
    finally:shutil.rmtree(tmp,ignore_errors=True)

def capture_master(data,output_dir,scenes):
    ready=readiness()
    if not ready['capture_ready']: raise RuntimeError('tutorial_demo_isolation_not_configured')
    from playwright.sync_api import sync_playwright
    email=os.environ['BORIS_VIDEO_DEMO_EMAIL'];password=os.environ['BORIS_VIDEO_DEMO_PASSWORD'];account=os.environ['BORIS_VIDEO_DEMO_ACCOUNT_ID']
    api=os.environ.get('BORIS_VIDEO_API','http://127.0.0.1:8000');web=os.environ.get('BORIS_VIDEO_WEB','https://boris-ai.pro')
    http=Http(timeout=30,label='video-master');ok,report=run_preflight(http,api,email,password,[account],web,[web]);assert_preflight(ok,report)
    logged,code,payload=http.login(api+'/api/auth/login',email,password)
    if not logged: raise RuntimeError(f'tutorial_demo_login_failed:{code}')
    outdir=Path(output_dir);outdir.mkdir(parents=True,exist_ok=True);tmp=Path(tempfile.mkdtemp(prefix='master_',dir=str(outdir))); clips=[]
    try:
      with sync_playwright() as p:
        browser=p.chromium.launch(headless=True,args=['--no-sandbox','--disable-dev-shm-usage'])
        for i,sc in enumerate(scenes):
          cdir=tmp/f'c{i:02d}';cdir.mkdir()
          ctx=browser.new_context(viewport={'width':1920,'height':1080},record_video_dir=str(cdir),record_video_size={'width':1920,'height':1080})
          page=ctx.new_page();token=http.token;page.add_init_script(f"localStorage.setItem('boris_token',{token!r});localStorage.setItem('token',{token!r});")
          page.goto(web+sc['route'],wait_until='domcontentloaded',timeout=45000);page.wait_for_timeout(1800)
          # Gentle, human-like movement and focus; never click destructive actions.
          page.mouse.move(520,330);page.wait_for_timeout(500);page.mouse.wheel(0,220);page.wait_for_timeout(900);page.mouse.wheel(0,-90)
          _spotlight(page,'main',sc['caption']);page.wait_for_timeout(max(1200,int(float(sc['end']-sc['start'])*1000)-3600));_clear_focus(page);page.wait_for_timeout(500)
          v=page.video;ctx.close();clips.append(Path(v.path()))
        browser.close()
      # Normalize every scene to its planned duration and concatenate.
      norm=[]
      for i,(src,sc) in enumerate(zip(clips,scenes)):
        dst=tmp/f'n{i:02d}.mp4';dur=float(sc['end']-sc['start'])
        subprocess.run(['ffmpeg','-y','-stream_loop','-1','-i',str(src),'-an','-vf','scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080,fps=30','-t',str(dur),'-c:v','libx264','-preset','veryfast','-crf','20','-pix_fmt','yuv420p',str(dst)],check=True,capture_output=True)
        norm.append(dst)
      listing=tmp/'concat.txt';listing.write_text(''.join("file '%s'\n"%x for x in norm))
      out=outdir/'tutorial_capture.mp4';subprocess.run(['ffmpeg','-y','-f','concat','-safe','0','-i',str(listing),'-c','copy',str(out)],check=True,capture_output=True)
      return {'path':str(out),'route':'academy_master','preflight':{'ok':report.get('ok'),'role':report.get('role'),'visible_account_count':len(report.get('visible_account_ids') or [])},'scenes':len(scenes)}
    finally: shutil.rmtree(tmp,ignore_errors=True)
