import { useEffect, useRef } from 'react'
import { useTaskStore } from '@/store/taskStore'
import { get_task_status } from '@/services/note.ts'
import toast from 'react-hot-toast'

export const useTaskPolling = (interval = 3000) => {
  const tasks = useTaskStore(state => state.tasks)
  const updateTaskContent = useTaskStore(state => state.updateTaskContent)

  const tasksRef = useRef(tasks)

  // 每次 tasks 更新，把最新的 tasks 同步进去
  useEffect(() => {
    tasksRef.current = tasks
  }, [tasks])

  useEffect(() => {
    const timer = setInterval(async () => {
      const pendingTasks = tasksRef.current.filter(
        task => task.status != 'SUCCESS' && task.status != 'FAILED'
      )

      // 无活跃任务时跳过轮询
      if (pendingTasks.length === 0) return

      for (const task of pendingTasks) {
        try {
          const res = await get_task_status(task.id)
          const { status } = res
          const progress = {
            message: res.message,
            detail: res.detail,
            source: res.source,
            step: res.step,
            started_at: res.started_at,
            updated_at: res.updated_at,
            elapsed_ms: res.elapsed_ms,
            events: res.events || [],
            diagnostics: res.diagnostics || {},
            error: res.error,
          }

          const shouldUpdate =
            !!status &&
            (status !== task.status ||
              progress.updated_at !== task.progress?.updated_at ||
              progress.detail !== task.progress?.detail ||
              progress.message !== task.progress?.message)

          if (shouldUpdate) {
            if (status === 'SUCCESS' && res.result) {
              const { markdown, transcript, audio_meta } = res.result
              toast.success('笔记生成成功')
              updateTaskContent(task.id, {
                status,
                markdown,
                transcript,
                audioMeta: audio_meta,
                progress,
              })
            } else if (status === 'SUCCESS') {
              updateTaskContent(task.id, { status, progress })
            } else if (status === 'FAILED') {
              updateTaskContent(task.id, { status, progress })
              console.warn(`⚠️ 任务 ${task.id} 失败`)
            } else {
              updateTaskContent(task.id, { status, progress })
            }
          }
        } catch (e: any) {
          console.error('❌ 任务轮询失败：', e)
          updateTaskContent(task.id, {
            status: 'FAILED',
            progress: {
              ...(task.progress || {}),
              message: '任务轮询失败',
              detail: e?.msg || e?.message || '无法获取任务状态',
              source: 'system',
              error: { reason_code: 'POLLING_ERROR', retryable: true },
            },
          })
        }
      }
    }, interval)

    return () => clearInterval(timer)
  }, [interval])
}
