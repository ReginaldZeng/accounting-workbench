// 钉钉 JSAPI 小工具（手机当相机 InvPhone、申请人自助登记 InvSelf 共用）：加载 SDK、回调/Promise 两接、免登码、dd.config 鉴权

export const DD_JS = 'https://g.alicdn.com/dingding/dingtalk-jsapi/3.0.25/dingtalk.open.js'
export const inDingTalk = () => /DingTalk/i.test(navigator.userAgent || '')
export function withTimeout(p, ms, text) {
  let t = null
  return Promise.race([p, new Promise((_, rej) => { t = setTimeout(() => rej(new Error(text || '超时')), ms) })]).finally(() => clearTimeout(t))
}
let _ddP = null
export function loadDd() {
  if (window.dd) return Promise.resolve(window.dd)
  if (_ddP) return _ddP
  _ddP = new Promise((res, rej) => {
    const s = document.createElement('script')
    s.src = DD_JS
    s.async = true
    s.onload = () => (window.dd ? res(window.dd) : rej(new Error('钉钉组件没加载上')))
    s.onerror = () => { _ddP = null; rej(new Error('钉钉组件没加载上')) }
    document.head.appendChild(s)
  })
  return _ddP
}
// 钉钉 JSAPI 新旧版本有的走回调、有的回 Promise，两种都接
export function ddCall(fn, args, pick) {
  return new Promise((res, rej) => {
    try {
      const r = fn({ ...args, onSuccess: x => res(pick(x)), onFail: e => rej(e) })
      if (r && typeof r.then === 'function') r.then(x => res(pick(x)), rej)
    } catch (e) { rej(e) }
  })
}
export async function getAuthCode(corpId) {
  const dd = await withTimeout(loadDd(), 6000, '钉钉组件加载超时')
  const ask = () => ddCall(dd.runtime.permission.requestAuthCode, { corpId }, x => (x && x.code) || '')
  const ready = typeof dd.ready === 'function' ? new Promise(r => dd.ready(r)) : Promise.resolve()
  return withTimeout(ready.then(ask), 4000, '钉钉免登超时')
}
// dd.config 鉴权：getCfg(页面地址不含#) → 后端签名 {ok, agentId, corpId, timeStamp, nonceStr, signature, msg}；
// 成功回签名参数（含 corpId），失败抛带原因的错误
export async function ddConfig(getCfg, jsApiList) {
  const dd = await withTimeout(loadDd(), 6000, '钉钉组件加载超时')
  const cfg = (await getCfg(String(location.href).split('#')[0])) || {}
  if (!cfg.ok) throw new Error(cfg.msg || '钉钉鉴权没通过')
  if (typeof dd.config !== 'function') return cfg
  await withTimeout(new Promise((res, rej) => {
    if (typeof dd.error === 'function') dd.error(e => rej(new Error('钉钉鉴权没通过：' + ((e && (e.errorMessage || e.message)) || JSON.stringify(e || {})))))
    dd.config({ agentId: cfg.agentId, corpId: cfg.corpId, timeStamp: cfg.timeStamp, nonceStr: cfg.nonceStr,
      signature: cfg.signature, type: 0, jsApiList })
    if (typeof dd.ready === 'function') dd.ready(() => res(true)); else res(true)
  }), 6000, '钉钉鉴权超时')
  return cfg
}
