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
    await goto('/login/');
    assert.ok(await page.$('input[name="email"]'));
    step('welcome_entry');
    await page.type('input[name="email"]',sellerEmail);
    await submit('button[type=submit]');
    assert.ok(page.url().endsWith('/auth/code/'),'expected email verification, got '+page.url());
    await page.screenshot({path:snapshotDir+'/01-code.png'});
    step('email_registration_code_page');
    await page.type('input[name="code"]','0007');
    await submit('button[type=submit]');
    assert.ok(page.url().endsWith('/auth/pin/'));
    step('email_otp_verified');
    await page.type('input[name="pin"]','8394');
    await page.type('input[name="confirm_pin"]','8394');
    await submit('button[type=submit]');
    assert.ok(page.url().endsWith('/'));
    await page.waitForSelector('#wb-setup');
    assert.ok((await page.content()).includes('5 дней бесплатно'));
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
    const marketplaceMenu = await page.$('details.marketplace-picker');
    assert.ok(marketplaceMenu,'Marketplaces need a clear selector in the header');
    await page.click('details.marketplace-picker > summary');
    assert.ok(await page.$('details.marketplace-picker[open]'));
    const themes = [
      {key:'ozon',label:'Ozon',accent:'#005bff'},
      {key:'yandex',label:'Яндекс Маркет',accent:'#ffcc00'},
      {key:'mega',label:'Мегамаркет',accent:'#7736e9'},
      {key:'wb',label:'Wildberries',accent:'#9636dc'}
    ];
    for (const theme of themes) {
      await page.click('[data-theme-preview="'+theme.key+'"]');
      const state=await page.evaluate(() => ({
        theme:document.body.dataset.previewMarketplace,
        accent:getComputedStyle(document.body).getPropertyValue('--accent').trim(),
        bannerHidden:document.getElementById('theme-preview-banner').hidden,
        label:document.getElementById('marketplace-current-label').textContent,
        items:document.querySelectorAll('.product-card').length,
        source:document.body.innerText.includes('Browser fixture item A'),
        wbCard:!!document.querySelector('a[href*="wildberries.ru/catalog/79001"]'),
        persisted:localStorage.getItem('nosale.marketplace-theme-preview.v1'),
        pageWidth:document.documentElement.scrollWidth,
        viewportWidth:innerWidth
      }));
      assert.equal(state.theme,theme.key);
      assert.equal(state.accent,theme.accent);
      assert.equal(state.label,theme.label);
      assert.equal(state.bannerHidden,theme.key==='wb');
      assert.equal(state.items,2,'Preview must not swap in invented marketplace goods');
      assert.ok(state.source && state.wbCard,'Same real WB dataset and links only');
      assert.equal(state.persisted,theme.key);
      const iconBg = await page.$eval('#marketplace-current-icon',x=>getComputedStyle(x).backgroundColor);
      if(theme.key==='ozon') assert.equal(iconBg,'rgb(0, 91, 255)');
      if(theme.key==='yandex') assert.equal(iconBg,'rgb(255, 214, 0)');
      if(theme.key==='mega') assert.equal(iconBg,'rgb(124, 54, 225)');
      assert.ok(state.pageWidth<=state.viewportWidth+2,'Desktop overflow: '+JSON.stringify(state));
      if(theme.key!=='wb') {
        const disclaimer=await page.$eval('#theme-preview-banner',e=>e.innerText);
        assert.ok(disclaimer.includes('взяты из Wildberries') && disclaimer.includes('не подключена'));
      }
      await page.screenshot({path:snapshotDir+'/theme-'+theme.key+'-desktop.png',fullPage:true});
      step('marketplace_'+theme.key+'_visual_preview_only');
      if(theme.key!=='wb') {
        await goto('/assistant/');
        assert.equal(await page.$eval('body',e=>e.dataset.previewMarketplace),theme.key,
          'Theme must persist across seller navigation');
        await goto('/');
      }
      if(theme.key!=='wb')await page.click('details.marketplace-picker > summary');
    }
    await page.click('details.marketplace-picker > summary');
    const external = await page.$$eval('.wb-quick-links a',links=>links.map(a=>a.href));
    assert.ok(external.some(x=>x==='https://www.wildberries.ru/'));
    assert.ok(external.some(x=>x==='https://seller.wildberries.ru/'));
    await page.screenshot({path:snapshotDir+'/05-marketplace-selector.png'});
    step('marketplace_theme_selector_and_wb_quick_links');
    await goto('/assistant/');
    await page.waitForSelector('[data-assistant-prompt]');
    await page.click('[data-assistant-prompt]');
    const promptValue=await page.$eval('#assistant-question',node=>node.value);
    assert.equal(promptValue,'Сколько товаров выше лимита?');
    const assistantWidth=await page.evaluate(()=>({
      width:window.innerWidth,pageWidth:document.documentElement.scrollWidth
    }));
    assert.ok(assistantWidth.pageWidth<=assistantWidth.width+2);
    await page.screenshot({path:snapshotDir+'/06-assistant-desktop.png',fullPage:true});
    step('assistant_responsive_layout_and_live_prompt_buttons');
    await goto('/billing/');
    await waitText('пробный период');
    const subscriptionText = await page.evaluate(()=>document.body.innerText);
    assert.ok(subscriptionText.toLowerCase().includes('standard'),'trial should have Standard capabilities');
    step('trial_granted_on_first_import');
    await goto('/settings/');
    await page.waitForSelector('form');
    step('seller_settings_access');
    await goto('/notifications/');
    await waitText('Важных событий пока нет.');
    step('routine_sync_does_not_spam_important_notifications');
    await page.screenshot({path:snapshotDir+'/03-notifications.png'});
    await goto('/history/');
    await waitText('Проверок: 1');
    step('successful_wb_check_is_recorded_in_history');
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
    await page.click('details.marketplace-picker > summary');
    const menuVisible=await page.$eval('details.marketplace-picker .marketplace-options',x=>{
      const r=x.getBoundingClientRect();
      const e=document.elementFromPoint(r.left+20,r.top+20);
      return r.width>200 && r.left>=0 && r.right<=window.innerWidth+2 && !!(e && x.contains(e));
    });
    assert.ok(menuVisible,'Marketplace selector must remain usable on small phones');
    step('mobile_marketplace_picker_visible_and_not_clipped');
    await page.click('[data-theme-preview="ozon"]');
    const mobileTheme = await page.evaluate(()=>({
      theme:document.body.dataset.previewMarketplace,
      width:innerWidth, pageWidth:document.documentElement.scrollWidth,
      bannerVisible:!document.querySelector('#theme-preview-banner').hidden
    }));
    assert.equal(mobileTheme.theme,'ozon');
    assert.ok(mobileTheme.bannerVisible);
    assert.ok(mobileTheme.pageWidth<=mobileTheme.width+2);
    await page.screenshot({path:snapshotDir+'/theme-ozon-mobile.png',fullPage:true});
    step('mobile_ozon_theme_preview_without_overflow');
    await goto('/assistant/');
    assert.equal(await page.$eval('body',e=>e.dataset.previewMarketplace),'ozon');
    await page.click('details.marketplace-picker > summary');
    await page.click('[data-theme-preview="wb"]');
    assert.equal(await page.$eval('body',e=>e.dataset.previewMarketplace),'wb');
    const mobileAssistant=await page.evaluate(()=>({
      width:window.innerWidth,pageWidth:document.documentElement.scrollWidth,
    }));
    assert.ok(mobileAssistant.pageWidth<=mobileAssistant.width+2,
      'Mobile assistant has horizontal overflow: '+JSON.stringify(mobileAssistant));
    await page.screenshot({path:snapshotDir+'/07-assistant-mobile.png',fullPage:true});
    step('assistant_mobile_no_horizontal_overflow');
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
