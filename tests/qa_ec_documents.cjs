// Browser fixtures only. No production login, upload, or Kingdee mutation.
const {chromium}=require('C:/Users/94899/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const assert=require('node:assert/strict');
const OUT='C:/Users/94899/.codex/visualizations/2026/09/09/01a08653-5228-71d3-b61a-9060735b517c';
const shop='星期零STARFIELD 天猫官旗店',no='1234567890123456789';
const account={id:'test-account',kind:'alipay',name:'验收支付宝',suffix:'4680',shops:[shop],rows:536,balance:100,as_of:'2026-08-31',balance_basis:'测试数据'};
const match={order_no:no,status:'amount_equal',message:'订单累计交易收款与平台确认打款金额一致',evidence:{amount_check:{expected:'10',received:'10',difference:'0',basis:'跨期累计交易收款；费用及退款单独核对，非银行到账'},documents:[{kind:'order',document_no:no,period:'2026-07',filename:'七月来源.xlsx',sheet:'Sheet1',row:2}]}};
const flow={id:1,account_id:account.id,account_name:account.name,ts:'2026-08-02 08:00:00',serial:'123456789012345678901',txn:'987654321098765432101',order_no:no,order_link:{order_no:no},desc:'0010001|交易收款',btype:'在线支付',income:10,outgo:0,balance:100,bucket:'receipt',flags:[],document_match:match,raw:{支付宝流水号:'123456789012345678901',业务描述:'0010001|交易收款',新增字段:'保留原始列'}};
(async()=>{
 const browser=await chromium.launch({headless:true,channel:'msedge'}),page=await browser.newPage({viewport:{width:1600,height:1000}}),errors=[],requests=[];
 page.on('pageerror',e=>errors.push(e.message));
 await page.route('**/api/**',async route=>{
  const u=new URL(route.request().url());requests.push(u.pathname+u.search);let b={ok:true};
  if(u.pathname==='/api/me')b={user:{name:'界面测试账号',role:'admin',perms:{}}};
  else if(u.pathname==='/api/config')b={source:'sample',year:2026,period:8};
  else if(u.pathname==='/api/nav-modules')b={state:{ecommonth:{'可进入':true,status:'待验收'}},modules:[{key:'ecommonth',label:'电商工作台',sec:'ar',cap:'enter:ecomsettle'}],sections:[{key:'ar',label:'应收模块'}],posts:[]};
  else if(u.pathname.endsWith('/workbench/shops'))b={ok:true,shops:[{id:shop,name:shop,platform:'天猫'}]};
  else if(u.pathname.endsWith('/workbench/overview'))b={ok:true,shops:[{id:shop,name:shop,platform:'天猫',available:true}],totals:{orders:1},coverage:{available:1,total:1}};
  else if(u.pathname.endsWith('/workbench/sources'))b={ok:true,sources:[{kind:'order',label:'平台订单',available:true,rows:575,files:['七月来源.xlsx']}],rule:{recognition:'shipment'},kingdee:{available:false}};
  else if(u.pathname==='/api/ec/flows/accounts')b={ok:true,accounts:[account],shops:[{id:shop,name:shop}]};
  else if(u.pathname==='/api/ec/flows')b={ok:true,rows:u.searchParams.get('match_status')==='missing_document'?[]:[flow],total:1,income:10,outgo:0,flagged:0,page:1,pages:1,buckets:{receipt:536}};
  else if(u.pathname==='/api/ec/flows/detail/1')b={ok:true,row:flow,sources:[{filename:'八月流水.zip',sheet:'账务明细',row_number:26}]};
  else if(u.pathname==='/api/ec/documents')b={ok:true,files:[{id:1,filename:'七月来源.xlsx',kinds:'order',row_count:575,ts:'2026-09-12 12:00:00'}],coverage:[{kind:'order',period:'2026-07',rows:575}]};
  else if(u.pathname==='/api/ec/documents/reconcile')b={ok:true,job_id:'test-job'};
  else if(u.pathname==='/api/ec/documents/jobs/test-job')b={ok:true,status:'complete',result:{updated:536}};
  await route.fulfill({contentType:'application/json',body:JSON.stringify(b)});
 });
 await page.goto('http://127.0.0.1:8783/#/ecommonth');
 await page.getByRole('heading',{name:'电商对账工作台',exact:true}).waitFor();
 await page.getByRole('button',{name:'数据准备',exact:true}).click();
 await page.getByRole('heading',{name:'跨期单据资料库',exact:true}).waitFor();
 await page.getByText('已保存 1 个来源文件',{exact:false}).click();
 await page.getByRole('button',{name:'重新核对流水',exact:true}).click();
 await page.getByText('更新 536 笔流水核对结果',{exact:false}).waitFor();
 await page.getByRole('heading',{name:'跨期单据资料库',exact:true}).scrollIntoViewIfNeeded();
 await page.screenshot({path:OUT+'/documents-registry-desktop.png'});
 await page.getByRole('button',{name:'账户流水',exact:true}).click();
 await page.getByLabel('核对结果',{exact:true}).selectOption('missing_document');
 await page.waitForResponse(r=>r.url().includes('match_status=missing_document'));
 await page.getByLabel('核对结果',{exact:true}).selectOption('amount_equal');
 await page.getByRole('button',{name:no,exact:true}).waitFor();
 await page.screenshot({path:OUT+'/documents-match-desktop.png'});
 await page.getByRole('button',{name:no,exact:true}).click();
 await page.getByRole('heading',{name:'跨期单据核对 · 金额一致'}).waitFor();
 await page.getByText('七月来源.xlsx',{exact:false}).waitFor();
 assert(await page.getByText('保留原始列',{exact:true}).isVisible());
 await page.screenshot({path:OUT+'/documents-match-drawer.png'});
 await page.keyboard.press('Escape');
 for(const width of [720,390]){
   await page.setViewportSize({width,height:1000});await page.screenshot({path:OUT+`/documents-match-${width}.png`});
   assert(!(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1)),`body overflow at ${width}`);
 }
 assert.deepEqual(errors,[]);
 assert(requests.some(p=>p.includes('match_status=missing_document')));
 assert(!requests.some(p=>/Save|Push|Submit|Audit/.test(p)));
 console.log(JSON.stringify({ok:true,errors,filter:true,sourceEvidence:true,polling:true,readOnlyKingdee:true}));
 await browser.close();
})().catch(e=>{console.error(e);process.exit(1)});
