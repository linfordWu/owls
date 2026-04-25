import Router from '@koa/router'
import * as ctrl from '../../controllers/owls/weixin'

export const weixinRoutes = new Router()

weixinRoutes.get('/api/owls/weixin/qrcode', ctrl.getQrcode)
weixinRoutes.get('/api/owls/weixin/qrcode/status', ctrl.pollStatus)
weixinRoutes.post('/api/owls/weixin/save', ctrl.save)
