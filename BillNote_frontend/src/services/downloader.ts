import request from '@/utils/request.ts'

export const CONFIG_ADMIN_TOKEN_STORAGE_KEY = 'bilinote_config_admin_token'

export const getConfigAdminToken = (): string => {
  if (typeof window === 'undefined') {
    return ''
  }
  return window.localStorage.getItem(CONFIG_ADMIN_TOKEN_STORAGE_KEY) || ''
}

export const setConfigAdminToken = (token: string) => {
  if (typeof window === 'undefined') {
    return
  }
  const trimmedToken = token.trim()
  if (!trimmedToken) {
    window.localStorage.removeItem(CONFIG_ADMIN_TOKEN_STORAGE_KEY)
    return
  }
  window.localStorage.setItem(CONFIG_ADMIN_TOKEN_STORAGE_KEY, trimmedToken)
}

const getSensitiveHeaders = () => {
  const token = getConfigAdminToken()
  if (!token) {
    return {}
  }
  return { 'X-BiliNote-Admin-Token': token }
}

export const getDownloaderCookie = async id => {
  return await request.get('/get_downloader_cookie/' + id, {
    headers: id === 'bilibili' ? getSensitiveHeaders() : {},
  })
}

export const updateDownloaderCookie = async (data: { cookie: string; platform: any }) => {
  const isBilibili = String(data?.platform || '').toLowerCase() === 'bilibili'
  return await request.post('/update_downloader_cookie', data, {
    headers: isBilibili ? getSensitiveHeaders() : {},
  })
}

export const getBilibiliBbdownStatus = async () => {
  return await request.get('/bilibili_bbdown/status', { headers: getSensitiveHeaders() })
}

export const getBilibiliCookieStatus = async () => {
  return await request.get('/bilibili_cookie/status', { headers: getSensitiveHeaders() })
}

export const verifyBilibiliCookie = async () => {
  return await request.get('/bilibili_cookie/verify', { headers: getSensitiveHeaders() })
}

export const clearBilibiliCookie = async () => {
  return await request.delete('/bilibili_cookie', { headers: getSensitiveHeaders() })
}

export const startBilibiliBbdownLogin = async () => {
  return await request.post('/bilibili_bbdown/login/start', {}, { headers: getSensitiveHeaders() })
}

export const getBilibiliBbdownLoginStatus = async (sessionId?: string) => {
  const params = sessionId ? { session_id: sessionId } : undefined
  return await request.get('/bilibili_bbdown/login/status', {
    headers: getSensitiveHeaders(),
    params,
  })
}

export const cancelBilibiliBbdownLogin = async (sessionId: string) => {
  return await request.post(
    '/bilibili_bbdown/login/cancel',
    { session_id: sessionId },
    { headers: getSensitiveHeaders() }
  )
}

export const testBilibiliBbdownSubtitle = async (videoUrl: string) => {
  return await request.post(
    '/bilibili_bbdown/test',
    { video_url: videoUrl },
    { headers: getSensitiveHeaders() }
  )
}
