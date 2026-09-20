import {createServer} from 'vite';
import assert from 'node:assert/strict';
import fs from 'node:fs';
const server=await createServer({server:{middlewareMode:true},appType:'custom'});
try{
 const React=await import('react');
 const {renderToStaticMarkup}=await import('react-dom/server');
 const {default:Workspace}=await server.ssrLoadModule('/src/views/LogisticsWorkspace.jsx');
 for(const entry of ['master','overview','receive','reconcile','ledger']){
  const html=renderToStaticMarkup(React.createElement(Workspace,{entry,cfg:{year:2026,period:7}}));
  assert(html.includes('entry='+entry));assert(html.includes('month=2026-07'));assert(html.includes('embedded=1'));
 }
 const app=fs.readFileSync('src/App.jsx','utf8');
 for(const old of ['LogisticsAccrual','LogisticsBasicData','LogisticsBillUpload','LogisticsRecon','LogisticsCost'])assert(!app.includes(old));
 assert.equal((app.match(/<LogisticsWorkspace /g)||[]).length,5);
 console.log('PASS: five portal routes use embedded new logistics; old components removed; selected period forwarded');
}finally{await server.close();}

