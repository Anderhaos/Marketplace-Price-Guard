/* Headless Chrome browser acceptance: isolated, synthetic, read-only WB seller. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const puppeteer = require(process.env.NOSALE_PUPPETEER_PACKAGE || 'puppeteer');
const base = process.env.NOSALE_E2E_URL || 'http://127.0.0.1:8941';
const chrome = process.env.NOSALE_E2E_CHROME;
const snapshotDir = process.env.NOSALE_E2E_SHOTS || '/tmp/nosale-e2e-shots';
if (!/^http:\/\/127\.0\.0\.1:\d+$/.test(base) || !chrome) throw Error('Refusing nonlocal browser fixture');
const sellerEmail = "seller-browser-"+Date.now()+"@example.test";
const timeline = [];
const step = (name, detail) => { timeline.push({step:name,detail:detail||'PASS'});console.log('PASS '+name+' '+(detail||'')); };
(async () => {
  fs.mkdirSync(snapshotDir,{recursive:true});
  const browser=await puppeteer.launch({executablePath:chrome,headless:true,args:['--no-sandbox','--disable-dev-shm-usage','--disable-gpu','--no-first-run','--no-default-browser-check']});
  const page=await browser.newPage();
  const failures=[];
  page.on('pageerror',e=>failures.push(e.message));
  await page.setViewport({width:1365,height:860,deviceScaleFactor:1});
  await page.setRequestInterception(true);
  page.on('request',req=>{if(req.url().startsWith(base+'/')) req.continue();else req.abort();});
  const goto=async path => {let r=await page.goto(base+path,{waitUntil:'domcontentloaded',timeout:16000});if(!r)throw Error('No response '+path);assert.ok([200,302].includes(r.status()),path+': HTTP '+r.status());return r;};
  const waitText=async (needle,timeout=10000) => {await page.waitForFunction(s=>document.body.innerText.includes(s),{timeout},needle);};
  const submit=async selector => {await Promise.all([page.waitForNavigation({waitUntil:'domcontentloaded',timeout:16000}),page.click(selector)]);};
  try {
    // Preserve the original email-first welcome appearance at the public root.
    await goto('/');
    assert.equal(new URL(page.url()).pathname, '/', 'root must stay clean without ?next=/');
    assert.ok(await page.$('#welcome-headline'), 'original welcome design missing');
    assert.ok(await page.$('#welcome-auth input[name="email"]'), 'original email form missing');
    assert.equal(await page.$('.public-landing'), null, 'unrequested marketing redesign still visible');
    const publicMeta = await page.evaluate(() => ({
      canonical: document.querySelector('link[rel="canonical"]')?.href,
      description: document.querySelector('meta[name="description"]')?.content,
      gutter: getComputedStyle(document.documentElement).scrollbarGutter,
      mainLeft: document.querySelector('main.shell').getBoundingClientRect().left
    }));
    assert.equal(publicMeta.canonical, 'https://nosale.pro/');
    assert.ok(publicMeta.description.includes('Wildberries'));
    assert.ok(publicMeta.gutter.includes('stable'), 'scrollbar gutter must be reserved');
    await page.screenshot({path:snapshotDir+'/00-original-welcome-desktop.png',fullPage:true});
    for (const width of [390,320]) {
      await page.setViewport({width,height:844,deviceScaleFactor:1,isMobile:true,hasTouch:true});
      await goto('/');
      const state = await page.evaluate(() => ({
        overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
        original: !!document.querySelector('.welcome-story') && !!document.querySelector('.welcome-panel'),
        headerHeight: document.querySelector('.site-header').getBoundingClientRect().height
      }));
      assert.ok(state.overflow <= 1, 'Original welcome has mobile overflow at '+width+': '+JSON.stringify(state));
      assert.ok(state.original, 'Original welcome layout missing at '+width);
      await page.screenshot({path:snapshotDir+'/00-original-welcome-mobile-'+width+'.png',fullPage:true});
    }
    await page.setViewport({width:1365,height:860,deviceScaleFactor:1,isMobile:false,hasTouch:false});
    await goto('/login/');
    assert.ok(await page.$('#welcome-headline'));
    const loginMainLeft = await page.$eval('main.shell',el=>el.getBoundingClientRect().left);
    assert.ok(Math.abs(publicMeta.mainLeft-loginMainLeft)<=1,
      'Original home and login must share identical left boundary');
    const noScrollbarShift = await page.evaluate(() => {
      const header = document.querySelector('.header-inner');
      const main = document.querySelector('main.shell');
      const was = main.style.display;
      main.style.display = 'none';
      const shortLeft = header.getBoundingClientRect().left;
      const tall = document.createElement('div');
      tall.style.flex = '0 0 200vh';
      document.body.appendChild(tall);
      const tallLeft = header.getBoundingClientRect().left;
      tall.remove();
      main.style.display = was;
      return Math.abs(shortLeft-tallLeft);
    });
    assert.ok(noScrollbarShift <= 1,'Scrollbar causes page to jump: '+noScrollbarShift);
    const alignment = async () => page.evaluate(() => {
      const story = document.querySelector('.welcome-story')?.getBoundingClientRect();
      const form = document.querySelector('.welcome-panel')?.getBoundingClientRect();
      const brand = document.querySelector('.site-header .brand')?.getBoundingClientRect();
      return {left:story?.left,storyTop:story?.top,panelTop:form?.top,brandX:brand?.left,brandY:brand?.top};
    });
    const loginPosition = await alignment();
    assert.ok(Math.abs(loginPosition.storyTop-loginPosition.panelTop)<=1,
      'Login columns are not top-aligned: '+JSON.stringify(loginPosition));
    assert.ok(await page.$('input[name="email"]'));
    step('original_design_restored_with_seo_no_layout_shift');
    await page.type('input[name="email"]',sellerEmail);
    await submit('button[type=submit]');
    assert.ok(page.url().endsWith('/auth/code/'),'expected email verification, got '+page.url());
    const codePosition = await alignment();
    assert.ok(Math.abs(codePosition.storyTop-codePosition.panelTop)<=1,'Code columns not top aligned');
    assert.ok(Math.abs(codePosition.brandX-loginPosition.brandX)<=1 && Math.abs(codePosition.brandY-loginPosition.brandY)<=1,
      'Brand/header moves between login and email code steps');
    await page.screenshot({path:snapshotDir+'/01-code.png'});
    step('email_registration_code_page');
    await page.type('input[name="code"]','0007');
    await submit('button[type=submit]');
    assert.ok(page.url().endsWith('/auth/pin/'));
    const pinPosition = await alignment();
    assert.ok(Math.abs(pinPosition.storyTop-pinPosition.panelTop)<=1,'PIN columns not top aligned');
    assert.ok(Math.abs(pinPosition.brandX-loginPosition.brandX)<=1 && Math.abs(pinPosition.brandY-loginPosition.brandY)<=1,
      'Brand/header moves between login and PIN registration');
    step('email_otp_verified');
    await page.type('input[name="pin"]','8394');
    await page.type('input[name="confirm_pin"]','8394');
    await submit('button[type=submit]');
    assert.ok(page.url().endsWith('/'));
    await page.waitForSelector('#wb-setup');
    assert.ok((await page.content()).includes('3 дня бесплатно'));
    step('new_account_and_wb_onboarding');
    await page.type('#onboarding-token','not-a-jwt');
    await submit('.setup-submit');
    await waitText('Не удалось определить права токена');
    step('reject_invalid_wb_token_no_trial');
    const payload=Buffer.from(JSON.stringify({acc:1,s:(1<<1)|(1<<3)})).toString('base64url');
    const syntheticToken='header.'+payload+'.signature';
    await page.$eval('#onboarding-token',e=>e.value='');
    await page.type('#onboarding-token',syntheticToken);
    await submit('.setup-submit');
    await waitText('Загружаем товары',10000).catch(()=>{});
    step('valid_wb_token_accepted_readonly');
    // A real server worker, not a browser stub, reads two synthetic WB objects.
    let ready=false;
    for(let i=0;i<12;i++){
      await goto('/');
      const text=await page.evaluate(()=>document.body.innerText);
      if(text.includes('Browser fixture item A') && text.includes('Browser fixture item B')){ready=true;break;}
      await new Promise(r=>setTimeout(r,850));
    }
    assert.ok(ready,'First import did not unlock a dashboard with 2 real ORM-backed synthetic goods');
    await page.screenshot({path:snapshotDir+'/02-dashboard-desktop.png',fullPage:true});
    const text=await page.evaluate(()=>document.body.innerText);
    assert.ok(text.includes('Browser fixture item A')&&text.includes('Browser fixture item B'));
    step('initial_inventory_synced_and_dashboard_ready','2 synthetic WB items');
    assert.ok(await page.$('body.seller-dashboard-page'));
    await page.evaluate(() => {
      const grid=document.querySelector('.products-grid');
      const first=grid.querySelector('.product-card');
      for(let i=0;i<5;i++) {
        const clone=first.cloneNode(true);
        clone.dataset.layoutClone='true';
        grid.appendChild(clone);
      }
    });
    const sizes=[
      [2560,5],[1920,5],[1700,5],[1699,4],[1536,4],[1366,4],
      [1300,4],[1299,3],[1024,3],[980,3],[979,2],[768,2],
      [640,2],[639,1],[390,1],[320,1]
    ];
    for(const [width,expected] of sizes) {
      await page.setViewport({width,height:900,deviceScaleFactor:1,isMobile:width<640,hasTouch:width<640});
      const state=await page.evaluate(()=>{
        const grid=document.querySelector('.products-grid');
        const cards=[...grid.querySelectorAll('.product-card')];
        const content=document.querySelector('main.shell').getBoundingClientRect();
        const header=document.querySelector('.header-inner').getBoundingClientRect();
        const rects=cards.map(card=>card.getBoundingClientRect());
        const sizes=getComputedStyle(grid).gridTemplateColumns.split(' ').filter(Boolean).length;
        return {
          width:innerWidth,pageWidth:document.documentElement.scrollWidth,
          cols:sizes,contentWidth:content.width,headerWidth:header.width,
          firstRow:rects.slice(0,sizes).every(r=>Math.abs(r.top-rects[0].top)<2),
          nextRow:rects.length<=sizes||rects[sizes].top>rects[0].top+8,
          minCardWidth:Math.min(...rects.map(r=>r.width)),
          maxCardOverflow:Math.max(...cards.map(el=>el.scrollWidth-el.clientWidth)),
        };
      });
      assert.equal(state.cols,expected,'Unexpected columns '+JSON.stringify(state));
      assert.ok(state.contentWidth<=1801 && state.contentWidth<=width-20,'Too wide '+JSON.stringify(state));
      assert.ok(Math.abs(state.contentWidth-state.headerWidth)<=2,'Header and inventory misaligned '+JSON.stringify(state));
      assert.ok(state.pageWidth<=width+2,'Horizontal overflow '+JSON.stringify(state));
      assert.ok(state.firstRow && state.nextRow,'Product placement invalid '+JSON.stringify(state));
      assert.ok(state.minCardWidth>230,'Cramped product cards '+JSON.stringify(state));
      assert.ok(state.maxCardOverflow<=2,'Card content overflows '+JSON.stringify(state));
      if([1920,1536,980,768,390].includes(width)){
        await page.screenshot({path:snapshotDir+'/responsive-'+width+'.png',fullPage:true});
      }
    }
    await page.evaluate(()=>document.querySelectorAll('.products-grid [data-layout-clone]').forEach(n=>n.remove()));
    await page.setViewport({width:1365,height:860,deviceScaleFactor:1,isMobile:false,hasTouch:false});
    step('product_grid_breakpoints_320_to_2560_including_five_across');
    // Read-only permits local policy editing without any WB discount upload.
    assert.ok(await page.$('.compact-limit-form button:not([disabled])'));
    await page.$eval('input[name="default_allowed_discount"]',field => { field.value='25'; });
    await submit('.compact-limit-form button[type="submit"]');
    await waitText('Предел 25% сохранён только в NoSale');
    assert.equal(await page.$eval('input[name="default_allowed_discount"]',n=>n.value),'25');
    step('readonly_local_limit_saved_without_wb_write');
    await Promise.all([
      page.waitForNavigation({waitUntil:'domcontentloaded',timeout:16000}),
      page.click('.product-rule input[name="no_discount"]'),
    ]);
    await waitText('Правило сохранено в NoSale');
    const localRule=await page.evaluate(()=>document.body.innerText);
    assert.ok(localRule.includes('Локальное правило'));
    step('readonly_local_product_override_saved_without_wb_write');
    // Only real WB functionality must be visible: no fake marketplace themes
    // or duplicate links to the same notifications screen.
    assert.equal(await page.$('details.marketplace-picker'),null,
      'Unconnected marketplaces must not appear in the seller navigation');
    assert.equal(await page.$('#theme-preview-banner'),null,
      'Decorative marketplace preview banner must be removed');
    assert.equal(await page.$('script[src*="marketplace-previews.js"]'),null);
    const navState=await page.evaluate(()=>{
      const nav=document.querySelector('.site-nav');
      return {
        primary:[...nav.querySelectorAll('a.nav-primary')].map(a=>a.textContent.trim()),
        notificationLinks:nav.querySelectorAll('a[href="/notifications/"]').length,
        bell:!!nav.querySelector('a.nav-notifications svg'),
        bellCount:nav.querySelector('a.nav-notifications .nav-notification-count')?.textContent.trim(),
        bellActive:nav.querySelector('a.nav-notifications')?.getAttribute('aria-label'),
        more:!!nav.querySelector('details.seller-more-menu'),
        links:[...nav.querySelectorAll('.seller-more-options a')].map(a=>new URL(a.href).pathname),
        hasWBLinks:[...document.querySelectorAll('.wb-quick-links a')].map(a=>a.href),
      };
    });
    assert.ok(navState.primary.includes('Товары'),'Inventory must remain primary');
    assert.ok(navState.primary.includes('Настройки'),'WB settings must remain available');
    assert.ok(navState.primary.length<=4,'Too many main navigation items: '+JSON.stringify(navState));
    assert.equal(navState.notificationLinks,1,'Notifications must appear exactly once');
    assert.ok(navState.bell,'Separate notifications bell must be present');
    assert.ok(/^\d+$/.test(navState.bellCount),'Unread counter must be numeric');
    assert.ok(navState.bellActive.includes('Уведомления'));
    assert.ok(navState.more,'Secondary navigation must be grouped');
    assert.ok(!navState.links.includes('/notifications/'),'Notifications must not be hidden under More');
    for(const url of ['/stores/','/billing/','/history/']){
      assert.ok(navState.links.includes(url),'Secondary section lost '+url);
    }
    assert.ok(navState.hasWBLinks.includes('https://www.wildberries.ru/'));
    assert.ok(navState.hasWBLinks.includes('https://seller.wildberries.ru/'));
    await page.click('details.seller-more-menu > summary');
    assert.ok(await page.$('details.seller-more-menu[open]'));
    await page.screenshot({path:snapshotDir+'/05-simplified-navigation.png'});
    step('minimal_real_marketplace_navigation_with_no_duplicates');
    await goto('/assistant/');
    assert.ok(page.url().endsWith('/notifications/'), 'Retired assistant URL must redirect to scheduled messages');
    assert.equal(await page.$('form[action="/push/preferences/"]'),null,'Notification feed must not contain settings');
    assert.equal(await page.$('#push-enable'),null,'Push controls belong to settings');
    assert.equal(await page.$('#assistant-question'), null, 'Free AI chat must not be visible');
    const summaryWidth=await page.evaluate(()=>({
      width:window.innerWidth,pageWidth:document.documentElement.scrollWidth
    }));
    assert.ok(summaryWidth.pageWidth<=summaryWidth.width+2);
    await page.screenshot({path:snapshotDir+'/06-smart-summaries-desktop.png',fullPage:true});
    step('smart_summaries_replace_interactive_chat');
    await goto('/billing/');
    await waitText('пробный период');
    const subscriptionText = await page.evaluate(()=>document.body.innerText);
    assert.ok(subscriptionText.toLowerCase().includes('standard'),'trial should have Standard capabilities');
    step('trial_granted_on_first_import');
    await goto('/settings/');
    await page.waitForSelector('form[action="/push/preferences/"]');
    assert.ok(await page.$('#notification-settings'),'Notifications settings section missing');
    step('seller_settings_access');
    await goto('/notifications/');
    assert.equal(await page.$('#notification-settings'),null);
    await waitText('Важных событий пока нет.');
    step('routine_sync_does_not_spam_important_notifications');
    await page.screenshot({path:snapshotDir+'/03-notifications.png'});
    await goto('/history/');
    await waitText('Проверок: 1');
    step('successful_wb_check_is_recorded_in_history');
    const workspaceRoutes=['/','/settings/','/notifications/','/promotions/','/history/','/stores/','/billing/'];
    for(const width of [1920,390]){
      await page.setViewport({width,height:900,deviceScaleFactor:1,isMobile:width<640,hasTouch:width<640});
      let expected=null;
      for(const route of workspaceRoutes){
        await goto(route);
        const geometry=await page.evaluate(()=>{
          const main=document.querySelector('main.shell').getBoundingClientRect();
          const header=document.querySelector('.header-inner').getBoundingClientRect();
          const footer=document.querySelector('.site-footer .shell').getBoundingClientRect();
          return {
            mainWidth:main.width,headerWidth:header.width,footerWidth:footer.width,
            mainLeft:main.left,headerLeft:header.left,footerLeft:footer.left,
            width:document.documentElement.scrollWidth,
            viewport:document.documentElement.clientWidth,
            shared:document.body.classList.contains('seller-workspace-page'),
          };
        });
        assert.ok(geometry.shared,'Shared seller layout missing on '+route);
        assert.ok(geometry.width<=geometry.viewport+2,'Horizontal overflow '+route+' at '+width);
        assert.ok(Math.abs(geometry.mainWidth-geometry.headerWidth)<=1,'Header/main width differs '+route+': '+JSON.stringify(geometry));
        assert.ok(Math.abs(geometry.mainWidth-geometry.footerWidth)<=1,'Footer/main width differs '+route+': '+JSON.stringify(geometry));
        assert.ok(Math.abs(geometry.mainLeft-geometry.headerLeft)<=1,'Header/main alignment differs '+route);
        assert.ok(Math.abs(geometry.mainLeft-geometry.footerLeft)<=1,'Footer/main alignment differs '+route);
        if(expected===null)expected=geometry.mainWidth;
        assert.ok(Math.abs(expected-geometry.mainWidth)<=1,'Inconsistent page width '+route+' at '+width);
      }
      step('seller_workspace_shared_geometry_'+width,'7 routes, no horizontal overflow');
    }
    await page.setViewport({width:390,height:844,deviceScaleFactor:1,isMobile:true,hasTouch:true});
    await goto('/');
    await waitText('Browser fixture item A');
    await page.screenshot({path:snapshotDir+'/04-dashboard-mobile.png',fullPage:true});
    const mobileDimensions = await page.evaluate(()=>({
      width:window.innerWidth, pageWidth:document.documentElement.scrollWidth,
    }));
    assert.ok(mobileDimensions.pageWidth <= mobileDimensions.width+2,
      'Unexpected horizontal overflow: '+JSON.stringify(mobileDimensions));
    step('mobile_dashboard_without_horizontal_overflow',JSON.stringify(mobileDimensions));
    await page.click('details.seller-more-menu > summary');
    await page.waitForFunction(()=>{
      const panel=document.querySelector('details.seller-more-menu[open] .seller-more-options');
      if(!panel)return false;
      const rect=panel.getBoundingClientRect();
      return rect.left>=0 && rect.right<=window.innerWidth+2;
    },{timeout:2000});
    const menuVisible=await page.$eval('details.seller-more-menu .seller-more-options',x=>{
      const r=x.getBoundingClientRect();
      const test=document.elementFromPoint(r.left+20,r.top+20);
      return {
        visible:r.width>190 && r.left>=0 && r.right<=window.innerWidth+2
          && !!(test && x.contains(test)),
        left:r.left,right:r.right,viewport:window.innerWidth,
        hit:test?.tagName || null,
      };
    });
    assert.ok(menuVisible.visible,'Compact extra menu must remain usable at 390px: '+JSON.stringify(menuVisible));
    assert.ok(await page.$('details.seller-more-menu[open] a[href="/stores/"]'));
    await page.screenshot({path:snapshotDir+'/05-simplified-navigation-mobile.png'});
    step('compact_more_menu_usable_on_mobile');
    await goto('/assistant/');
    const mobileAssistant=await page.evaluate(()=>({
      width:window.innerWidth,pageWidth:document.documentElement.scrollWidth,
    }));
    assert.ok(mobileAssistant.pageWidth<=mobileAssistant.width+2,
      'Mobile summaries have horizontal overflow: '+JSON.stringify(mobileAssistant));
    await page.screenshot({path:snapshotDir+'/07-summaries-mobile.png',fullPage:true});
    step('smart_summaries_mobile_no_horizontal_overflow');
    await page.setViewport({width:1365,height:860,deviceScaleFactor:1,isMobile:false,hasTouch:false});
    await goto('/');
    // CSRF-protected POST logout through actual page form (do not handcraft cookie).
    const logout=await page.$('form[action="/logout/"]');
    if(!logout) throw Error('No logout form found');
    await Promise.all([page.waitForNavigation({waitUntil:'domcontentloaded'}),page.$eval('form[action="/logout/"]',f=>f.submit())]);
    await goto('/login/');
    await page.type('input[name="email"]',sellerEmail);
    await submit('button[type=submit]');
    await page.waitForSelector('input[name=pin]');
    await page.type('input[name=pin]','8394');
    await submit('button[type=submit]');
    await waitText('Browser fixture item A');
    step('existing_seller_relogin_via_pin');
    // Verify password recovery ends in a logged-in dashboard, not a second login.
    const recoveryLogout=await page.$('form[action="/logout/"]');
    assert.ok(recoveryLogout, 'recovery prerequisite: dashboard logout unavailable');
    await Promise.all([
      page.waitForNavigation({waitUntil:'domcontentloaded'}),
      page.$eval('form[action="/logout/"]',f=>f.submit())
    ]);
    await goto('/password-reset/');
    await page.type('input[name="email"]',sellerEmail);
    await submit('button[type=submit]');
    assert.ok(page.url().endsWith('/auth/code/'),'recovery must issue email challenge');
    await page.type('input[name="code"]','0007');
    await submit('button[type=submit]');
    assert.ok(page.url().endsWith('/auth/pin/'),'recovery email must verify');
    await page.type('input[name="pin"]','5269');
    await page.type('input[name="confirm_pin"]','5269');
    await submit('button[type=submit]');
    assert.ok(page.url().endsWith('/'), 'PIN recovery must auto-login to dashboard');
    await waitText('Browser fixture item A');
    assert.ok(await page.$('form[action="/logout/"]'),'recovered account is not authenticated');
    await page.screenshot({path:snapshotDir+'/08-recovery-auto-login.png',fullPage:true});
    step('recovery_email_code_new_pin_auto_login');
    // Separate synthetic seller: 429 during first sync must NOT start trial
    // or open a fictional inventory dashboard.
    await goto('/');
    const secondLogout = await page.$('form[action="/logout/"]');
    assert.ok(secondLogout,'logout must remain present on dashboard');
    await Promise.all([page.waitForNavigation({waitUntil:'domcontentloaded'}),
      page.$eval('form[action="/logout/"]',f=>f.submit())]);
    const secondEmail='seller-rate-limit-'+Date.now()+'@example.test';
    await goto('/login/');
    await page.type('input[name="email"]',secondEmail);
    await submit('button[type=submit]');
    await page.type('input[name="code"]','0007');
    await submit('button[type=submit]');
    await page.type('input[name="pin"]','7328');
    await page.type('input[name="confirm_pin"]','7328');
    await submit('button[type=submit]');
    const limitedPayload=Buffer.from(JSON.stringify({
      acc:1,s:(1<<1)|(1<<3),nosale_e2e_scenario:'429'
    })).toString('base64url');
    await page.type('#onboarding-token','header.'+limitedPayload+'.signature');
    await submit('.setup-submit');
    let rateLimited=false;
    for(let i=0;i<15;i++){
      await goto('/');
      const screenText=await page.evaluate(()=>document.body.innerText);
      if(screenText.includes('Ожидаем разрешения WB') ||
         screenText.includes('Wildberries ограничил частоту запросов')){
        rateLimited=true;break;
      }
      await new Promise(r=>setTimeout(r,800));
    }
    assert.ok(rateLimited,'WB 429 must show a retry cooldown instead of unlocking dashboard');
    assert.ok(await page.$('button[disabled]'),'retry should be disabled until cooldown expires');
    assert.ok(!(await page.content()).includes('Browser fixture item A'),
      '429 store must never inherit inventory from another seller');
    step('wb_429_first_import_rate_limit_and_tenant_isolation');
    assert.deepEqual(failures,[],'JavaScript errors: '+JSON.stringify(failures));
    step('no_browser_javascript_exceptions');
    console.log('BROWSER_E2E_PASS stages='+timeline.length);
    fs.writeFileSync(snapshotDir+'/result.json',JSON.stringify({outcome:'PASS',timeline},null,2));
  } catch(error){
    const info={outcome:'FAIL',error:String(error.stack||error),url:page.url(),timeline,failures};
    fs.writeFileSync(snapshotDir+'/result.json',JSON.stringify(info,null,2));
    await page.screenshot({path:snapshotDir+'/failure.png',fullPage:true}).catch(()=>{});
    console.error('BROWSER_E2E_FAIL',info);
    process.exitCode=1;
  } finally {
    await browser.close();
  }
})().catch(e=>{console.error(e);process.exit(1)});
