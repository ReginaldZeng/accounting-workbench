import React from 'react'
// Same-origin module: existing portal permissions and server authentication both apply.
export default function LogisticsWorkspace({entry,cfg}) {
 const month = String(cfg.year || 2026) + '-' + String(cfg.period || 8).padStart(2,'0')
 const src = '/logistics/?embedded=1&entry=' + encodeURIComponent(entry) + '&month=' + encodeURIComponent(month)
 return <iframe key={src} title="新版物流核对工作台" src={src} style={{display:'block',border:0,width:'100%',height:'100vh',background:'#f6f7fc'}} />
}
