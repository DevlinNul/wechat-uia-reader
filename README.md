# 微信窗口定位

Windows 桌面版微信（4.x）的只读自动化脚本集：定位主窗口、当前会话与聊天区，判定会话类型，读取聊天输入框内容。

脚本不会发送消息、不会点击或切换会话，也不会主动唤起已最小化的微信窗口。窗口最小化或隐藏到托盘时，脚本会直接报错并提示，不会继续读取 UI 数据。

## 环境要求

- Windows 10/11
- Python 3.10+（使用 `X | None` 类型标注）
- 依赖：

```powershell
pip install -r requirements.txt
```

- 微信已登录，并打开过至少一个会话
- 微信的 UI Automation 控件树可被读取（若只能看到 Qt 外壳节点，需先开启系统“屏幕阅读器/辅助功能”再重启微信）

> UI Automation 的类名与 AutomationId 锚点基于微信 4.2 实测，微信版本升级后可能变化，需要相应调整。

## 脚本一览

| 文件 | 用途 |
| --- | --- |
| `get_hwnd.py` | 枚举微信窗口，定位主窗口句柄与窗口状态 |
| `get_chat.py` | 获取当前会话信息、聊天区位置，可选列出会话列表 |
| `get_chat_type.py` | 判定当前会话/视图类型，并给出是否可发送消息 |
| `get_input.py` | 读取当前聊天输入框内容，判断是否为空；不可发送时报错 |
| `get_messages.py` | 读取当前会话可获得的聊天记录（不滚动）；不判断会话类型 |

## 使用方法

### get_hwnd.py

```powershell
python get_hwnd.py
```

输出所有微信相关窗口，并给出主窗口句柄与状态：

```
主窗口句柄: 0x123456 (1193046)
窗口状态  : normal
```

窗口状态可能为 `normal`、`minimized`、`hidden`、`offscreen`。非 `normal` 时会在 stderr 报错并给出还原提示，退出码为 `2`，不会主动还原窗口。

也可作为库使用：

```python
from errors import WindowUnavailableError
from get_hwnd import require_wechat_main_window

try:
    hwnd = require_wechat_main_window()  # 定位并校验窗口状态
except WindowUnavailableError as exc:
    print("窗口不可用:", exc)
```

`find_wechat_main_window()` 只负责定位句柄（可含隐藏窗口），`require_wechat_main_window()` 和 `require_window_ready(hwnd)` 会在窗口未启动、最小化、隐藏或位于屏幕外时立即抛 `WindowUnavailableError`。

主窗口识别不依赖硬编码类名，而是按进程名（`WeChat.exe` / `Weixin.exe`）与窗口样式（可见标题栏、非工具窗口、可最小化/最大化等）综合判断。

### get_chat.py

```powershell
python get_chat.py             # 当前会话与聊天区
python get_chat.py --sessions  # 同时列出会话列表
```

输出示例：

```
主窗口句柄 : 0x123456 (1193046)  状态: 普通
聊天区句柄 : 无（Qt 子控件，归属于主窗口）
当前会话   : '某个会话'
聊天区位置 : (548, 66, 2004, 1446)  (物理像素，x,y,w,h)
  → 逻辑坐标: (274, 33, 1002, 723)
消息列表   : (548, 162, 2004, 1228)
```

微信 4.x 使用 Qt 单 HWND 渲染，聊天区没有独立窗口句柄，因此用“主窗口句柄 + UI Automation 控件矩形”描述。UIA 坐标是物理像素，脚本同时给出按屏幕 DPI 换算后的逻辑坐标。所有对外矩形统一为 `(x, y, width, height)`。

处于列表页（折叠的聊天、服务号等）时，会提示当前是列表页而非聊天区。

作为库使用时，`get_chat_info()` 只返回纯数据（不包含 UIA 控件对象）；列表页会抛 `NotAConversationError`。会话列表用 `get_sessions()` 获取。

### get_chat_type.py

```powershell
python get_chat_type.py
```

输出示例：

```
当前会话 : '某个群聊'
类型     : 群聊 (group)
可发送   : 是
成员数   : 26
判定依据 : 标题栏显示群成员数 (26)
```

类型与可发送性：

| 类型 | 含义 | 可发送 |
| --- | --- | --- |
| `private` | 私聊 | 是 |
| `group` | 群聊 | 是 |
| `official` | 公众号/服务号会话（有输入框，可发送） | 是 |
| `broadcast` | 公众号/服务号推送（无输入框，如消息通知） | 否 |
| `notice` | 服务通知等单聊样式、但没有输入框的会话 | 否 |
| `feed` | 公众号消息聚合页（文章列表，不是会话） | 否 |
| `list` | 列表页：折叠的聊天、服务号、服务号子列表 | 否 |
| `unknown` | 无法判定 | 否 |

判定要点：

- 可发送 = 存在聊天输入框（`mmui::ChatInputView` / `ChatInputField`）
- 群聊 = 标题栏中“名称”和“成员数”是两个独立文本控件；私聊只有“名称”一个控件，因此名称为 `(1)`、`(2)` 的私聊不会被误判
- 公众号/服务号会话（`official`）= 标题栏为 `ChatBrandView` 且有输入框
- 公众号推送（`broadcast`）= 标题栏为 `ChatBrandView` 但没有输入框
- 服务通知（`notice`）= 标题栏为单聊视图，但没有输入框
- 公众号聚合页（`feed`）= 显示文章列表的 `ChatBrandDisorderPage`，不是会话
- 列表页（`list`）= 带“返回”按钮的列表，列表名即页面标题

### get_input.py

```powershell
python get_input.py
```

可发送时输出输入框内容并判断是否为空：

```
会话     : '某个私聊'
类型     : 私聊 (private)
可发送   : 是
输入内容 : '你好'
是否为空 : 否
```

不可发送时向 stderr 报错，并以退出码 `2` 结束：

```
错误: 当前会话不可发送消息：'某公众号'（公众号推送 / broadcast）
```

微信窗口未启动、最小化或隐藏到托盘时同样报错并以 `2` 退出，例如：

```
错误: 微信窗口已隐藏到托盘，请手动还原后再运行（本脚本不会主动唤起窗口）
```

空白字符视为空内容。脚本只读取输入框，不会修改或发送。

### get_messages.py

```powershell
python get_messages.py
```

不判断会话类型，读取消息列表**当前已暴露**的消息项，不滚动、不加载历史：

```
消息条数 : 3（未滚动，仅当前可获得）
[ 0] time   '10:24'
[ 1] text   '你好'
[ 2] image  '图片'
```

消息项的 `kind` 可能为：

| kind | 含义 |
| --- | --- |
| `time` | 时间分隔 |
| `text` | 文本消息 |
| `bubble` | 文件、链接、卡片等气泡 |
| `image` | 图片 |
| `voice` | 语音 |
| `app` | 小程序、引用等应用卡片 |
| `other` | 其他未识别项 |

无法定位消息列表时在 stderr 报运行时错误，并以退出码 `2` 退出：

```
错误: 未找到消息列表，无法读取聊天记录
```

消息列表存在但没有消息项时返回 `count: 0`、`messages: []`。由于不滚动，返回条数取决于 UIA 当前暴露的消息项，可能少于完整历史；消息内容取自控件名，不额外解析发送者与引用结构。

## 作为库调用

```python
from errors import CannotSendError, WindowUnavailableError
from get_chat_type import get_current_chat_type
from get_input import get_input_state

try:
    info = get_current_chat_type()   # 窗口不可用时抛 WindowUnavailableError
    print(info["type"], info["can_send"])

    state = get_input_state()        # 不可发送时抛 CannotSendError
    print(state["text"], state["is_empty"])
except WindowUnavailableError as exc:
    print("窗口不可用:", exc)
except CannotSendError as exc:
    print("不能发送:", exc)
```

窗口不可用时，校验会在调用链最前端抛出 `WindowUnavailableError`，下游函数不会继续执行，也不会返回半成品数据。

读取当前消息列表：

```python
from errors import MessageListUnavailableError, WindowUnavailableError
from get_messages import get_messages

try:
    data = get_messages()   # 不判断会话类型
    print(data["count"])
    for msg in data["messages"]:
        print(msg["kind"], msg["text"])
except WindowUnavailableError as exc:
    print("窗口不可用:", exc)
except MessageListUnavailableError as exc:
    print("无法读取消息:", exc)
```

## 行为约定

- 只读：不点击、不切换会话、不发送消息、不修改输入框
- 最小化/隐藏到托盘/屏幕外的微信：立即报错并提示，退出码 `2`，不主动唤起窗口
- early panic：窗口不可用时在入口处抛 `WindowUnavailableError`，调用链下游不会继续执行
- 一次读取只遍历一次控件树：类型、输入框、消息都消费同一份快照，避免结果来自不同时刻
- 所有矩形统一为 `(x, y, width, height)`
- 异常都继承自 `WeChatUIAError`：`WindowUnavailableError`、`NotAConversationError`、`CannotSendError`、`MessageListUnavailableError`
- UIA 坐标为物理像素，高 DPI 环境下与常规逻辑坐标不同
- 依赖微信 UI 的类名与 AutomationId，微信升级后通常只需修改 `uia_utils.py` 中的锚点

## 已知限制

- 仅在 Windows 桌面版微信 4.x 验证
- 需要微信已登录且打开过会话
- 公众号推送、服务通知、公众号聚合页、列表页等不可发送视图无法读取输入内容
- `get_messages.py` 不滚动列表，只返回当前已暴露的消息项，不保证完整历史

## 测试

纯逻辑测试不需要真实微信窗口：

```powershell
python -m unittest discover -s tests -t . -v
```

## 项目结构

```
errors.py          # 统一异常族（不依赖第三方库）
get_hwnd.py        # L0 窗口与主窗口句柄（纯 win32）
uia_utils.py       # L1 UIA 基础能力与锚点常量
snapshot.py        # L2 一次遍历生成窗口快照（唯一导入 uiautomation 的模块）
get_chat.py        # L3 会话与聊天区定位
get_chat_type.py   # L3 会话类型与可发送性
get_input.py       # L3 输入框内容读取
get_messages.py    # L3 当前消息列表读取（不判断会话类型）
tests/             # 类型判定、矩形约定与分层规则测试
requirements.txt   # 运行依赖
```

依赖方向固定为 `errors / get_hwnd / uia_utils → snapshot → 各读取模块`，读取模块之间不互相依赖（`get_input` 仅依赖 `get_chat_type` 的纯判定函数）。`tests/test_structure.py` 会在测试时检查这条规则。
