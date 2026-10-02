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
      const success = x => res(pick(x))
      const fail = e => rej(new Error(e?.errorMessage || e?.message || JSON.stringify(e || {})))
      const r = fn({ ...args, onSuccess: success, onFail: fail, success, fail })
      if (r && typeof r.then === 'function') r.then(x => res(pick(x)), rej)
    } catch (e) { rej(e) }
  })
}
export async function getAuthCode(corpId, clientId) {
  const dd = await withTimeout(loadDd(), 6000, '钉钉组件加载超时')
  if (!corpId) throw new Error('缺少企业编号，请联系财务')
  // 免登接口不需要 dd.config；不要让签名失败阻塞身份识别。
  if (clientId && typeof dd.requestAuthCode === 'function') {
    try { return await withTimeout(ddCall(dd.requestAuthCode.bind(dd), { corpId, clientId }, x => x?.code || ''), 6000, '钉钉免登超时') }
    catch { /* 老容器继续用旧版接口 */ }
  }
  if (typeof dd.ready === 'function') {
    try { await withTimeout(new Promise(resolve => dd.ready(resolve)), 2500, '钉钉组件尚未就绪') } catch { /* 仍尝试旧版桥接，错误由接口返回 */ }
  }
  const permission = dd.runtime?.permission
  if (!permission?.requestAuthCode) throw new Error('当前钉钉容器不支持免登，请从手机钉钉内打开')
  return withTimeout(ddCall(permission.requestAuthCode.bind(permission), { corpId }, x => x?.code || ''), 6000, '钉钉免登超时')
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
