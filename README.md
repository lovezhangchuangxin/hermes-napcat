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
  reverse_host: "127.0.0.1"          # 必填：Hermes 监听地址
  reverse_port: 6099                 # 必填：Hermes 监听端口
  reverse_path: "/onebot/v11/ws"     # 必填：Hermes WebSocket 路径
  http_url: "http://127.0.0.1:3000"  # 推荐：NapCat HTTP 地址
  access_token: ""                   # 如果 NapCat 配了 token，这里填同一个
  self_id: "123456789"               # 机器人 QQ 号，建议填写
  require_mention: true
```

然后把 NapCat 的反向 WebSocket 地址设置为 Hermes 的监听地址：

```text
ws://127.0.0.1:6099/onebot/v11/ws
```

NapCat 侧需要开启：

- 反向 WebSocket：必需，地址填上面的 `ws://...`。
- HTTP：推荐，用于 cron、`send_message` 等独立发送场景。

## 用户授权

Hermes gateway 的用户授权仍然生效。本地初次测试可以临时使用：

```bash
NAPCAT_ALLOW_ALL_USERS=true
```

正式使用建议配置允许访问 Hermes 的 QQ 号：

```bash
NAPCAT_ALLOWED_USERS=10001,10002
```

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

建议使用带前缀的 chat ID，避免私聊和群聊 ID 歧义：

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

## 验证

```bash
/Users/keqing/.hermes/hermes-agent/venv/bin/python -m unittest discover -s tests -v
```

安装并配置完成后：

```bash
hermes gateway restart
hermes gateway status
```
