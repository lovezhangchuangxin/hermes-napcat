# 仓库指南

## 项目结构与模块组织

本仓库是一个 Hermes Agent 平台插件，通过 NapCat 的 OneBot v11 网关接入 QQ。

- `adapter.py` 包含 NapCat 适配器、OneBot 解析、WebSocket 处理、HTTP action 和 Hermes 平台注册逻辑。
- `__init__.py` 导出 Hermes 插件入口。
- `plugin.yaml` 声明插件元数据和支持的环境变量。
- `tests/test_adapter.py` 包含 helper 行为、配置桥接、消息解析和发送 action 的单元测试。
- `README.md` 是面向用户的中文安装与部署指南。

除非某个模块已经大到需要拆分，否则运行时代码应保留在顶层插件文件中。

## 构建、测试与开发命令

本项目测试会导入 Hermes Agent 源码中的 `gateway.*` 模块。默认源码路径为 `~/.hermes/hermes-agent`；如果你的 Hermes 源码在其他位置，请通过 `HERMES_AGENT_PATH` 指定。

本地测试时，将插件安装或软链接到 Hermes：

```bash
mkdir -p ~/.hermes/plugins
ln -s /path/to/hermes-napcat ~/.hermes/plugins/hermes-napcat
hermes plugins enable hermes-napcat
```

运行测试套件：

```bash
HERMES_AGENT_PATH=~/.hermes/hermes-agent python -m unittest discover -s tests -v
```

修改插件或配置后重启 Hermes gateway：

```bash
hermes gateway restart
```

## 代码风格与命名约定

使用 Python 3 风格，缩进为 4 个空格。公共函数或逻辑较复杂的 helper 应添加类型标注，Python 模块中保留 `from __future__ import annotations`。OneBot 规范化、配置解析和传输逻辑优先拆成小 helper。常量使用 `UPPER_SNAKE_CASE`，内部 helper 使用 `_snake_case`，类名使用 `PascalCase`。

修改适配器行为时避免大范围重构。保持现有 YAML 配置和环境变量兼容性。

## 测试规范

测试使用标准库 `unittest` 框架。测试文件命名为 `test_*.py`，测试方法命名为 `test_*`。修改配置优先级、消息段解析、ACL 行为或 OneBot 出站 action 时，应补充聚焦的单元测试。网络 action 应使用 mock，不要求真实 NapCat 实例。

## 提交与 Pull Request 规范

Git 历史使用简短的祈使句提交标题，例如 `Improve reverse WebSocket setup docs` 或 `Support NapCat auth config in YAML`。每个提交应聚焦一个行为变更或文档变更。

Pull Request 应包含简要摘要、相关配置示例、测试结果，以及 Hermes 或 NapCat 版本兼容性说明。仅修改文档时，说明未运行测试即可。

## 安全与配置提示

不要提交 QQ 凭据、access token、`.env` 文件或私有服务器地址。文档中说明 token 用法时使用占位符。新增影响授权的功能时，应让 `allow_from` / `NAPCAT_ALLOWED_USERS` 行为保持明确且保守。
