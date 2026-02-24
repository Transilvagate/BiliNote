<div style="display: flex; justify-content: center; align-items: center; gap: 10px;">
    <p align="center">
  <img src="./doc/icon.svg" alt="BiliNote Banner" width="50" height="50" />
</p>
<h1 align="center">BiliNote (个人定制版)</h1>
</div>

<p align="center"><i>基于 <a href="https://github.com/JefferyHcool/BiliNote">JefferyHcool/BiliNote</a> 的个人修改版本</i></p>

<p align="center">
  <img src="https://img.shields.io/badge/license-MIT-blue.svg" />
  <img src="https://img.shields.io/badge/upstream-JefferyHcool%2FBiliNote-orange" />
  <img src="https://img.shields.io/badge/frontend-react-blue" />
  <img src="https://img.shields.io/badge/backend-fastapi-green" />
  <img src="https://img.shields.io/badge/docker-compose-blue" />
  <img src="https://img.shields.io/badge/CUDA-12.8-76B900?logo=nvidia" />
</p>

---

## 关于本仓库

本仓库是 [JefferyHcool/BiliNote](https://github.com/JefferyHcool/BiliNote)（v1.8.1）的个人 fork，包含大量针对个人使用习惯的功能扩展和改造。**本仓库仅供个人使用与备份，不代表上游项目的官方版本。**

如果你对原始项目感兴趣，请访问：
- 上游仓库：https://github.com/JefferyHcool/BiliNote
- 官方文档：https://docs.bilinote.app/

---

## 与上游的主要差异

本 fork 的改动主要围绕三个方面：

### 1. BBDown 集成 —— 替换 B 站字幕获取方案

**背景：** 上游项目使用 yt-dlp 来获取 B 站字幕，存在登录态维护困难、字幕抓取不稳定等问题。本 fork 引入 [BBDown](https://github.com/nilaoda/BBDown)（一个专门的哔哩哔哩下载器）作为 B 站字幕的主要获取工具。

**改动内容：**

| 模块 | 文件 | 说明 |
|------|------|------|
| BBDown 客户端 | `backend/app/services/bbdown_client.py` | 封装 BBDown 命令行调用，支持版本探测、字幕下载（`--sub-only`）、超时控制、日志脱敏 |
| BBDown 登录服务 | `backend/app/services/bbdown_login_service.py` | 通过 BBDown 的 QR 码登录流程管理 B 站认证，支持会话状态跟踪、超时清理、Cookie 自动持久化 |
| Cookie 管理 | `backend/app/services/bilibili_cookie_service.py` | 统一管理 B 站 Netscape 格式 Cookie 文件（读写、验证、迁移旧格式、在线检测登录状态） |
| QR 登录（备用） | `backend/app/services/bilibili_qr_login_service.py` | 直接调用 B 站 Passport API 的 QR 登录方案（已标记为废弃，保留代码） |
| 安全守卫 | `backend/app/security/config_guard.py` | 保护 Cookie/BBDown 等敏感接口：本地请求检测 + Admin Token 校验（HMAC 常量时间比较） |
| B 站下载器 | `backend/app/downloaders/bilibili_downloader.py` | 重写字幕下载逻辑，路由到 BBDown；新增 SRT/VTT/ASS/JSON3/JSON 多格式字幕解析器 |
| 字幕优先策略 | `backend/app/services/note.py` + `backend/app/downloaders/bilibili_downloader.py` | B站改为“中文字幕优先”；无中文时按时长分流：`<10分钟` 可尝试英文字幕，`>=10分钟` 或时长未知回退本地 ASR |
| Docker 镜像 | `backend/Dockerfile` | 构建时自动下载 BBDown v1.6.3 Linux x64 二进制并安装到 `/usr/local/bin/` |
| 后端路由 | `backend/app/routers/config.py` | 新增 BBDown 状态查询、登录启动/轮询/取消、字幕测试、Cookie 管理等 API 端点 |
| 前端设置面板 | `BillNote_frontend/src/components/Form/DownloaderForm/Form.tsx` | 全新 BBDown 管理 UI：安装状态、QR 码扫码登录弹窗、Cookie 检测与清除、字幕下载测试、CLI 命令参考 |
| 前端服务层 | `BillNote_frontend/src/services/downloader.ts` | 新增 BBDown/Cookie 全套 API 调用函数，统一 Admin Token 请求头注入 |

**BBDown 相关环境变量：**

| 变量名 | 默认值 | 说明 |
|--------|--------|------|
| `BBDOWN_BIN` | `/usr/local/bin/BBDown` | BBDown 可执行文件路径 |
| `BBDOWN_WORK_DIR` | `data/bbdown` | BBDown 字幕下载工作目录 |
| `BBDOWN_TIMEOUT_SECONDS` | `120` | BBDown 执行超时（秒） |
| `BILIBILI_SUBTITLE_PROVIDER` | `bbdown` | B 站字幕提供方（`bbdown` / `auto`） |
| `BILIBILI_COOKIES_FILE` | `config/bilibili.cookies.txt` | Cookie 文件路径 |
| `COOKIE_SECURITY_LOCAL_ONLY` | `true` | 是否仅允许本机访问 Cookie 敏感接口 |
| `CONFIG_ADMIN_TOKEN` | （空） | 可选的管理令牌，用于远程访问敏感接口 |

**BBDown 登录流程：**

```
前端设置页 → 点击"网页扫码登录" → 后端启动 BBDown login 子进程
  → BBDown 生成 QR 码 PNG → 后端读取并返回 base64 图片
  → 前端弹窗展示 QR 码 → 用户用哔哩哔哩 App 扫码
  → 前端轮询登录状态 → 登录成功后 Cookie 自动写入文件
```

### 2. 正式文稿生成 —— 新增输出格式与长文本分块处理

**背景：** 上游项目的笔记生成以"总结"为主，本 fork 新增"正式文稿"格式，目标是将口语化的视频内容忠实还原为书面文字，而非压缩摘要。

**改动内容：**

| 模块 | 文件 | 说明 |
|------|------|------|
| 文稿提示词 | `backend/app/gpt/prompt.py` | 新增 `FORMAL_TRANSCRIPT` 系列提示词，定义文稿还原规则（禁止摘要、保留顺序、多人发言归属等） |
| 提示词构建 | `backend/app/gpt/prompt_builder.py` | 新增 `formal_transcript` 格式选项和四种风格模式 |
| GPT 调用 | `backend/app/gpt/universal_gpt.py` | 新增 `chat_text()` 方法用于分块文稿生成的单轮文本调用 |
| Token 预算 | `backend/app/gpt/context_budget.py` | 启发式 Token 估算（中日韩/拉丁分别计算）、模型上下文窗口管理、溢出检测 |
| 笔记服务 | `backend/app/services/note.py` | 核心改动：长文本分块 → 逐块生成 → 树状归约合并的完整流水线 |
| 文稿清洗 | `backend/app/utils/formal_transcript.py` | 移除时间戳残留、规范化发言人标签格式 |
| 单元测试 | `backend/tests/test_formal_transcript.py` | 覆盖提示词拼装和文稿清洗逻辑 |

**四种文稿风格：**

| 值 | 说明 |
|----|------|
| `auto` | 自动检测：独白 / 一主多辅 / 多人对话 |
| `single_narration` | 强制单人连续叙述 |
| `lead_plus_support` | 一位主讲人 + 偶尔标注其他人 |
| `multi_dialogue` | 完整多人对话，带发言人标签 |

**分块处理流程（长视频场景）：**

```
原始字幕 → Token 估算 → 超出上下文窗口？
  → 是：按 FORMAL_TRANSCRIPT_CHUNK_TARGET (6000) 分块
       → 逐块发送给 GPT 生成文稿片段
       → 按 FORMAL_TRANSCRIPT_MERGE_TARGET (8000) 分组
       → 树状归约合并直到剩余一篇完整文稿
  → 否：单次调用生成
```

**相关环境变量：**

| 变量名 | 默认值 | 说明 |
|--------|--------|------|
| `MODEL_CONTEXT_LIMIT_DEFAULT` | `32000` | 默认模型上下文窗口 Token 数 |
| `MODEL_CONTEXT_LIMIT_OVERRIDES` | `{"gpt-4o-mini":128000,...}` | 按模型名覆盖上下文限制（JSON） |
| `FORMAL_TRANSCRIPT_OUTPUT_RESERVE` | `4000` | 输出预留 Token 数 |
| `FORMAL_TRANSCRIPT_CHUNK_TARGET` | `6000` | 分块目标 Token 数 |
| `FORMAL_TRANSCRIPT_MERGE_TARGET` | `8000` | 合并组目标 Token 数 |

### 3. 任务资产管理与 ZIP 导出

**背景：** 上游项目将截图统一存放在 `/static/screenshots/` 下，缺乏按任务隔离。本 fork 引入按任务 ID 隔离的资产目录结构，并支持将笔记打包为自包含 ZIP 下载。

**改动内容：**

| 模块 | 文件 | 说明 |
|------|------|------|
| 路径管理 | `backend/app/utils/task_assets.py` | 统一的 `data/notes/<task_id>/assets/` 路径管理，含文件名清洗和路径遍历防护 |
| Markdown 图片工具 | `backend/app/utils/markdown_images.py` | Markdown 图片引用的解析、遍历和路径重写 |
| ZIP 打包 | `backend/app/services/markdown_bundle_export.py` | 收集 Markdown 中引用的所有图片（本地/远程），打包为 `笔记标题.zip` |
| 资产服务端点 | `backend/app/routers/note.py` | `GET /tasks/{task_id}/assets/{path}` 端点，含路径遍历防护 |
| 导出端点 | `backend/app/routers/note.py` | `POST /tasks/{task_id}/export-md` 端点，返回 ZIP 流式响应 |
| 前端渲染 | `BillNote_frontend/src/pages/HomePage/components/MarkdownViewer.tsx` | 图片路径代理解析 + "下载"按钮触发 ZIP 导出 |
| 迁移脚本 | `backend/scripts/migrate_note_assets.py` | 将旧版 `/static/screenshots/` 下的截图迁移到按任务隔离的目录（支持 dry-run） |

**目录结构：**

```
data/notes/
  └── <task_id>/
      ├── note.md          # 生成的笔记
      └── assets/          # 截图等资产文件
          ├── screenshot_001.png
          └── screenshot_002.png
```

### 4. 其他改动

| 改动 | 说明 |
|------|------|
| 任务并发隔离 | 下载器由全局单例改为按任务实例化；ASR 增加互斥锁；BBDown 运行目录改为 `run_<timestamp>_<uuid>`，降低多任务串台风险 |
| YouTube 下载器 | 重构字幕处理逻辑，新增 SRT/VTT/JSON3/JSON 解析器，增强诊断信息输出 |
| 任务状态跟踪 | 状态 JSON 新增 `step`、`started_at`、`elapsed_ms`、`events[]`、`diagnostics` 字段 |
| 前端任务轮询 | 支持展示详细进度步骤、耗时、技术诊断信息、失败重试按钮 |
| 模型供应商管理 | 新增 `POST /delete_provider` 接口；支持删除供应商并级联删除其关联模型；前端设置页新增删除按钮 |
| Docker Compose | 新增 Nginx 反向代理层，backend/frontend 改为仅暴露内部端口 |
| 前端 Dockerfile | 调整构建配置 |

---

## 快速开始

### 使用 Docker 部署（推荐）

**CPU 版（默认）**

```bash
git clone https://github.com/Transilvagate/BiliNote.git
cd BiliNote
git checkout my-custom
cp .env.example .env
# 编辑 .env 配置端口等参数
docker compose up -d
```

**GPU 版（NVIDIA，推荐用于本地转写加速）**

前提：已安装 NVIDIA Windows 驱动（WSL2 环境）或 Linux 原生驱动，且 Docker Desktop / Docker Engine 已启用 GPU 支持。

```bash
cp .env.example .env
# 编辑 .env，设置转写模型（GPU 下推荐 large-v3-turbo）：
# TRANSCRIBER_TYPE=fast-whisper
# WHISPER_MODEL_SIZE=large-v3-turbo

docker compose -f docker-compose.gpu.yml up -d
```

验证 GPU 是否生效：

```bash
docker exec bilinote-backend python3 -c "
import ctranslate2
print('CUDA 设备数:', ctranslate2.get_cuda_device_count())
"
```

> **注意（RTX 50xx / Blackwell 架构）**：`Dockerfile.gpu` 使用 `nvidia/cuda:12.8.0` 基础镜像，支持 RTX 5060 及以上 Blackwell GPU。RTX 40xx 及更早架构同样兼容。

### 手动部署

```bash
# 后端
cd backend
pip install -r requirements.txt
python main.py

# 前端
cd BillNote_frontend
pnpm install
pnpm dev
```

### BBDown 登录（Docker 环境）

首次使用 B 站字幕功能前，需要完成登录：

**方式一：网页操作**
1. 打开前端设置页 → 下载器设置 → Bilibili 区域
2. 点击"网页扫码登录"
3. 用哔哩哔哩 App 扫描弹窗中的 QR 码

**方式二：命令行操作**
```bash
docker compose exec -it backend BBDown login
# 用手机扫描终端中的二维码
```

---

## 依赖说明

- **FFmpeg** — 音频处理（必须）
- **BBDown** v1.6.3 — B 站字幕下载（Docker 镜像已内置，手动部署需自行安装）
  - 项目地址：https://github.com/nilaoda/BBDown
  - 许可证：MIT
- **faster-whisper** — 本地音频转写（可选）
  - CPU 模式：使用 `int8` 量化，无需 GPU
  - GPU 模式：使用 `float16` 推理，需 NVIDIA GPU + CUDA 12.8+（GPU 版 Docker 镜像已内置）
  - 推荐模型：`large-v3-turbo`（显存约 3GB，中文准确率高，速度快）

---

## 上游同步

本仓库保留对上游的 remote 引用，可按需同步原项目的更新：

```bash
git fetch upstream
git checkout master
git merge upstream/master
git push origin master

# 将上游修复合入自定义分支（推荐 cherry-pick 方式）
git checkout my-custom
git cherry-pick <commit-hash>
git push origin my-custom
```

---

## 许可证

本项目继承上游的 MIT License。

**致谢：** 感谢 [JefferyHcool/BiliNote](https://github.com/JefferyHcool/BiliNote) 原作者的开源贡献，以及 [nilaoda/BBDown](https://github.com/nilaoda/BBDown) 提供的 B 站下载工具。
