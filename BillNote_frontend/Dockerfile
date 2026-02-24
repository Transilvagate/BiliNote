# === 前端构建阶段 ===
ARG NODE_BASE_IMAGE=m.daocloud.io/docker.io/library/node:20-slim
ARG NGINX_BASE_IMAGE=m.daocloud.io/docker.io/library/nginx:1.25-alpine

FROM ${NODE_BASE_IMAGE} AS builder

# 使用 pnpm v9，规避 pnpm v10 在容器内默认拦截依赖构建脚本导致的构建失败
RUN corepack enable && corepack prepare pnpm@9.15.5 --activate

# 设置工作目录
WORKDIR /app

# 拷贝前端源码
COPY ./BillNote_frontend /app

# 安装依赖并构建
RUN pnpm install --no-frozen-lockfile && pnpm run build

# --- 阶段2：使用 nginx 作为静态服务器 ---
FROM ${NGINX_BASE_IMAGE}

# 删除默认配置（可选）
RUN rm -rf /etc/nginx/conf.d/default.conf
COPY ./BillNote_frontend/deploy/default.conf /etc/nginx/conf.d/default.conf


# 拷贝构建产物
COPY --from=builder /app/dist /usr/share/nginx/html
