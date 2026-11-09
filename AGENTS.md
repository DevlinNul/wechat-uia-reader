# AGENTS.md

本文件面向在本仓库里工作的自动化代理和开发者，记录项目定位、设计原则、关键细节和修改约定。

## 1. 项目定位

Windows 桌面版微信 4.x 的 UI Automation 工具。

对下游暴露三个原子操作：

| 入口 | 作用 |
| --- | --- |
| `read_private_chat.py` | 读取当前私聊、聊天记录和 session 身份 |
| `fill_input.py` | 只把文本填入输入框，不发送 |
| `send_message.py` | 校验会话后发送，并尽量独立确认 |

另外：

- `wechat_uia.core.read_private_tags`：读取当前私聊联系人的标签；
- `python -m wechat_uia.core.list_surfaces`：列出主窗口/独立窗口；
- `python -m wechat_uia.core.get_*`：内部诊断。

默认行为偏保守：

- 不点击消息；
- 不切换会话；
- 不改变窗口状态；
- 只有显式参数才唤醒窗口；
- 只有 `fill_input` / `send_message` 会写输入框或发送。

## 2. 设计原则

1. **模块化**：每个模块只做一类事。
2. **原子化**：公开操作边界清晰；失败时尽量不留下半成品。
3. **副作用最小**：选择窗口只读；扫描只读；唤醒显式；点击有命中测试。
4. **职责清晰**：API 只编排，core 实现具体能力。
5. **单一信源**：同一逻辑只保留一份实现，不在多个文件复制。

## 3. 分层与单一信源

```text
根目录三个薄包装
  -> api/*                 只做编排：选窗口、唤醒、扫描一次、调用 core
  -> core/*
```

单一信源：

| 能力 | 唯一实现 |
| --- | --- |
| 完整控件树遍历 | `core/snapshot.scan_window()` |
| 窗口分类与选择 | `core/surface.resolve_surface()` |
| 会话类型判定 | `core/classify.classify_snapshot()` |
| 会话身份与比较 | `core/session.py` |
| 输入框读写 | `core/input.py` |
| 填入输入框 | `core/fill.fill_snapshot()` |
| 发送提交与确认 | `core/send.py` |
| 点击、聚焦、键盘 | `core/ui_actions.py` |
| 我/其他人方向判断 | `core/direction.py` |
| Win32 窗口、托盘、命中测试 | `core/get_hwnd.py` |
| UIA 常量、遍历、DPI | `core/uia_utils.py` |

硬约束：

- **只有 `core/snapshot.py` 导入 `uiautomation`**；
- `core/get_hwnd.py` 保持纯 Win32，不导入 UIA；
- `api/*` 不实现 UIA 遍历，也不放底层点击/键盘原语；
- 根目录三个入口只依赖各自的 `api` 模块。

`tests/test_structure.py` 会检查这些约束；新增模块时同步更新允许的依赖表。

## 4. 多窗口模型

微信 4.x 有三种会话窗口，角色只看 UIA 根类名：

| 角色 | UIA 根类名 | 说明 |
| --- | --- | --- |
| 主窗口 | `mmui::MainWindow` | 有会话列表 |
| 独立窗口 | `mmui::ChatSingleWindow` | 只承载一个会话 |
| 聊天记录窗口 | `mmui::SearchMsgUniqueChatWindow` | 只读 |

不要用 Win32 类名、窗口样式、标题或面积判断角色。独立窗口和主窗口的 Win32 样式完全一样。

### 同一会话的窗口关系

- 允许“主窗口 + 一个独立窗口”同时打开同一会话；
- 不允许两个独立窗口打开同一会话；
- 因此两个独立窗口同名时，一定是不同会话。

### 主窗口宽窄布局

主窗口有布局断点：

- 宽布局：有会话列表，可以拿到选中项的 AutomationId / RuntimeId；
- 窄布局：会话列表消失，只能比较名称、类型、成员数。

不要硬编码宽度。统一判断 `session_list` 是否存在。窄布局下强身份不可用，`session_check` 只能是 `name_only`。

### 聊天区容器

主窗口的聊天区容器是 `mmui::ChatDetailView`；独立窗口没有这个控件，回退到 `mmui::ChatMessagePage`（AutomationId `chat_message_page`）。`snapshot.chat_detail` 会优先保留 `ChatDetailView`，取不到时再使用 `ChatMessagePage`。

## 5. 窗口选择

`resolve_surface()` 的顺序：

1. 显式 `hwnd`；
2. `session` 中的窗口身份；
3. `chat_name` 匹配当前会话名；
4. 什么都没传时选择默认窗口：前台微信会话窗口 -> 正常主窗口 ->
   唯一的正常独立窗口。

主窗口最小化/隐藏时，不能因为它先出现就跳过旁边正常显示的独立窗口；多个独立窗口仍然要求显式指定。

同一会话出现在主窗口和独立窗口时：

- 传了 `session`：按来源窗口选，不跨窗口；
- 只传 `chat_name`：前台优先，其次正常主窗口，再其次正常独立窗口；
- 结果标记 `ambiguous` 和候选 HWND；
- `read_private_chat` / `send_message` 的 `strict` 多候选时直接报错。

找不到就报 `ConversationNotOpenError`，不自动切换会话。

## 6. 会话身份

| 目标 | 强身份 | 说明 |
| --- | --- | --- |
| 主窗口宽布局 | 选中项 `session_runtime_id` + `session_automation_id` | RuntimeId 只在本次微信运行内有效 |
| 主窗口窄布局 | 无 | 只能名称级校验 |
| 独立窗口 | 根 `window_automation_id` | 如 `ChatSingleWindowfilehelper` |

规则：

- 主窗口的 id 不能拿去验证独立窗口；
- 独立窗口的 id 不能拿去验证主窗口；
- 宽布局 session 遇到窄布局默认拒绝，除非显式允许布局降级；
- 标题控件 RuntimeId 不是会话身份，实测会被复用。

## 7. 输入框与发送

- 输入框每个窗口独立；消息内容共享；
- 选错窗口会写错草稿位置；
- 目标输入框非空时直接报 `InputNotEmptyError`，不覆盖、不追加、不换窗口；
- `send_message` 只操作一个窗口，绝不两边都发；
- 已经提交但没确认时，不要换一种方式重发，也不要直接重试。

## 8. 鼠标、键盘、唤醒

### 鼠标

所有模拟点击必须做命中测试：

- 底层唯一点击入口是 `get_hwnd._click_at`；
- `click_point` 点击前后都检查 `point_belongs_to_window`；
- `_click_screen` 必须传 `expected_classes`，不允许没有命中测试的点击路径；
- `ui_actions.click_control`、`focus_input`、发送按钮、标签读取都走 `click_point`。

不要用裸 `mouse_event` 做业务点击。

### 键盘

键盘事件集中在 `core/ui_actions.py`。键盘是全局输入，发送前必须确认目标窗口在前台。

### 唤醒

- 选择窗口阶段绝不唤醒；
- `scan_window` 只读，不唤醒；
- 唤醒显式通过 `require_window_ready(..., window_role=...)`；
- 托盘唤醒只属于主窗口；
- 独立窗口最小化只能 `--wake-minimized`；
- 独立窗口隐藏时 `--wake-tray` 不支持，直接报错；
- 屏幕外窗口不能被唤醒参数移动。

## 9. 消息方向判断

`read_private_chat --direction` / `with_direction=True` 会给文本消息标注：

```text
self / other / unknown
```

实现方式：

- UIA 直接给出 `mmui::ChatTextItemView` 的整行包围盒，不需要焦点；
- 每页只截消息列表一次；
- 只对文本消息行裁剪分析；
- 图片、文件、语音、卡片、时间分隔项一律 `unknown`；
- 不点击消息、不改变焦点；
- 不使用气泡颜色判断，避免深色/浅色主题影响；
- 使用 pywin32 的 `win32ui` 截图，纯 Python 分析，不依赖 Pillow / NumPy。

判定规则：

```text
所有有效非背景内容整体贴左 -> other
所有有效非背景内容整体贴右 -> self
判不出                     -> unknown
```

实现要点：

- 用整页消息列表左边缘采样背景色，不逐行从气泡旁边取色；
- 统计整行可见区域里的非背景列，过滤掉太窄的噪声/滚动条；
- 用所有有效区间的**整体跨度**判断左右留白，不要只看最长的一段；引用块、多个文本段会导致最长段落在中间；
- 使用相对左右留白，不要用固定绝对边距；窗口宽度变化时相对关系不变；
- 行只露出一小部分时先返回 `unknown`；
- 同一条消息在不同滚动页重复出现时，按内容身份聚合方向；只要有一页判出就采用，避免页边界处漏判。

不要用引用内容推测发送者；测试时可以用点击父链做 ground truth，但落地实现只能依赖视觉几何。

### XTextView 细节

消息文本是 `mmui::XTextView`，但它不在消息项的普通后代树里：

- 消息项 `GetChildren()` 为 0；
- 普通遍历看不到消息文本的 `XTextView`；
- 鼠标点击文本后，`GetFocusedControl()` 才返回它；
- 它的 `BoundingRectangle` 是紧贴文本的小矩形；
- 父链大致为：

```text
XTextView -> XVBoxView -> ChatBubbleFrame -> XHBoxView -> ... -> ChatTextItemView
```

点击父链可以判断方向，但每条消息都要点击，成本高、有焦点副作用，只作为显式回退。

键盘遍历不能进入 `XTextView`：方向键会在消息行、头像、时间分隔项和输入框之间移动。不要指望键盘遍历批量读取消息文本。

## 10. 开发与测试

设计点要多多征求用户意见，不要自己做主意。

终端统一使用 `python`，不要依赖 `py`：

```powershell
python -m unittest discover -s tests -t . -q
```

当前测试数：204。

测试要求：

- 纯逻辑测试不依赖真实微信；
- `tests/test_structure.py` 检查分层、单一信源、重复文件；
- 新增模块时同步更新允许的依赖表；
- 真机测试后恢复会话、输入框、窗口状态和前台窗口；
- 测试时只点击文本消息，绝不点击图片、语音、视频、文件、卡片等可能触发副作用的类型；
- 不发送真实消息，除非任务明确要求。

## 11. 提交约定

- 每次修改都提交 git；
- 提交信息用中文，描述做了什么；
- `.gitignore` 的提交信息固定写成 `Update .gitignore`，不要写具体改了什么；
- 保持工作区干净，不留下临时脚本、截图或调试文件。

## 12. 常见坑

- 不要再引入 `find_wechat_main_window` 或基于 Win32 样式猜主窗口；
- `window_kind()` 对未知类名返回 `other`，不要猜成 `main`；
- 不要用标题控件 RuntimeId 当会话身份；
- 不要硬编码主窗口宽窄断点；
- 不要把主窗口选中项 id 和独立窗口根 id 混用；
- 不要默认选中已最小化的主窗口，而忽略旁边正常显示的独立窗口；
- 不要给独立窗口承诺托盘唤醒，托盘图标只属于主窗口；
- 不要让 `scan_window` 承担唤醒副作用；
- 不要在 `api/*` 里实现 UIA 遍历或底层点击；
- 不要在 `snapshot.py` 之外导入 `uiautomation`；
- 不要用绝对气泡边距判断方向；
- 不要点击非文本消息来尝试判断方向；
- 不要为了让截图分析工作而引入不必要的重依赖。

## 13. 目录速查

```text
wechat_uia/
  api/             公开操作编排
  core/
    snapshot.py    唯一完整控件树遍历
    surface.py     窗口分类与选择
    classify.py    会话类型
    session.py     会话身份与比较
    fill.py        校验 + 填入
    send.py        提交 + 确认
    input.py       输入框读写
    input_text.py  文本规范化
    ui_actions.py  点击/聚焦/键盘/唤醒
    direction.py   截图判断我/其他人
    get_hwnd.py    Win32 窗口与托盘
    uia_utils.py   UIA 基础能力
    message_history.py 滚动历史
    get_*.py       诊断与读取
    read_private_tags.py 私聊标签
    list_surfaces.py 窗口诊断
```

## 14. 隐私与个人信息

仓库跟踪的文件里不得出现真实个人信息：

- 真实姓名、微信昵称、微信号、群名、群 ID；
- 真实聊天内容、截图、头像；
- 本机绝对路径、用户名；
- 真实 HWND、PID、RuntimeId、会话 ID；
- 真实手机号、邮箱、地址等。

示例和测试统一使用占位内容：

```text
张三 / 李四 / 某联系人 / 某群
文件传输助手
0x123456
[42, 123456, 4, -1]
```

调试日志和测试产物不要复制真实聊天记录。Git 提交者信息不在此限制内。

## 15. 修改检查清单

修改前：

- 先确认逻辑应该落在哪个模块；
- 检查是否已有单一实现可以复用；
- 检查是否影响窄布局、独立窗口、主窗口双开。

修改后：

- `python -m unittest discover -s tests -t . -q` 全绿；
- 更新 README / AGENTS.md（如果行为或约束变化）；
- 真机验证时只做必要操作，结束后恢复状态；
- 用中文提交，工作区保持干净。
