/* -------------------- 常量 -------------------- */
import {
  BiliBiliLogo,
  DouyinLogo,
  KuaishouLogo,
  LocalLogo,
  YoutubeLogo,
} from '@/components/Icons/platform.tsx'

export const noteFormats = [
  { label: '目录', value: 'toc' },
  { label: '原片跳转', value: 'link' },
  { label: '原片截图', value: 'screenshot' },
  { label: 'AI总结', value: 'summary' },
  { label: '正式文稿', value: 'formal_transcript' },
] as const

export const noteStyles = [
  { label: '精简', value: 'minimal' },
  { label: '详细', value: 'detailed' },
  { label: '教程', value: 'tutorial' },
  { label: '学术', value: 'academic' },
  { label: '小红书', value: 'xiaohongshu' },
  { label: '生活向', value: 'life_journal' },
  { label: '任务导向', value: 'task_oriented' },
  { label: '商业风格', value: 'business' },
  { label: '会议纪要', value: 'meeting_minutes' },
] as const

export const formalTranscriptStyles = [
  { label: '自动判定', value: 'auto' },
  { label: '单人叙述', value: 'single_narration' },
  { label: '主讲+补充', value: 'lead_plus_support' },
  { label: '多人对话', value: 'multi_dialogue' },
] as const

export const videoPlatforms = [
  { label: '哔哩哔哩', value: 'bilibili', logo: BiliBiliLogo },
  { label: 'YouTube', value: 'youtube', logo: YoutubeLogo },
  { label: '抖音', value: 'douyin', logo: DouyinLogo },
  { label: '快手', value: 'kuaishou', logo: KuaishouLogo },
  { label: '本地视频', value: 'local', logo: LocalLogo },
] as const

export const transcriptSources = [
  { label: '自动（优先字幕）', value: 'auto', description: '优先使用平台字幕，失败时自动回退本地语音转写' },
  { label: 'BBDown 字幕', value: 'bbdown', description: '仅使用 BBDown / 平台字幕，不进行本地语音转写' },
  { label: '本地模型转写', value: 'asr', description: '跳过字幕下载，直接使用本地语音识别模型转写' },
] as const
