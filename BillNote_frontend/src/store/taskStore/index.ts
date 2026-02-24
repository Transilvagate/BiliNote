import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import { delete_task, generateNote } from '@/services/note.ts'
import { v4 as uuidv4 } from 'uuid'
import toast from 'react-hot-toast'

export type TaskStatus =
  | 'PENDING'
  | 'PARSING'
  | 'DOWNLOADING'
  | 'TRANSCRIBING'
  | 'SUMMARIZING'
  | 'FORMATTING'
  | 'SAVING'
  | 'SUCCESS'
  | 'FAILED'

export interface AudioMeta {
  cover_url: string
  duration: number
  file_path: string
  platform: string
  raw_info: any
  title: string
  video_id: string
}

export interface Segment {
  start: number
  end: number
  text: string
}

export interface Transcript {
  full_text: string
  language: string
  raw: any
  segments: Segment[]
}

export interface TaskProgressStep {
  key: string
  label: string
  index: number
  total: number
}

export interface TaskProgressEvent {
  at: string
  status: string
  message: string
  detail?: string
  source?: string
  diagnostics?: Record<string, any>
}

export interface TaskProgressError {
  reason_code?: string
  retryable?: boolean
}

export interface TaskProgress {
  message?: string
  detail?: string
  source?: string
  step?: TaskProgressStep
  started_at?: string
  updated_at?: string
  elapsed_ms?: number
  events?: TaskProgressEvent[]
  diagnostics?: Record<string, any>
  error?: TaskProgressError
}

export interface Markdown {
  ver_id: string
  content: string
  style: string
  model_name: string
  created_at: string
}

export interface Task {
  id: string
  markdown: string|Markdown [] //为了兼容之前的笔记
  transcript: Transcript
  status: TaskStatus
  platform: string
  progress?: TaskProgress
  audioMeta: AudioMeta
  createdAt: string
  formData: {
    video_url: string
    link: undefined | boolean
    screenshot: undefined | boolean
    platform: string
    quality: string
    model_name: string
    provider_id: string
    style?: string
    formal_transcript_style?: 'auto' | 'single_narration' | 'lead_plus_support' | 'multi_dialogue'
    extras?: string
    format?: string[]
    video_understanding?: boolean
    video_interval?: number
    grid_size?: number[]
    task_id?: string
  }
}

interface TaskStore {
  tasks: Task[]
  currentTaskId: string | null
  addPendingTask: (taskId: string, platform: string, formData: any) => void
  updateTaskContent: (id: string, data: Partial<Omit<Task, 'id' | 'createdAt'>>) => void
  removeTask: (id: string) => void
  clearTasks: () => void
  setCurrentTask: (taskId: string | null) => void
  getCurrentTask: () => Task | null
  retryTask: (id: string, payload?: any) => Promise<void>
}

export const useTaskStore = create<TaskStore>()(
  persist(
    (set, get) => ({
      tasks: [],
      currentTaskId: null,

      addPendingTask: (taskId: string, platform: string, formData: any) =>

        set(state => ({
          tasks: [
            {
              formData: formData,
              id: taskId,
              status: 'PENDING',
              markdown: '',
              platform: platform,
              progress: {
                message: '任务排队中',
                detail: '任务已提交，等待后端开始处理',
                source: 'system',
                events: [],
                diagnostics: {},
              },
              transcript: {
                full_text: '',
                language: '',
                raw: null,
                segments: [],
              },
              createdAt: new Date().toISOString(),
              audioMeta: {
                cover_url: '',
                duration: 0,
                file_path: '',
                platform: '',
                raw_info: null,
                title: '',
                video_id: '',
              },
            },
            ...state.tasks,
          ],
          currentTaskId: taskId, // 默认设置为当前任务
        })),

      updateTaskContent: (id, data) =>
          set(state => ({
            tasks: state.tasks.map(task => {
              if (task.id !== id) return task

              if (task.status === 'SUCCESS' && data.status === 'SUCCESS') return task
              const mergedData = {
                ...data,
                progress: {
                  ...(task.progress || {}),
                  ...(data.progress || {}),
                },
              }

              // 如果是 markdown 字符串，封装为版本
              if (typeof data.markdown === 'string') {
                const prev = task.markdown
                const newVersion: Markdown = {
                  ver_id: `${task.id}-${uuidv4()}`,
                  content: data.markdown,
                  style: task.formData.style || '',
                  model_name: task.formData.model_name || '',
                  created_at: new Date().toISOString(),
                }

                let updatedMarkdown: Markdown[]
                if (Array.isArray(prev)) {
                  updatedMarkdown = [newVersion, ...prev]
                } else {
                  updatedMarkdown = [
                    newVersion,
                    ...(typeof prev === 'string' && prev
                        ? [{
                          ver_id: `${task.id}-${uuidv4()}`,
                          content: prev,
                          style: task.formData.style || '',
                          model_name: task.formData.model_name || '',
                          created_at: new Date().toISOString(),
                        }]
                        : []),
                  ]
                }

                return {
                  ...task,
                  ...mergedData,
                  markdown: updatedMarkdown,
                }
              }

              return { ...task, ...mergedData }
            }),
          })),


      getCurrentTask: () => {
        const currentTaskId = get().currentTaskId
        return get().tasks.find(task => task.id === currentTaskId) || null
      },
      retryTask: async (id: string, payload?: any) => {

        if (!id){
          toast.error('任务不存在')
          return
        }
        const task = get().tasks.find(task => task.id === id)
        console.log('retry',task)
        if (!task) return

        const newFormData = payload || task.formData
        await generateNote({
          ...newFormData,
          task_id: id,
          force_refresh_transcript: true,
        })

        set(state => ({
          tasks: state.tasks.map(t =>
              t.id === id
                  ? {
                    ...t,
                    formData: newFormData, // ✅ 显式更新 formData
                    status: 'PENDING',
                    progress: {
                      ...(t.progress || {}),
                      message: '任务重试中',
                      detail: '正在重新拉取字幕与转写',
                      source: 'system',
                    },
                  }
                  : t
          ),
        }))
      },


      removeTask: async id => {
        const task = get().tasks.find(t => t.id === id)

        // 更新 Zustand 状态
        set(state => ({
          tasks: state.tasks.filter(task => task.id !== id),
          currentTaskId: state.currentTaskId === id ? null : state.currentTaskId,
        }))

        // 调用后端删除接口（如果找到了任务）
        if (task) {
          await delete_task({
            video_id: task.audioMeta.video_id,
            platform: task.platform,
          })
        }
      },

      clearTasks: () => set({ tasks: [], currentTaskId: null }),

      setCurrentTask: taskId => set({ currentTaskId: taskId }),
    }),
    {
      name: 'task-storage',
      version: 3,
      migrate: (persistedState: any, version) => {
        if (version < 2) {
          return {
            tasks: [],
            currentTaskId: null,
          }
        }
        if (version < 3 && persistedState?.tasks) {
          return {
            ...persistedState,
            tasks: persistedState.tasks.map((task: any) => ({
              ...task,
              status: task.status === 'FAILD' ? 'FAILED' : task.status,
              progress: task.progress || {
                message: task.status === 'SUCCESS' ? '任务完成' : '任务处理中',
                detail: '',
                source: 'system',
                events: [],
                diagnostics: {},
              },
            })),
          }
        }
        return persistedState
      },
    }
  )
)
