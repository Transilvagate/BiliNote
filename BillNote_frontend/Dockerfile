# === 前端构建阶段 ===
ARG NODE_BASE_IMAGE=m.daocloud.io/docker.io/library/node:20-slim
ARG NGINX_BASE_IMAGE=m.daocloud.io/docker.io/library/nginx:1.25-alpine

FROM ${NODE_BASE_IMAGE} AS builder

# 使用 pnpm v9，规避 pnpm v10 在容器内默认拦截依赖构建脚本导致的构建失败
# 配置 npm 使用国内镜像源，确保 corepack 拉取 pnpm 及后续依赖安装均走国内镜像
# COREPACK_NPM_REGISTRY 控制 corepack 下载 pnpm 二进制时使用的 registry
ENV COREPACK_NPM_REGISTRY=https://registry.npmmirror.com
RUN npm config set registry https://registry.npmmirror.com && \
    corepack enable && corepack prepare pnpm@9.15.5 --activate

WORKDIR /app

COPY ./BillNote_frontend/ ./
RUN pnpm install --no-frozen-lockfile --registry https://registry.npmmirror.com && \
    pnpm run build

# --- 阶段2：使用 nginx 作为静态服务器 ---
FROM ${NGINX_BASE_IMAGE}

RUN rm -rf /etc/nginx/conf.d/default.conf
COPY ./BillNote_frontend/deploy/default.conf /etc/nginx/conf.d/default.conf

# 拷贝构建产物
COPY --from=builder /app/dist /usr/share/nginx/html
