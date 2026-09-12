// Mocked UI contracts; never uploads financial data or calls Kingdee.
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const assert=require('node:assert/strict');
const shop='测试天猫店',version='a'.repeat(32),requests=[],errors=[];
const row=n=>({order_no:String(1000000000000000000n+BigInt(n)),business_type:'normal',business_label:'正常销售',status:'已发货',order_amount:100,paid:90,refund:0,fees:1,net_receipt:89,destination:'支付宝',ar_documents:[],issues:[],items:[],events:[]});
(async()=>{
 const browser=await chromium.launch({headless:true,channel:'msedge'}),page=await browser.newPage({viewport:{width:1600,height:1000}});
 let delay=false,stale=false,phase='ready';
 page.on('pageerror',e=>errors.push(e.message));
 await page.route('**/api/**',async route=>{
  const u=new URL(route.request().url());let b={ok:true};requests.push(u.pathname+u.search);
  if(u.pathname==='/api/me')b={user:{name:'测试账号',role:'admin',perms:{}}};
  else if(u.pathname==='/api/config')b={source:'sample',year:2026,period:8};
  else if(u.pathname==='/api/nav-modules')b={state:{ecommonth:{'可进入':true,status:'待验收'}},modules:[{key:'ecommonth',label:'电商工作台',sec:'ar',cap:'enter:ecomsettle'}],sections:[{key:'ar',label:'应收模块'}],posts:[]};
  else if(u.pathname.endsWith('/workbench/shops'))b={shops:[{id:shop,name:shop,platform:'天猫'}]};
  else if(u.pathname.endsWith('/workbench/overview'))b={shops:[{id:shop,name:shop,platform:'天猫',available:true}],totals:{orders:65},coverage:{available:1,total:1}};
  else if(u.pathname.endsWith('/workbench/orders')){
   if(delay)await new Promise(r=>setTimeout(r,900));
   const filtered=!!u.searchParams.get('q'),number=filtered?1:Number(u.searchParams.get('page')||1);
   b={ok:true,page:number,pages:filtered?1:3,total:filtered?1:65,rows:filtered?[row(2)]:Array.from({length:number===3?5:30},(_,i)=>row((number-1)*30+i)),result:{status:phase==='first'?'building':phase,build_id:version,stored_rows:65,built_at:'2026-09-12 12:00:00',stale,error:phase==='error'?'测试构建失败，旧结果保留':''}};
   if(phase==='first'){b.rows=[];b.total=null;b.pages=null;b.result.build_id='';b.result.stored_rows=null;}
  }else if(u.pathname.endsWith('/workbench/results/refresh')){phase='ready';stale=false;
  }else if(u.pathname.endsWith('/workbench/order'))b={order:row(30)};
  else if(u.pathname.endsWith('/flows/accounts'))b={accounts:[]};
  await route.fulfill({contentType:'application/json',body:JSON.stringify(b)});
 });
 await page.goto(process.env.QA_URL||'http://127.0.0.1:8783/#/ecommonth');
 await page.getByRole('button',{name:'收入确认',exact:true}).click();
 await page.getByRole('button',{name:row(0).order_no,exact:true}).waitFor();
 delay=true;await page.getByRole('button',{name:'下一页',exact:true}).click();
 await page.getByText('正在读取第 2 页…',{exact:true}).waitFor();
 assert.equal(await page.locator('.ew-order-table tbody tr').count(),30);
 assert((await page.locator('.ew-pagination').innerText()).includes('1 / 3'));
 await page.getByRole('button',{name:row(30).order_no,exact:true}).waitFor();
 assert((await page.locator('.ew-pagination').innerText()).includes('2 / 3'));
 assert(requests.some(s=>s.includes('page=2')&&s.includes('version='+version)));
 await page.getByRole('button',{name:row(30).order_no,exact:true}).click();
 await page.getByRole('dialog').waitFor();assert(requests.some(s=>s.includes('/workbench/order?')&&s.includes('version='+version)));
 await page.keyboard.press('Escape');
 const before=requests.filter(s=>s.includes('/workbench/orders?')).length;
 await page.getByRole('textbox',{name:'模糊搜索',exact:true}).fill(row(2).order_no);
 await page.getByRole('button',{name:'查询',exact:true}).click();
 await page.getByRole('button',{name:row(2).order_no,exact:true}).waitFor();
 assert.equal(requests.filter(s=>s.includes('/workbench/orders?')).length-before,1);
 assert((await page.locator('.ew-pagination').innerText()).includes('1 / 1'));
 stale=true;delay=false;await page.getByRole('combobox',{name:'支付渠道',exact:true}).selectOption('alipay');
 await page.getByRole('button',{name:'查看新版',exact:true}).waitFor();
 await page.getByRole('button',{name:'查看新版',exact:true}).click();
 stale=false;phase='first';await page.getByRole('button',{name:'刷新数据',exact:true}).click();
 await page.getByText('正在后台首次生成核算结果，可离开页面，完成后自动展示。',{exact:true}).waitFor();
 assert((await page.locator('.ew-pagination').innerText()).includes('1 / —'));
 phase='ready';await page.getByRole('button',{name:row(2).order_no,exact:true}).waitFor();
 phase='error';stale=true;await page.getByRole('button',{name:'刷新数据',exact:true}).click();
 await page.getByRole('button',{name:'重试生成',exact:true}).waitFor();
 assert.equal(await page.getByRole('button',{name:row(2).order_no,exact:true}).count(),1);
 assert.equal(await page.getByRole('button',{name:'查看新版',exact:true}).count(),0);
 await page.getByRole('button',{name:'重试生成',exact:true}).click();
 await page.getByText('已保存 65 笔',{exact:false}).waitFor();
 if(process.env.QA_OUT)await page.screenshot({path:process.env.QA_OUT+'/orders-paging-desktop.png'});
 for(const width of [720,390]){await page.setViewportSize({width,height:1000});assert(!(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1)),`body overflow at ${width}`);if(process.env.QA_OUT)await page.screenshot({path:process.env.QA_OUT+`/orders-paging-${width}.png`});}
 assert.deepEqual(errors,[]);await browser.close();console.log(JSON.stringify({ok:true,retained_rows_while_loading:true,pinned_paging_and_drawer:true,filter_single_request:true,desktop_mobile:true,console_errors:errors.length}));
})().catch(e=>{console.error(e);process.exit(1)});
