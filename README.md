# hermes-napcat

通过 NapCat 的 OneBot v11 网关，把 QQ 接入 Hermes Agent 的平台插件。

本插件会向 Hermes 注册一个名为 `napcat` 的 gateway platform，不需要修改
Hermes 核心代码。

## 已支持功能

- OneBot v11 正向 WebSocket 收发消息。
- 反向 WebSocket 模式：Hermes 监听端口，NapCat 主动连接。
- HTTP fallback：用于 `send_message`、cron 等独立进程发送消息。
- QQ 私聊。
- QQ 群聊，默认要求 @ 机器人后才触发，避免刷屏。
- OneBot 文本、`at`、`reply`、`face` 和入站图片消息段解析。
- Hermes 平台注册、ACL 环境变量、home channel、platform hint、standalone sender 钩子。

## 安装

Hermes 会从 `~/.hermes/plugins/<插件名>/` 加载用户插件，用户插件需要在
Hermes 配置里显式启用。

推荐直接把 GitHub 仓库克隆到 Hermes 插件目录：

```bash
mkdir -p ~/.hermes/plugins
git clone https://github.com/lovezhangchuangxin/hermes-napcat.git ~/.hermes/plugins/hermes-napcat
hermes plugins enable hermes-napcat
```

如果你的 Hermes 版本没有 `hermes plugins enable`，可以手动在
`~/.hermes/config.yaml` 中加入：

```yaml
plugins:
  enabled:
    - hermes-napcat
```

更新插件：

```bash
git -C ~/.hermes/plugins/hermes-napcat pull
hermes gateway restart
```

如果你是在本地开发，可以改用软链接：

```bash
mkdir -p ~/.hermes/plugins
ln -s /path/to/hermes-napcat ~/.hermes/plugins/hermes-napcat
hermes plugins enable hermes-napcat
```

## 配置建议

NapCat 端需要开启 OneBot v11。WebSocket 连接模式只需要选一种：

- `mode: forward`：Hermes 主动连接 NapCat。配置 `ws_url`。
- `mode: reverse`：Hermes 监听一个 WebSocket 地址，NapCat 主动连接 Hermes。配置 `reverse_host`、`reverse_port`、`reverse_path`。

不要把正向和反向同时接到同一个 Hermes 实例，否则同一条 QQ 消息可能会被处理两次。

`http_url` 不是收消息通道，它只用于发送补充。建议同时开启 NapCat HTTP 并配置
`http_url`，这样 cron、`send_message`、gateway 外独立发送等场景也能发 QQ 消息。

如果 NapCat 配置了 access token，Hermes 侧也要配置同一个 token。敏感信息建议放在
`~/.hermes/.env`：

```bash
NAPCAT_ACCESS_TOKEN=your-token
NAPCAT_SELF_ID=123456789
NAPCAT_ALLOWED_USERS=10001,10002
```

## 服务器部署推荐 Docker

服务器上推荐用 NapCat Docker。这样宿主机不需要手动安装 QQ 客户端，QQ/NTQQ 和
NapCat 运行在容器里；Hermes 只需要连接 NapCat 暴露出来的 OneBot HTTP/WebSocket。

你仍然需要一个 QQ 账号登录 NapCat。首次启动后进入 NapCat WebUI 扫码或按页面提示完成登录。

NapCat-Docker 官方项目：https://github.com/NapNeko/NapCat-Docker

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

NapCat Docker 端口用途：

- `6099`：NapCat WebUI，浏览器访问 `http://服务器IP:6099/webui`。
- `3000`：OneBot HTTP API，Hermes 的 `http_url` 通常填 `http://127.0.0.1:3000`。
- `3001`：OneBot WebSocket，正向模式下 Hermes 的 `ws_url` 通常填 `ws://127.0.0.1:3001`。

注意：`6099` 是 NapCat WebUI 端口，不要再拿它作为 Hermes 反向 WebSocket 监听端口。
下面的反向 WebSocket 示例使用 `8765`。

### Docker 场景地址怎么填

如果 Hermes 直接跑在宿主机上、NapCat 跑在 Docker 里：

- 正向 WebSocket 模式：Hermes 主动连 NapCat，Hermes 配置里可以用 `127.0.0.1`，因为 NapCat 端口映射到了宿主机。
- 反向 WebSocket 模式：NapCat 容器主动连 Hermes，NapCat 里不能填 `127.0.0.1`，因为那指的是容器自己。Hermes 应监听 `0.0.0.0`，NapCat 里填宿主机内网 IP。

查看宿主机内网 IP：

```bash
hostname -I
```

这时 NapCat 反向 WebSocket 地址填：

```text
ws://10.5.0.12:8765/onebot/v11/ws
```

如果 Hermes 和 NapCat 都跑宿主机，反向 WebSocket 可以用 `127.0.0.1`。如果 Hermes 在另一台机器或另一个容器，请填 NapCat 能访问到的 Hermes 地址，例如服务器内网 IP、Docker Compose 服务名或内网域名。

安全建议：

- 不要把 `6099` WebUI 直接暴露到公网，至少应加防火墙或反向代理鉴权。
- 首次启动后修改 NapCat WebUI 默认 token。
- 如果配置了 OneBot access token，Hermes 的 `access_token` 必须和 NapCat 里一致。

## 正向 WebSocket 模式

正向模式下，Hermes 主动连接 NapCat 的 WebSocket 地址。

NapCat 侧需要开启：

- 正向 WebSocket：必需，用于收消息和网关内发送。
- HTTP：推荐，用于 cron、`send_message` 等独立发送场景。

Hermes 配置示例：

```yaml
plugins:
  enabled:
    - hermes-napcat

napcat:
  enabled: true
  mode: forward
  ws_url: "ws://127.0.0.1:3001"      # 必填：NapCat 正向 WebSocket 地址
  http_url: "http://127.0.0.1:3000"  # 推荐：NapCat HTTP 地址
  access_token: ""                   # 如果 NapCat 配了 token，这里填同一个
  self_id: "123456789"               # 机器人 QQ 号，建议填写
  require_mention: true
```

`ws_url` 和 `http_url` 请改成你在 NapCat 里配置的实际地址和端口。

## 反向 WebSocket 模式

反向模式下，Hermes 会启动一个 WebSocket 服务，NapCat 主动连接这个服务。

Hermes 侧需要配置监听地址：

```yaml
plugins:
  enabled:
    - hermes-napcat

napcat:
  enabled: true
  mode: reverse
  reverse_host: "0.0.0.0"            # 必填：Docker NapCat 要连宿主机 Hermes 时用 0.0.0.0
  reverse_port: 8765                 # 必填：Hermes 监听端口，不要和 NapCat WebUI 的 6099 冲突
  reverse_path: "/onebot/v11/ws"     # 必填：Hermes WebSocket 路径
  http_url: "http://127.0.0.1:3000"  # 推荐：NapCat HTTP 地址
  access_token: ""                   # 如果 NapCat 配了 token，这里填同一个
  self_id: "123456789"               # 机器人 QQ 号，建议填写
  require_mention: true
```

然后把 NapCat 的反向 WebSocket 地址设置为 Hermes 的监听地址：

```text
ws://宿主机内网IP:8765/onebot/v11/ws
```

NapCat 侧需要开启：

- 反向 WebSocket：必需，地址填上面的 `ws://...`。
- HTTP：推荐，用于 cron、`send_message` 等独立发送场景。

如果 `hostname -I` 输出 `10.5.0.12 172.17.0.1 172.18.0.1`，通常填：

```text
ws://10.5.0.12:8765/onebot/v11/ws
```

### 反向 WebSocket 401 排查

如果 NapCat 日志里出现：

```text
Unexpected server response: 401
```

说明 Hermes 拒绝了 NapCat 的反向 WebSocket 连接。最常见原因是 Hermes 配置了
`access_token` 或 `NAPCAT_ACCESS_TOKEN`，但 NapCat 的反向 WebSocket 客户端没有带同一个 token。

处理方式二选一：

1. 在 NapCat 的反向 WebSocket 客户端配置里填写同一个 access token。
2. 或者把 token 放到反向 WebSocket URL 查询参数里：

```text
ws://10.5.0.12:8765/onebot/v11/ws?access_token=your-token
```

本地临时测试也可以不启用 token：删除或注释 `~/.hermes/.env` 里的
`NAPCAT_ACCESS_TOKEN`，并把 `config.yaml` 里的 `napcat.access_token` 留空，然后重启 Hermes gateway。

## 用户授权

Hermes gateway 的用户授权仍然生效。本地初次测试可以临时使用：

```yaml
napcat:
  allow_all_users: true
```

正式使用建议配置允许访问 Hermes 的 QQ 号：

```yaml
napcat:
  allow_from:
    - "10001"
    - "10002"
```

`allow_from` 里填的是 QQ 用户 ID。它同时适用于私聊和群聊里的发送者授权。

也可以用环境变量配置，适合放在 `~/.hermes/.env`：

```bash
NAPCAT_ALLOW_ALL_USERS=true
NAPCAT_ALLOWED_USERS=10001,10002
```

如果同时配置了环境变量和 `config.yaml`，环境变量优先。

## 群聊行为

群聊默认需要显式 @ 机器人后才会触发 Hermes。这是为了避免 Hermes 回复群里的每一条消息。

允许指定群不需要 @ 就能触发：

```yaml
napcat:
  free_response_groups:
    - "group:123456"
```

只允许指定群触发：

```yaml
napcat:
  allowed_groups:
    - "group:123456"
```

忽略指定群：

```yaml
napcat:
  ignored_groups:
    - "group:123456"
```

## Home Channel

Home Channel 是 Hermes 用来发送 cron 结果、跨平台消息和部分系统通知的默认目标。
如果没有配置，Hermes 在首次会话时会发送一条类似
`No home channel is set for Napcat` 的提示。这不是 NapCat 报错。

最简单的方式是在你希望作为默认目标的 QQ 私聊或群聊里发送：

```text
/sethome
```

也可以手动配置。建议使用带前缀的 chat ID，避免私聊和群聊 ID 歧义。

私聊示例：

```yaml
napcat:
  home_channel:
    chat_id: "private:2911331070"
    name: "QQ DM"
```

群聊示例：

```yaml
napcat:
  home_channel:
    chat_id: "group:123456"
    name: "QQ Home"
```

对应环境变量写法：

```bash
NAPCAT_HOME_CHANNEL=group:123456
NAPCAT_HOME_CHANNEL_NAME="QQ Home"
```

配置完成后重启 Hermes gateway：

```bash
hermes gateway restart
```

## 验证

```bash
/Users/keqing/.hermes/hermes-agent/venv/bin/python -m unittest discover -s tests -v
```

安装并配置完成后：

```bash
hermes gateway restart
hermes gateway status
```
