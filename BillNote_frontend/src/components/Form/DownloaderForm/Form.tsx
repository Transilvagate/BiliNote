import { useForm } from 'react-hook-form'
import { z } from 'zod'
import { zodResolver } from '@hookform/resolvers/zod'
import {
  Form,
  FormField,
  FormItem,
  FormLabel,
  FormControl,
  FormMessage,
} from '@/components/ui/form'
import { Textarea } from '@/components/ui/textarea.tsx'
import { Input } from '@/components/ui/input'
import { Button } from '@/components/ui/button'
import { useCallback, useEffect, useRef, useState } from 'react'
import toast from 'react-hot-toast'
import {
  cancelBilibiliBbdownLogin,
  clearBilibiliCookie,
  getBilibiliBbdownLoginStatus,
  getBilibiliBbdownStatus,
  getBilibiliCookieStatus,
  getConfigAdminToken,
  getDownloaderCookie,
  startBilibiliBbdownLogin,
  setConfigAdminToken,
  testBilibiliBbdownSubtitle,
  updateDownloaderCookie,
  verifyBilibiliCookie,
} from '@/services/downloader'
import { useParams } from 'react-router-dom'
import { videoPlatforms } from '@/constant/note.ts'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'

interface BilibiliCookieStatus {
  exists: boolean
  cookies_file?: string
  updated_at?: string | null
  source?: string
}

interface BilibiliBbdownStatus {
  installed: boolean
  version?: string | null
  bin_path?: string
  work_dir?: string
  cookies_file?: string
  cookie_exists?: boolean
  cookie_updated_at?: string | null
  login_command?: string
  test_command_template?: string
}

interface BilibiliCookieVerifyResult {
  valid: boolean
  checked_at?: string | null
  message?: string
  uname?: string | null
  level?: number | null
  error?: string | null
  bili_code?: number | null
}

interface BilibiliBbdownLoginResult {
  session_id?: string
  status?: string
  message?: string
  qrcode_base64?: string | null
  cookie_saved?: boolean
  logs?: string[]
  error?: string | null
}

interface BilibiliBbdownTestResult {
  success: boolean
  reason_code?: string | null
  message?: string
  subtitle_count?: number
  subtitle_files?: string[]
  stdout_tail?: string[]
  stderr_tail?: string[]
}

const cookieSourceTextMap: Record<string, string> = {
  cookies_file: '本地文件',
  legacy_migrated: '已从旧配置迁移',
  legacy_invalid: '旧配置无效',
  manual_or_qr: '手动/扫码写入',
  missing: '未保存',
  cleared: '已清除',
}

const bbdownLoginStatusLabelMap: Record<string, string> = {
  STARTING: '启动中',
  WAITING_SCAN: '等待扫码',
  SCANNED: '已扫码待确认',
  CONFIRMED: '登录成功',
  EXPIRED: '二维码过期',
  FAILED: '登录失败',
  CANCELLED: '已取消',
  IDLE: '空闲',
}

const CookieSchema = z.object({
  cookie: z.string().min(10, '请填写有效 Cookie'),
})
type CookieFormValues = z.infer<typeof CookieSchema>

const formatDateTime = (value?: string | null) => {
  if (!value) {
    return '-'
  }
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) {
    return value
  }
  return date.toLocaleString()
}

const copyText = async (text?: string) => {
  if (!text) {
    return
  }
  try {
    await navigator.clipboard.writeText(text)
    toast.success('命令已复制')
  } catch {
    toast.error('复制失败，请手动复制')
  }
}

const DownloaderForm = () => {
  const form = useForm<CookieFormValues>({
    resolver: zodResolver(CookieSchema),
    defaultValues: { cookie: '' },
  })
  const { id } = useParams()
  const isBilibili = id === 'bilibili'

  const [loading, setLoading] = useState(true)
  const [statusLoading, setStatusLoading] = useState(false)
  const [cookieVerifyLoading, setCookieVerifyLoading] = useState(false)
  const [loginActionLoading, setLoginActionLoading] = useState(false)
  const [bbdownTestLoading, setBbdownTestLoading] = useState(false)
  const [cookieStatus, setCookieStatus] = useState<BilibiliCookieStatus | null>(null)
  const [cookieVerifyResult, setCookieVerifyResult] = useState<BilibiliCookieVerifyResult | null>(null)
  const [bbdownStatus, setBbdownStatus] = useState<BilibiliBbdownStatus | null>(null)
  const [loginDialogOpen, setLoginDialogOpen] = useState(false)
  const [bbdownLoginResult, setBbdownLoginResult] = useState<BilibiliBbdownLoginResult | null>(null)
  const [bbdownTestVideoUrl, setBbdownTestVideoUrl] = useState('')
  const [bbdownTestResult, setBbdownTestResult] = useState<BilibiliBbdownTestResult | null>(null)
  const [adminToken, setAdminToken] = useState('')
  const loginPollTimerRef = useRef<number | null>(null)

  const refreshBilibiliStatus = useCallback(
    async (silent = false) => {
      if (!isBilibili) {
        return
      }
      setStatusLoading(true)
      try {
        const [cookieRes, bbdownRes] = await Promise.all([
          getBilibiliCookieStatus(),
          getBilibiliBbdownStatus(),
        ])
        setCookieStatus({
          exists: Boolean(cookieRes?.exists),
          cookies_file: cookieRes?.cookies_file,
          updated_at: cookieRes?.updated_at,
          source: cookieRes?.source,
        })
        setBbdownStatus({
          installed: Boolean(bbdownRes?.installed),
          version: bbdownRes?.version,
          bin_path: bbdownRes?.bin_path,
          work_dir: bbdownRes?.work_dir,
          cookies_file: bbdownRes?.cookies_file,
          cookie_exists: bbdownRes?.cookie_exists,
          cookie_updated_at: bbdownRes?.cookie_updated_at,
          login_command: bbdownRes?.login_command,
          test_command_template: bbdownRes?.test_command_template,
        })
      } catch {
        setCookieStatus(null)
        setBbdownStatus(null)
        if (!silent) {
          toast.error('获取 B站 BBDown 状态失败')
        }
      } finally {
        setStatusLoading(false)
      }
    },
    [isBilibili]
  )

  const stopLoginPolling = useCallback(() => {
    if (loginPollTimerRef.current) {
      window.clearInterval(loginPollTimerRef.current)
      loginPollTimerRef.current = null
    }
  }, [])

  const pollLoginStatus = useCallback(
    async (sessionId?: string) => {
      if (!isBilibili) {
        return
      }
      try {
        const statusRes = await getBilibiliBbdownLoginStatus(sessionId)
        setBbdownLoginResult(statusRes)
        const statusKey = String(statusRes?.status || '').toUpperCase()
        if (['CONFIRMED', 'FAILED', 'EXPIRED', 'CANCELLED', 'IDLE'].includes(statusKey)) {
          stopLoginPolling()
          if (statusKey === 'CONFIRMED') {
            toast.success('BBDown 登录成功，Cookie 已同步')
            await refreshBilibiliStatus(true)
            const verifyRes = await verifyBilibiliCookie()
            setCookieVerifyResult(verifyRes)
          }
        }
      } catch {
        stopLoginPolling()
      }
    },
    [isBilibili, refreshBilibiliStatus, stopLoginPolling]
  )

  const startLoginPolling = useCallback(
    (sessionId?: string) => {
      stopLoginPolling()
      if (!sessionId) {
        return
      }
      loginPollTimerRef.current = window.setInterval(() => {
        void pollLoginStatus(sessionId)
      }, 2000)
      void pollLoginStatus(sessionId)
    },
    [pollLoginStatus, stopLoginPolling]
  )

  const handleStartBbdownLogin = async () => {
    if (!isBilibili) {
      return
    }
    setLoginActionLoading(true)
    try {
      const loginRes = await startBilibiliBbdownLogin()
      setBbdownLoginResult(loginRes)
      setLoginDialogOpen(true)
      startLoginPolling(loginRes?.session_id)
    } catch {
      toast.error('启动 BBDown 登录失败')
    } finally {
      setLoginActionLoading(false)
    }
  }

  const handleCloseLoginDialog = async (open: boolean) => {
    setLoginDialogOpen(open)
    if (open) {
      return
    }
    const status = String(bbdownLoginResult?.status || '').toUpperCase()
    const sessionId = bbdownLoginResult?.session_id
    stopLoginPolling()
    if (sessionId && ['STARTING', 'WAITING_SCAN', 'SCANNED'].includes(status)) {
      try {
        await cancelBilibiliBbdownLogin(sessionId)
      } catch {
        // ignore cancel failure on dialog close
      }
    }
  }

  const handleVerifyCookie = async () => {
    if (!isBilibili) {
      return
    }
    setCookieVerifyLoading(true)
    try {
      const verifyRes = await verifyBilibiliCookie()
      setCookieVerifyResult(verifyRes)
      if (verifyRes?.valid) {
        toast.success('Cookie 检测通过')
      } else {
        toast.error(verifyRes?.message || 'Cookie 检测失败')
      }
    } catch {
      toast.error('Cookie 检测失败')
    } finally {
      setCookieVerifyLoading(false)
    }
  }

  const handleBbdownSubtitleTest = async () => {
    if (!bbdownTestVideoUrl.trim()) {
      toast.error('请先输入测试视频链接')
      return
    }
    setBbdownTestLoading(true)
    try {
      const result = await testBilibiliBbdownSubtitle(bbdownTestVideoUrl.trim())
      setBbdownTestResult(result)
      if (result?.success) {
        toast.success('BBDown 字幕测试成功')
      } else {
        toast.error(result?.message || 'BBDown 字幕测试失败')
      }
    } catch {
      toast.error('BBDown 字幕测试失败')
    } finally {
      setBbdownTestLoading(false)
    }
  }

  const handleSaveAdminToken = async () => {
    setConfigAdminToken(adminToken)
    if (adminToken.trim()) {
      toast.success('管理员令牌已保存到本地浏览器')
    } else {
      toast.success('管理员令牌已清除')
    }
    if (isBilibili) {
      await refreshBilibiliStatus(false)
    }
  }

  const handleClearBilibiliCookie = async () => {
    if (!window.confirm('确定要清除本地 B站 Cookie 吗？')) {
      return
    }

    try {
      const res = await clearBilibiliCookie()
      await refreshBilibiliStatus(true)
      if (res?.cleared) {
        toast.success('已清除本地 B站 Cookie')
      } else {
        toast.success('本地未找到可清除的 B站 Cookie')
      }
    } catch {
      toast.error('清除 B站 Cookie 失败')
    }
  }

  useEffect(() => {
    setAdminToken(getConfigAdminToken())
  }, [])

  useEffect(() => {
    const loadCookie = async () => {
      setLoading(true)
      try {
        if (isBilibili) {
          await refreshBilibiliStatus(true)
          setCookieVerifyResult(null)
          setBbdownTestResult(null)
        } else {
          const res = await getDownloaderCookie(id)
          const cookie = res?.cookie || ''
          form.reset({ cookie })
          setCookieStatus(null)
          setCookieVerifyResult(null)
          setBbdownStatus(null)
          setBbdownLoginResult(null)
          setBbdownTestResult(null)
        }
      } catch {
        form.reset({ cookie: '' })
        if (!isBilibili) {
          toast.error('加载 Cookie 失败')
        }
      } finally {
        setLoading(false)
      }
    }

    if (id) {
      void loadCookie()
    }
  }, [form, id, isBilibili, refreshBilibiliStatus])

  useEffect(() => {
    return () => {
      stopLoginPolling()
    }
  }, [stopLoginPolling])

  const onSubmit = async (values: CookieFormValues) => {
    try {
      await updateDownloaderCookie({
        platform: id,
        cookie: String(values.cookie),
      })
      toast.success('保存成功')
    } catch {
      toast.error('保存失败')
    }
  }

  if (loading) return <div className="p-4">加载中...</div>

  if (isBilibili) {
    const loginStatusKey = String(bbdownLoginResult?.status || 'IDLE').toUpperCase()
    const loginStatusLabel =
      bbdownLoginStatusLabelMap[loginStatusKey] || bbdownLoginResult?.status || '未知状态'

    return (
      <div className="mx-auto max-w-3xl space-y-4 p-4 pb-10">
        <div className="text-lg font-bold">设置哔哩哔哩下载器（BBDown）</div>

        <div className="rounded-md border p-3 text-sm">
          <div className="font-medium">管理员令牌（可选）</div>
          <div className="mt-1 text-xs text-muted-foreground">
            仅保存在当前浏览器本地（localStorage），用于访问敏感配置接口。
          </div>
          <div className="mt-2 flex flex-col items-stretch gap-2 sm:flex-row sm:items-center">
            <Input
              type="password"
              value={adminToken}
              onChange={event => setAdminToken(event.target.value)}
              placeholder="输入 CONFIG_ADMIN_TOKEN（可留空）"
            />
            <Button type="button" variant="outline" onClick={() => void handleSaveAdminToken()}>
              保存令牌
            </Button>
          </div>
        </div>

        <div className="rounded-md border p-3 text-sm">
          <div className="font-medium">BBDown 状态</div>
          {statusLoading ? (
            <div className="mt-2">状态加载中...</div>
          ) : (
            <div className="mt-2 flex flex-col gap-1 text-muted-foreground">
              <span>安装状态：{bbdownStatus?.installed ? '已安装' : '未安装'}</span>
              <span>版本：{bbdownStatus?.version || '-'}</span>
              <span>可执行文件：{bbdownStatus?.bin_path || '-'}</span>
              <span>工作目录：{bbdownStatus?.work_dir || '-'}</span>
            </div>
          )}
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <Button
              type="button"
              variant="outline"
              onClick={() => void refreshBilibiliStatus(false)}
              disabled={statusLoading}
            >
              刷新状态
            </Button>
            <Button
              type="button"
              onClick={() => void handleStartBbdownLogin()}
              disabled={loginActionLoading || !bbdownStatus?.installed}
            >
              {loginActionLoading ? '启动中...' : '网页扫码登录'}
            </Button>
          </div>
        </div>

        <div className="rounded-md border p-3 text-sm">
          <div className="font-medium">B站 Cookie 文件状态</div>
          {statusLoading ? (
            <div className="mt-2">状态加载中...</div>
          ) : (
            <div className="mt-2 flex flex-col gap-1 text-muted-foreground">
              <span>状态：{cookieStatus?.exists ? '已保存' : '未保存'}</span>
              <span>更新时间：{formatDateTime(cookieStatus?.updated_at)}</span>
              <span>来源：{cookieSourceTextMap[cookieStatus?.source || ''] || cookieStatus?.source || '-'}</span>
              <span className="break-all">文件：{cookieStatus?.cookies_file || bbdownStatus?.cookies_file || '-'}</span>
            </div>
          )}
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <Button
              type="button"
              variant="outline"
              onClick={() => void handleVerifyCookie()}
              disabled={cookieVerifyLoading}
            >
              {cookieVerifyLoading ? '检测中...' : '检测 Cookie'}
            </Button>
            <Button type="button" variant="destructive" onClick={() => void handleClearBilibiliCookie()}>
              清除 Cookie
            </Button>
          </div>
          {cookieVerifyResult && (
            <div className="mt-3 rounded bg-muted p-2 text-xs text-muted-foreground">
              <div>检测结果：{cookieVerifyResult.valid ? '有效' : '无效'}</div>
              <div>提示：{cookieVerifyResult.message || '-'}</div>
              <div>账号：{cookieVerifyResult.uname || '-'}</div>
              <div>等级：{cookieVerifyResult.level ?? '-'}</div>
              <div>检测时间：{formatDateTime(cookieVerifyResult.checked_at)}</div>
            </div>
          )}
        </div>

        <div className="rounded-md border p-3 text-sm">
          <div className="font-medium">BBDown 字幕能力测试（网页端）</div>
          <div className="mt-2 flex flex-col items-stretch gap-2 sm:flex-row sm:items-center">
            <Input
              value={bbdownTestVideoUrl}
              onChange={event => setBbdownTestVideoUrl(event.target.value)}
              placeholder="输入一个 B站视频链接用于测试字幕下载"
            />
            <Button type="button" onClick={() => void handleBbdownSubtitleTest()} disabled={bbdownTestLoading}>
              {bbdownTestLoading ? '测试中...' : '开始测试'}
            </Button>
          </div>
          {bbdownTestResult && (
            <div className="mt-3 rounded bg-muted p-2 text-xs text-muted-foreground">
              <div>测试结果：{bbdownTestResult.success ? '成功' : '失败'}</div>
              <div>信息：{bbdownTestResult.message || '-'}</div>
              <div>原因码：{bbdownTestResult.reason_code || '-'}</div>
              <div>字幕文件数：{bbdownTestResult.subtitle_count ?? 0}</div>
              {bbdownTestResult.subtitle_files?.length ? (
                <div className="break-all">字幕文件：{bbdownTestResult.subtitle_files.join(', ')}</div>
              ) : null}
            </div>
          )}
        </div>

        <div className="rounded-md border p-3 text-sm">
          <div className="font-medium">命令行备用（可选）</div>
          <div className="mt-2 rounded bg-muted p-2 font-mono text-xs break-all">
            {bbdownStatus?.login_command || 'docker compose exec -it backend BBDown login'}
          </div>
          <div className="mt-2 rounded bg-muted p-2 font-mono text-xs break-all">
            {bbdownStatus?.test_command_template ||
              'docker compose exec -it backend BBDown <B站URL> --sub-only --skip-ai false --work-dir /app/data/bbdown'}
          </div>
          <div className="mt-2 flex flex-col items-stretch gap-2 sm:flex-row sm:items-center">
            <Button
              type="button"
              variant="outline"
              onClick={() => void copyText(bbdownStatus?.login_command || 'docker compose exec -it backend BBDown login')}
            >
              复制登录命令
            </Button>
            <Button type="button" variant="outline" onClick={() => void copyText(bbdownStatus?.test_command_template)}>
              复制测试命令
            </Button>
          </div>
        </div>

        <Dialog open={loginDialogOpen} onOpenChange={open => void handleCloseLoginDialog(open)}>
          <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-md">
            <DialogHeader>
              <DialogTitle>BBDown 网页扫码登录</DialogTitle>
              <DialogDescription>
                使用哔哩哔哩 App 扫描下方二维码，登录结果将自动同步到本地 Cookie 文件。
              </DialogDescription>
            </DialogHeader>
            <div className="flex flex-col items-center gap-3 text-center">
              {bbdownLoginResult?.qrcode_base64 ? (
                <img
                  src={`data:image/png;base64,${bbdownLoginResult.qrcode_base64}`}
                  alt="BBDown Login QRCode"
                  className="mx-auto h-56 w-56 rounded border bg-white p-2 sm:h-64 sm:w-64"
                />
              ) : (
                <div className="flex h-56 w-56 items-center justify-center rounded border bg-muted text-sm text-muted-foreground sm:h-64 sm:w-64">
                  二维码生成中...
                </div>
              )}
              <div className="text-sm text-muted-foreground">
                状态：{loginStatusLabel}（{bbdownLoginResult?.message || '等待中'}）
              </div>
              {bbdownLoginResult?.logs?.length ? (
                <div className="max-h-28 w-full overflow-y-auto rounded bg-muted p-2 text-xs text-muted-foreground">
                  {bbdownLoginResult.logs.slice(-6).map((line, idx) => (
                    <div key={`${line}-${idx}`}>{line}</div>
                  ))}
                </div>
              ) : null}
              <div className="flex w-full flex-wrap items-center justify-end gap-2">
                <Button
                  type="button"
                  variant="outline"
                  onClick={() => void pollLoginStatus(bbdownLoginResult?.session_id)}
                >
                  刷新状态
                </Button>
                <Button type="button" onClick={() => void handleCloseLoginDialog(false)}>
                  关闭
                </Button>
              </div>
            </div>
          </DialogContent>
        </Dialog>
      </div>
    )
  }

  return (
    <div className="max-w-xl p-4">
      <Form {...form}>
        <form onSubmit={form.handleSubmit(onSubmit)} className="flex flex-col gap-4">
          <div className="text-lg font-bold">
            设置{videoPlatforms.find(item => item.value === id)?.label}下载器 Cookie
          </div>

          <FormField
            control={form.control}
            name="cookie"
            render={({ field }) => (
              <FormItem className="flex flex-col gap-2">
                <FormLabel>Cookie</FormLabel>
                <FormControl>
                  <Textarea {...field} placeholder="输入 Cookie" rows={6} />
                </FormControl>
                <FormMessage />
              </FormItem>
            )}
          />

          <div className="flex items-center gap-2">
            <Button type="submit">保存</Button>
          </div>
        </form>
      </Form>
    </div>
  )
}

export default DownloaderForm
