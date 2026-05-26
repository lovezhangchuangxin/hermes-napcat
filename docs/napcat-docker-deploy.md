# NapCat Docker 部署指南

服务器上推荐：

```text
NapCat Docker + Hermes 宿主机进程 + 反向 WebSocket
```

这样宿主机不需要手动安装 QQ 客户端。QQ/NTQQ 和 NapCat 运行在容器里，Hermes 只连接 NapCat 暴露的 OneBot HTTP/WebSocket。

你仍然需要一个 QQ 账号登录 NapCat。首次启动后进入 NapCat WebUI 扫码或按页面提示完成登录。

NapCat-Docker 官方项目：https://github.com/NapNeko/NapCat-Docker

## 启动 NapCat Docker

最小启动示例：

```bash
docker run -d \
  -e NAPCAT_GID=$(id -g) \
  -e NAPCAT_UID=$(id -u) \
  -p 3000:3000 \
  -p 3001:3001 \
  -p 6099:6099 \
  --name napcat \
  --restart=always \
  mlikiowa/napcat-docker:latest
```

端口用途：

- `6099`：NapCat WebUI，浏览器访问 `http://服务器IP:6099/webui`。
- `3000`：OneBot HTTP API，Hermes 的 `http_url` 通常填 `http://127.0.0.1:3000`。
- `3001`：OneBot WebSocket，正向模式下 Hermes 的 `ws_url` 通常填 `ws://127.0.0.1:3001`。

注意：`6099` 是 NapCat WebUI 端口，不要再拿它作为 Hermes 反向 WebSocket 监听端口。本文反向 WebSocket 示例使用 `8765`。

## 安全建议

- 不要把 `6099` WebUI 直接暴露到公网，至少应加防火墙或反向代理鉴权。
- 首次启动后修改 NapCat WebUI 默认 token。
- 如果配置 OneBot access token，Hermes 和 NapCat 必须使用同一个 token。

## 反向 WebSocket 与 Docker 网络

推荐服务器 Docker 场景使用反向 WebSocket。

Hermes 配置示例：

```yaml
napcat:
  enabled: true
  mode: reverse
  reverse_host: "0.0.0.0"            # Hermes 监听所有网卡，便于 NapCat 容器访问
  reverse_port: 8765                 # 不要和 NapCat WebUI 的 6099 冲突
  reverse_path: "/onebot/v11/ws"
  http_url: "http://127.0.0.1:3000"  # 推荐：NapCat HTTP 地址
  access_token: ""                   # 如果 NapCat 配了 token，这里填同一个
  self_id: "123456789"               # 机器人 QQ 号，建议填写
  require_mention: true
```

NapCat 侧开启：

- 反向 WebSocket 客户端：必需。
- HTTP 服务端：推荐。

NapCat 反向 WebSocket 地址填 Hermes 的可访问地址：

```text
ws://宿主机内网IP:8765/onebot/v11/ws
```

如果 Hermes 在宿主机、NapCat 在 Docker 里，不要在 NapCat 里填 `127.0.0.1`，因为那指的是容器自己。

查看宿主机内网 IP：

```bash
hostname -I
```

如果输出：

```text
10.5.0.12 172.17.0.1 172.18.0.1
```

通常选真实内网 IP `10.5.0.12`，而不是 Docker bridge 地址 `172.17.0.1` / `172.18.0.1`：

```text
ws://10.5.0.12:8765/onebot/v11/ws
```

如果 Hermes 和 NapCat 都跑在宿主机，反向 WebSocket 地址可以用：

```text
ws://127.0.0.1:8765/onebot/v11/ws
```

## 常见问题

### NapCat Docker 里 `127.0.0.1` 连不上 Hermes

NapCat 在 Docker 容器里时，容器内的 `127.0.0.1` 指容器自己，不是宿主机。

反向 WebSocket 应该这样配：

- Hermes：`reverse_host: "0.0.0.0"`
- NapCat：`ws://宿主机内网IP:8765/onebot/v11/ws`

