// [Change Log]
// Date: 2026-08-05 | Author: Claude / c | Version: V2.173
// Description: Vite 配置。V2.173：dev 代理端口改为跟随本工作树的后端端口（环境变量 FW_PORT），
//              不再写死——原先写死 8099，而启动脚本一直起在 8000，dev 模式下 /api 代理其实是断的；
//              且多工作树并行时写死端口必然串台（A 的页面调到 B 的后端，看着还挺正常）。
//              dev 服务器端口 ＝ 后端端口 + 1000，一一对应好认。
//              用 dev_frontend.bat 启动会自动算好 FW_PORT；手工 npm run dev 则默认 8000。
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

const apiPort = Number(process.env.FW_PORT) || 8000

export default defineConfig({
  base: './',
  plugins: [react()],
  build: { outDir: '../backend/static', emptyOutDir: true },
  server: { port: apiPort + 1000, proxy: { '/api': `http://localhost:${apiPort}` } }
})
