const http = require('node:http');
const fs = require('node:fs/promises');
const path = require('node:path');
const assert = require('node:assert/strict');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const root = path.resolve(__dirname, '../..');
const buildDir = path.join(root, 'src/bosshunter/web/frontend/dist');
const artifactDir = path.join(root, 'output/playwright');
const job = {id:'fixture-job',source_platform:'boss',title:'合成测试岗位',company:'合成测试公司',salary:'10-15K',city:'测试城',experience:'不限',jd:'纯本地合成数据',score:80,score_reason:'fixture',greeting:'本地测试消息，不会对外发送',status:'approved',hr_name:'合成角色',hr_title:'测试',hr_active:'',company_size:'',company_industry:'',url:'',created_at:'2026-09-06 00:00:00'};
const base = {funnel:{},funnel_today:{},pending_confirmation:[],pending_greetings:[],send_errors:[],needs_resume:[],send_quota:{daily_limit:30,sent:0,remaining:30,exhausted:false},task:null,last_task:null};
let workbench, deliveredBodies, deliverStatus, deliverReply, pendingResponse, holdResponse=false;
const server = http.createServer(async(req,res)=>{
  const url = new URL(req.url, 'http://127.0.0.1');
  const json = (data,status=200)=>{res.writeHead(status,{'Content-Type':'application/json'});res.end(JSON.stringify(data));};
  if(url.pathname==='/api/workbench') return json(workbench);
  if(url.pathname==='/api/workbench/deliver'){
    let data='';for await(const chunk of req)data+=chunk;
    deliveredBodies.push(JSON.parse(data));
    if(deliverStatus===200)workbench={...base};
    if(holdResponse){pendingResponse=()=>json(deliverReply,deliverStatus);return;}
    return json(deliverReply,deliverStatus);
  }
  if(url.pathname==='/api/history/unresolved-replies/count')return json({count:0});
  if(url.pathname.startsWith('/api/'))return json({});
  try{
    const file=path.join(buildDir,url.pathname==='/'?'index.html':url.pathname);
    const type={'.html':'text/html','.js':'application/javascript','.css':'text/css'}[path.extname(file)]||'application/octet-stream';
    const data = await fs.readFile(file);
    res.writeHead(200,{'Content-Type':type});res.end(data);
  }catch(e){res.writeHead(404);res.end('missing');}
});
(async()=>{
 await fs.mkdir(artifactDir,{recursive:true});
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
 const origin=`http://127.0.0.1:${server.address().port}`;
 const browser=await chromium.launch({headless:true,channel:process.env.BROWSER_CHANNEL || 'chrome'});
 try{
  const context=await browser.newContext({viewport:{width:1440,height:1100}});
  await context.route('**/*',route=>new URL(route.request().url()).origin===origin?route.continue():route.abort());
  const page=await context.newPage(); const errors=[];page.on('pageerror',e=>errors.push(String(e)));
  workbench={...base,pending_greetings:[job]};deliveredBodies=[];deliverStatus=409;deliverReply={error:'合成测试：已有任务正在运行，请稍后重试'};
  await page.goto(origin);await page.getByRole('button',{name:'发送招呼语',exact:true}).waitFor();
  await fs.writeFile(path.join(artifactDir,'145-ready-before.txt'),await page.locator('body').innerText());
  await Promise.all([page.waitForResponse(r=>r.url().endsWith('/api/workbench/deliver')),page.getByRole('button',{name:'发送招呼语',exact:true}).click()]);
  await page.evaluate(()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r))));
  const readyText=await page.locator('body').innerText();
  const readyResult={scenario:'pending greeting rejected, no previous send errors',requests:deliveredBodies,errorResponse:deliverReply.error,errorVisible:readyText.includes(deliverReply.error)};
  assert.equal(deliveredBodies.length,1);assert.equal(readyResult.errorVisible,true);
  await fs.writeFile(path.join(artifactDir,'145-ready-after.txt'),readyText);
  await page.getByText(deliverReply.error,{exact:true}).scrollIntoViewIfNeeded();
  await page.screenshot({path:path.join(artifactDir,'145-ready-error-visible.png')});
  workbench={...base,send_errors:[{...job,status:'error',last_error:'合成测试失败'}]};deliveredBodies=[];deliverStatus=200;deliverReply={queued_count:1};holdResponse=true;
  await page.reload();const retry=page.getByRole('button',{name:'重新发送全部 1 个',exact:true});await retry.waitFor();
  await retry.click();await page.getByRole('button',{name:'正在重新发送...',exact:true}).first().waitFor();
  const disabled=await page.getByRole('button',{name:'正在重新发送...',exact:true}).first().isDisabled();
  while(!pendingResponse)await new Promise(resolve=>setTimeout(resolve,10));
  const responsePromise=page.waitForResponse(r=>r.url().endsWith('/api/workbench/deliver'));
  pendingResponse();await responsePromise;
  await page.getByRole('heading',{name:'发送失败待处理',exact:true}).waitFor({state:'hidden'});
  await page.evaluate(()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r))));
  const retryText=await page.locator('body').innerText();
  const retryResult={scenario:'retry accepted, refresh removes last error job',requests:deliveredBodies,disabledWhilePending:disabled,queueNoticeVisible:retryText.includes('已将 1 个岗位追加到当前发送队列。')};
  assert.equal(deliveredBodies.length,1);assert.equal(disabled,true);assert.equal(retryResult.queueNoticeVisible,true);
  await page.screenshot({path:path.join(artifactDir,'145-retry-notice-visible.png')});
  const results={readyResult,retryResult,pageErrors:errors,allData:'synthetic, local HTTP only'};
  await fs.writeFile(path.join(artifactDir,'145-results.json'),JSON.stringify(results,null,2));console.log(JSON.stringify(results,null,2));assert.equal(errors.length,0);
 }finally{await browser.close();await new Promise(resolve=>server.close(resolve));}
})().catch(e=>{console.error(e);process.exitCode=1;server.close();});
