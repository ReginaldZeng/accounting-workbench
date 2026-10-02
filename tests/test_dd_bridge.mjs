import { getAuthCode } from '../01_Current_Deliverables/app/frontend/src/views/ddBridge.js'
import assert from 'node:assert/strict'
globalThis.window = { dd: { requestAuthCode({ success, clientId, corpId }) { assert.equal(this, window.dd); assert.equal(clientId, 'app'); assert.equal(corpId, 'corp'); success({ code: 'modern' }) } } }
assert.equal(await getAuthCode('corp', 'app'), 'modern')
window.dd = { runtime: { permission: { requestAuthCode({ onSuccess }) { assert.equal(this, window.dd.runtime.permission); onSuccess({ code: 'legacy' }) } } } }
assert.equal(await getAuthCode('corp'), 'legacy')
window.dd = { requestAuthCode({ fail }) { fail({ errorMessage: 'modern unavailable' }) }, runtime: { permission: { requestAuthCode: ({ onSuccess }) => onSuccess({ code: 'fallback' }) } } }
assert.equal(await getAuthCode('corp', 'app'), 'fallback')
console.log('DingTalk modern / legacy / fallback checks passed')
