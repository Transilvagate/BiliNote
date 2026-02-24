import request from '@/utils/request'
import axios from 'axios'
import toast from 'react-hot-toast'
import type { TaskStatus } from '@/store/taskStore'

export interface TaskStatusResponse {
  status: TaskStatus
  message?: string
  detail?: string
  task_id: string
  source?: string
  step?: {
    key: string
    label: string
    index: number
    total: number
  }
  started_at?: string
  updated_at?: string
  elapsed_ms?: number
  events?: Array<{
    at: string
    status: string
    message: string
    detail?: string
    source?: string
    diagnostics?: Record<string, any>
  }>
  diagnostics?: Record<string, any>
  error?: {
    reason_code?: string
    retryable?: boolean
  }
  result?: {
    markdown: string
    transcript: any
    audio_meta: any
  }
}

export const generateNote = async (data: {
  video_url: string
  platform: string
  quality: string
  model_name: string
  provider_id: string
  task_id?: string
  format: Array<string>
  style: string
  formal_transcript_style?: 'auto' | 'single_narration' | 'lead_plus_support' | 'multi_dialogue'
  extras?: string
  video_understanding?: boolean
  video_interval?: number
  grid_size: Array<number>
  force_refresh_transcript?: boolean
}) => {
  try {
    console.log('generateNote', data)
    const response = await request.post('/generate_note', data)

    if (!response) {
      if (response.data.msg) {
        toast.error(response.data.msg)
      }
      return null
    }
    toast.success('笔记生成任务已提交！')

    console.log('res', response)
    // 成功提示

    return response
  } catch (e: any) {
    console.error('❌ 请求出错', e)

    // 错误提示
    // toast.error('笔记生成失败，请稍后重试')

    throw e // 抛出错误以便调用方处理
  }
}

export const delete_task = async ({ video_id, platform }) => {
  try {
    const data = {
      video_id,
      platform,
    }
    const res = await request.post('/delete_task', data)


      toast.success('任务已成功删除')
      return res
  } catch (e) {
    toast.error('请求异常，删除任务失败')
    console.error('❌ 删除任务失败:', e)
    throw e
  }
}

export const get_task_status = async (task_id: string): Promise<TaskStatusResponse> => {
  try {
    // 成功提示

    return await request.get('/task_status/' + task_id)
  } catch (e) {
    console.error('❌ 请求出错', e)

    // 错误提示
    toast.error('笔记生成失败，请稍后重试')

    throw e // 抛出错误以便调用方处理
  }
}

const sanitizeFileName = (name: string, fallback = 'note') => {
  const cleaned = (name || '')
    .replace(/[\\/:*?"<>|]/g, '_')
    .trim()
    .replace(/\s+/g, ' ')
  return cleaned || fallback
}

const parseDownloadFilename = (contentDisposition?: string, fallback = 'note.zip') => {
  if (!contentDisposition) return fallback

  const utf8Match = contentDisposition.match(/filename\*=UTF-8''([^;]+)/i)
  if (utf8Match?.[1]) {
    try {
      return decodeURIComponent(utf8Match[1])
    } catch {
      // ignore decode error and fall back
    }
  }

  const plainMatch = contentDisposition.match(/filename="?([^";]+)"?/i)
  if (plainMatch?.[1]) {
    return plainMatch[1]
  }
  return fallback
}

export const export_markdown_bundle = async (data: {
  task_id: string
  title: string
  markdown: string
}) => {
  const baseURL = String(import.meta.env.VITE_API_BASE_URL || '/api').replace(/\/$/, '')
  const fallbackName = `${sanitizeFileName(data.title, 'note')}.zip`

  try {
    const response = await axios.post(
      `${baseURL}/tasks/${encodeURIComponent(data.task_id)}/export-md`,
      {
        title: data.title,
        markdown: data.markdown,
      },
      {
        responseType: 'blob',
        timeout: 30000,
      }
    )

    return {
      blob: response.data as Blob,
      filename: parseDownloadFilename(response.headers['content-disposition'], fallbackName),
    }
  } catch (e) {
    toast.error('导出失败，请稍后重试')
    throw e
  }
}
