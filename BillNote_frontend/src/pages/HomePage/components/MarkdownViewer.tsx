import { useState, useEffect, useMemo, memo, FC } from 'react'
import ReactMarkdown from 'react-markdown'
import { Button } from '@/components/ui/button.tsx'
import { Copy, ArrowRight, Play, ExternalLink, ChevronDown, ChevronUp } from 'lucide-react'
import { toast } from 'react-hot-toast'
import Error from '@/components/Lottie/error.tsx'
import Loading from '@/components/Lottie/Loading.tsx'
import Idle from '@/components/Lottie/Idle.tsx'
import StepBar from '@/pages/HomePage/components/StepBar.tsx'
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter'
import { atomDark as codeStyle } from 'react-syntax-highlighter/dist/esm/styles/prism'
import Zoom from 'react-medium-image-zoom'
import 'react-medium-image-zoom/dist/styles.css'
import gfm from 'remark-gfm'
import remarkMath from 'remark-math'
import rehypeKatex from 'rehype-katex'
import 'katex/dist/katex.min.css'
import 'github-markdown-css/github-markdown-light.css'
import { ScrollArea } from '@/components/ui/scroll-area.tsx'
import { export_markdown_bundle } from '@/services/note.ts'
import { useTaskStore } from '@/store/taskStore'
import { noteStyles } from '@/constant/note.ts'
import { MarkdownHeader } from '@/pages/HomePage/components/MarkdownHeader.tsx'
import TranscriptViewer from '@/pages/HomePage/components/transcriptViewer.tsx'
import MarkmapEditor from '@/pages/HomePage/components/MarkmapComponent.tsx'
import ChatPanel from '@/pages/HomePage/components/ChatPanel.tsx'
import VideoBanner from '@/pages/HomePage/components/VideoBanner.tsx'

interface VersionNote {
  ver_id: string
  content: string
  style: string
  model_name: string
  created_at?: string
}

interface MarkdownViewerProps {
  content?: string | VersionNote[]
  status: 'idle' | 'loading' | 'success' | 'failed'
}

const steps = [
  { label: '解析链接', key: 'PARSING' },
  { label: '下载音频', key: 'DOWNLOADING' },
  { label: '转写文字', key: 'TRANSCRIBING' },
  { label: '总结内容', key: 'SUMMARIZING' },
  { label: '格式化内容', key: 'FORMATTING' },
  { label: '保存结果', key: 'SAVING' },
  { label: '保存完成', key: 'SUCCESS' },
]

const remarkPlugins = [gfm, remarkMath]
const rehypePlugins = [rehypeKatex]

/**
 * 构建 ReactMarkdown components 对象。
 * 使用函数 + useMemo 避免每次渲染都创建新的函数实例。
 */
function createMarkdownComponents(resolveImageSrc: (src?: string) => string | undefined) {
  return {
    h1: ({ children, ...props }: any) => (
      <h1
        className="text-primary my-6 scroll-m-20 text-3xl font-extrabold tracking-tight lg:text-4xl"
        {...props}
      >
        {children}
      </h1>
    ),
    h2: ({ children, ...props }: any) => (
      <h2
        className="text-primary mt-10 mb-4 scroll-m-20 border-b pb-2 text-2xl font-semibold tracking-tight first:mt-0"
        {...props}
      >
        {children}
      </h2>
    ),
    h3: ({ children, ...props }: any) => (
      <h3
        className="text-primary mt-8 mb-4 scroll-m-20 text-xl font-semibold tracking-tight"
        {...props}
      >
        {children}
      </h3>
    ),
    h4: ({ children, ...props }: any) => (
      <h4
        className="text-primary mt-6 mb-2 scroll-m-20 text-lg font-semibold tracking-tight"
        {...props}
      >
        {children}
      </h4>
    ),
    p: ({ children, ...props }: any) => (
      <p className="leading-7 [&:not(:first-child)]:mt-6" {...props}>
        {children}
      </p>
    ),
    a: ({ href, children, ...props }: any) => {
      const isOriginLink =
        typeof children[0] === 'string' &&
        (children[0] as string).startsWith('原片 @')

      if (isOriginLink) {
        const timeMatch = (children[0] as string).match(/原片 @ (\d{2}:\d{2})/)
        const timeText = timeMatch ? timeMatch[1] : '原片'

        return (
          <span className="origin-link my-2 inline-flex">
            <a
              href={href}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1.5 rounded-full bg-blue-50 px-3 py-1 text-sm font-medium text-blue-700 transition-colors hover:bg-blue-100"
              {...props}
            >
              <Play className="h-3.5 w-3.5" />
              <span>原片（{timeText}）</span>
            </a>
          </span>
        )
      }

      return (
        <a
          href={href}
          target="_blank"
          rel="noopener noreferrer"
          className="text-primary hover:text-primary/80 inline-flex items-center gap-0.5 font-medium underline underline-offset-4"
          {...props}
        >
          {children}
          {href?.startsWith('http') && (
            <ExternalLink className="ml-0.5 inline-block h-3 w-3" />
          )}
        </a>
      )
    },
    img: ({ node, ...props }: any) => {
      props.src = resolveImageSrc(props.src)

      return (
        <div className="my-8 flex justify-center">
          <Zoom>
            <img
              {...props}
              className="max-w-full cursor-zoom-in rounded-lg object-cover shadow-md transition-all hover:shadow-lg"
              style={{ maxHeight: '500px' }}
            />
          </Zoom>
        </div>
      )
    },
    strong: ({ children, ...props }: any) => (
      <strong className="text-primary font-bold" {...props}>
        {children}
      </strong>
    ),
    li: ({ children, ...props }: any) => {
      const rawText = String(children)
      const isFakeHeading = /^(\*\*.+\*\*)$/.test(rawText.trim())

      if (isFakeHeading) {
        return (
          <div className="text-primary my-4 text-lg font-bold">{children}</div>
        )
      }

      return (
        <li className="my-1" {...props}>
          {children}
        </li>
      )
    },
    ul: ({ children, ...props }: any) => (
      <ul className="my-6 ml-6 list-disc [&>li]:mt-2" {...props}>
        {children}
      </ul>
    ),
    ol: ({ children, ...props }: any) => (
      <ol className="my-6 ml-6 list-decimal [&>li]:mt-2" {...props}>
        {children}
      </ol>
    ),
    blockquote: ({ children, ...props }: any) => (
      <blockquote
        className="border-primary/20 text-muted-foreground mt-6 border-l-4 pl-4 italic"
        {...props}
      >
        {children}
      </blockquote>
    ),
    code: ({ inline, className, children, ...props }: any) => {
      const match = /language-(\w+)/.exec(className || '')
      const codeContent = String(children).replace(/\n$/, '')

      if (!inline && match) {
        return (
          <div className="group bg-muted relative my-6 overflow-hidden rounded-lg border shadow-sm">
            <div className="bg-muted text-muted-foreground flex items-center justify-between px-4 py-1.5 text-sm font-medium">
              <div>{match[1].toUpperCase()}</div>
              <button
                onClick={() => {
                  navigator.clipboard.writeText(codeContent)
                  toast.success('代码已复制')
                }}
                className="bg-background/80 hover:bg-background flex items-center gap-1 rounded-md px-2 py-1 text-xs font-medium transition-colors"
              >
                <Copy className="h-3.5 w-3.5" />
                复制
              </button>
            </div>
            <SyntaxHighlighter
              style={codeStyle}
              language={match[1]}
              PreTag="div"
              className="!bg-muted !m-0 !p-0"
              customStyle={{
                margin: 0,
                padding: '1rem',
                background: 'transparent',
                fontSize: '0.9rem',
              }}
              {...props}
            >
              {codeContent}
            </SyntaxHighlighter>
          </div>
        )
      }

      return (
        <code
          className="bg-muted relative rounded px-[0.3rem] py-[0.2rem] font-mono text-sm"
          {...props}
        >
          {children}
        </code>
      )
    },
    table: ({ children, ...props }: any) => (
      <div className="my-6 w-full overflow-y-auto">
        <table className="w-full border-collapse text-sm" {...props}>
          {children}
        </table>
      </div>
    ),
    th: ({ children, ...props }: any) => (
      <th
        className="border-muted-foreground/20 border px-4 py-2 text-left font-medium [&[align=center]]:text-center [&[align=right]]:text-right"
        {...props}
      >
        {children}
      </th>
    ),
    td: ({ children, ...props }: any) => (
      <td
        className="border-muted-foreground/20 border px-4 py-2 text-left [&[align=center]]:text-center [&[align=right]]:text-right"
        {...props}
      >
        {children}
      </td>
    ),
    hr: ({ ...props }: any) => (
      <hr className="border-muted-foreground/20 my-8" {...props} />
    ),
  }
}

const MarkdownViewer: FC<MarkdownViewerProps> = memo(({ status }) => {
  const [currentVerId, setCurrentVerId] = useState<string>('')
  const [selectedContent, setSelectedContent] = useState<string>('')
  const [modelName, setModelName] = useState<string>('')
  const [style, setStyle] = useState<string>('')
  const [createTime, setCreateTime] = useState<string>('')
  const apiBaseURL = String(import.meta.env.VITE_API_BASE_URL || '/api').replace(/\/$/, '')
  const backendBaseURL = apiBaseURL.endsWith('/api') ? apiBaseURL.slice(0, -4) : apiBaseURL
  const getCurrentTask = useTaskStore.getState().getCurrentTask
  const currentTask = useTaskStore(state => state.getCurrentTask())
  const taskStatus = currentTask?.status || 'PENDING'
  const retryTask = useTaskStore.getState().retryTask
  const progress = currentTask?.progress
  const isMultiVersion = Array.isArray(currentTask?.markdown)
  const [showTranscribe, setShowTranscribe] = useState(false)
  const [showChat, setShowChat] = useState<false | 'half' | 'full'>(false)
  const [viewMode, setViewMode] = useState<'map' | 'preview'>('preview')
  const [showDiagnostics, setShowDiagnostics] = useState(false)

  const stepForBar = taskStatus === 'PENDING' ? 'PARSING' : taskStatus
  const progressMessage = progress?.message || '正在处理中'
  const progressDetail = progress?.detail || '任务已提交，请稍候...'
  const progressSource = progress?.source || 'system'
  const elapsedMs = progress?.elapsed_ms || 0
  const recentEvents = (progress?.events || []).slice(-5).reverse()

  const formatElapsed = (ms: number) => {
    if (!ms || ms < 1000) return '刚刚开始'
    const totalSec = Math.floor(ms / 1000)
    const min = Math.floor(totalSec / 60)
    const sec = totalSec % 60
    if (!min) return `${sec}s`
    return `${min}m ${sec}s`
  }
  // 多版本内容处理
  useEffect(() => {
    if (!currentTask) return

    if (!isMultiVersion) {
      setCurrentVerId('') // 清空旧版本 ID
      setModelName(currentTask.formData.model_name)
      setStyle(currentTask.formData.style || '')
      setCreateTime(currentTask.createdAt)
      setSelectedContent(typeof currentTask?.markdown === 'string' ? currentTask.markdown : '')
    } else {
      const versionList = Array.isArray(currentTask.markdown) ? currentTask.markdown : []
      const latestVersion = [...versionList].sort(
        (a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime()
      )[0]

      if (latestVersion) {
        setCurrentVerId(latestVersion.ver_id)
      }
    }
  }, [currentTask?.id, taskStatus])
  useEffect(() => {
    if (!currentTask || !isMultiVersion) return

    const versionList = Array.isArray(currentTask.markdown) ? currentTask.markdown : []
    const currentVer = versionList.find(v => v.ver_id === currentVerId)
    if (currentVer) {
      setModelName(currentVer.model_name)
      setStyle(currentVer.style)
      setCreateTime(currentVer.created_at || '')
      setSelectedContent(currentVer.content)
    }
  }, [currentVerId, currentTask?.id])
  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(selectedContent)
      toast.success('已复制到剪贴板')
    } catch (e) {
      toast.error('复制失败')
    }
  }
  const encodeAssetPath = (path: string) => path.split('/').map(encodeURIComponent).join('/')

  const resolveImageSrc = (src?: string) => {
    if (!src) return src
    const trimmed = src.trim()
    if (
      trimmed.startsWith('http://') ||
      trimmed.startsWith('https://') ||
      trimmed.startsWith('data:') ||
      trimmed.startsWith('blob:')
    ) {
      return trimmed
    }

    if (trimmed.startsWith('./assets/') || trimmed.startsWith('assets/')) {
      const taskId = currentTask?.id
      if (!taskId) return trimmed
      const relativePath = trimmed.replace(/^\.\//, '').replace(/^assets\//, '')
      return `${apiBaseURL}/tasks/${encodeURIComponent(taskId)}/assets/${encodeAssetPath(relativePath)}`
    }

    if (trimmed.startsWith('/')) {
      return `${backendBaseURL}${trimmed}`
    }

    return trimmed
  }
  const markdownComponents = useMemo(
    () => createMarkdownComponents(resolveImageSrc),
    [apiBaseURL, backendBaseURL, currentTask?.id]
  )

  const handleDownload = async () => {
    const task = getCurrentTask()
    const name = task?.audioMeta.title || 'note'
    if (!task?.id) {
      toast.error('任务不存在，无法导出')
      return
    }
    try {
      const { blob, filename } = await export_markdown_bundle({
        task_id: task.id,
        title: name,
        markdown: selectedContent || '',
      })
      const link = document.createElement('a')
      link.href = URL.createObjectURL(blob)
      link.download = filename || `${name}.zip`
      document.body.appendChild(link)
      link.click()
      document.body.removeChild(link)
      URL.revokeObjectURL(link.href)
    } catch (error) {
      console.error('导出失败', error)
    }
  }

  if (status === 'loading') {
    return (
      <div className="flex h-screen w-full flex-col items-center justify-center space-y-4 px-6 text-neutral-500">
        <StepBar steps={steps} currentStep={stepForBar} />
        <Loading className="h-5 w-5" />
        <div className="w-full max-w-2xl rounded-lg border bg-white p-4 shadow-sm">
          <p className="text-lg font-bold text-neutral-800">正在生成笔记，请稍候…</p>
          <p className="mt-2 text-sm text-neutral-700">
            {progressMessage} · {progressDetail}
          </p>
          <p className="mt-1 text-xs text-neutral-500">
            数据来源：{progressSource} · 已耗时：{formatElapsed(elapsedMs)}
          </p>

          <div className="mt-3 border-t pt-3">
            <button
              className="flex items-center gap-1 text-xs text-neutral-600 hover:text-neutral-800"
              onClick={() => setShowDiagnostics(!showDiagnostics)}
              type="button"
            >
              {showDiagnostics ? <ChevronUp className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />}
              查看技术细节
            </button>
            {showDiagnostics && (
              <div className="mt-2 space-y-2 text-xs text-neutral-600">
                {recentEvents.length > 0 && (
                  <div className="space-y-1 rounded bg-neutral-50 p-2">
                    {recentEvents.map((event, idx) => (
                      <div key={`${event.at}-${idx}`}>
                        <span className="font-medium">{event.status}</span>
                        <span className="ml-2">{event.message}</span>
                        {event.detail ? <span className="ml-1 text-neutral-500">- {event.detail}</span> : null}
                      </div>
                    ))}
                  </div>
                )}
                {progress?.diagnostics ? (
                  <pre className="max-h-32 overflow-auto rounded bg-neutral-100 p-2 text-[11px]">
                    {JSON.stringify(progress.diagnostics, null, 2)}
                  </pre>
                ) : null}
              </div>
            )}
          </div>
        </div>
      </div>
    )
  }

  if (status === 'idle') {
    return (
      <div className="flex h-screen w-full flex-col items-center justify-center space-y-3 text-neutral-500">
        <Idle />
        <div className="text-center">
          <p className="text-lg font-bold">输入视频链接并点击"生成笔记"</p>
          <p className="mt-2 text-xs text-neutral-500">支持哔哩哔哩、YouTube 、抖音等视频平台</p>
        </div>
      </div>
    )
  }

  if (status === 'failed' && !isMultiVersion) {
    return (
      <div className="flex h-screen w-full flex-col items-center justify-center gap-4 space-y-3">
        <Error />
        <div className="w-full max-w-2xl text-center">
          <p className="text-lg font-bold text-red-500">笔记生成失败</p>
          <p className="mt-2 mb-2 text-xs text-red-400">
            {progress?.detail || progress?.message || '请检查后台或稍后再试'}
          </p>

          <div className="mb-3 border-t pt-2 text-left">
            <button
              className="flex items-center gap-1 text-xs text-neutral-600 hover:text-neutral-800"
              onClick={() => setShowDiagnostics(!showDiagnostics)}
              type="button"
            >
              {showDiagnostics ? <ChevronUp className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />}
              查看技术细节
            </button>
            {showDiagnostics && (
              <div className="mt-2 space-y-2">
                {progress?.error ? (
                  <div className="rounded bg-neutral-100 p-2 text-xs text-neutral-700">
                    错误码：{progress.error.reason_code || 'UNKNOWN'} · 可重试：
                    {progress.error.retryable ? '是' : '否'}
                  </div>
                ) : null}
                {progress?.diagnostics ? (
                  <pre className="max-h-36 overflow-auto rounded bg-neutral-100 p-2 text-[11px] text-neutral-700">
                    {JSON.stringify(progress.diagnostics, null, 2)}
                  </pre>
                ) : null}
              </div>
            )}
          </div>

          <Button onClick={() => currentTask?.id && retryTask(currentTask.id)} size="lg">
            重试
          </Button>
        </div>
      </div>
    )
  }

  return (
    <div className="flex h-screen w-full flex-col overflow-hidden">
      <MarkdownHeader
        currentTask={currentTask}
        isMultiVersion={isMultiVersion}
        currentVerId={currentVerId}
        setCurrentVerId={setCurrentVerId}
        modelName={modelName}
        style={style}
        noteStyles={noteStyles}
        onCopy={handleCopy}
        onDownload={handleDownload}
        createAt={createTime}
        showTranscribe={showTranscribe}
        setShowTranscribe={setShowTranscribe}
        showChat={showChat}
        setShowChat={setShowChat}
        viewMode={viewMode}
        setViewMode={setViewMode}
      />

      {viewMode === 'map' ? (
        <div className="flex w-full flex-1 overflow-hidden bg-white">
          <div className={'w-full'}>
            <MarkmapEditor
              value={selectedContent}
              onChange={() => {}}
              height="100%" // 根据需求可以设定百分比或固定高度
              title={currentTask?.audioMeta?.title || '思维导图'}
            />
          </div>
        </div>
      ) : (
        <div className="flex flex-1 overflow-hidden bg-white py-2">
          {selectedContent && selectedContent !== 'loading' && selectedContent !== 'empty' ? (
            <>
              {showChat === 'full' && currentTask ? (
                <div className="h-full w-full">
                  <ChatPanel taskId={currentTask.id} mode="full" onModeChange={setShowChat} />
                </div>
              ) : (
              <>
              <ScrollArea className="min-w-0 flex-1">
                <div className="px-2">
                  <VideoBanner
                    audioMeta={currentTask?.audioMeta}
                    videoUrl={currentTask?.formData?.video_url}
                  />
                </div>
                <div className={'markdown-body w-full px-2'}>
                  <ReactMarkdown
                    remarkPlugins={remarkPlugins}
                    rehypePlugins={rehypePlugins}
                    components={markdownComponents}
                  >
                    {selectedContent.replace(/^>\s*来源链接：[^\n]*\n*/m, '')}
                  </ReactMarkdown>
                </div>
              </ScrollArea>
              {showTranscribe && (
                <div className={'ml-2 w-2/4'}>
                  <TranscriptViewer />
                </div>
              )}
              {/* 侧边问答模式：markdown + ChatPanel 各占一半 */}
              {showChat === 'half' && currentTask && (
                <div className="ml-2 h-full w-1/2 shrink-0">
                  <ChatPanel taskId={currentTask.id} mode="half" onModeChange={setShowChat} />
                </div>
              )}
              </>
              )}
            </>
          ) : (
            <div className="flex h-full w-full items-center justify-center">
              <div className="w-[300px] flex-col justify-items-center">
                <div className="bg-primary-light mb-4 flex h-16 w-16 items-center justify-center rounded-full">
                  <ArrowRight className="text-primary h-8 w-8" />
                </div>
                <p className="mb-2 text-neutral-600">输入视频链接并点击"生成笔记"按钮</p>
                <p className="text-xs text-neutral-500">支持哔哩哔哩、YouTube等视频网站</p>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  )
})

MarkdownViewer.displayName = 'MarkdownViewer'

export default MarkdownViewer
