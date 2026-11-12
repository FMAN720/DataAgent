---
name: 数据分析助手工作台
description: 安静的深色分析对话，围绕问题、执行进展与可核对的证据展开
colors:
  paper: "#191a19"
  surface: "#222321"
  ink: "#eceee8"
  muted: "#a5aaa0"
  line: "#343731"
  accent: "#c3d2bd"
  accent-strong: "#d0dfc9"
  accent-soft: "#283229"
  nav: "#141514"
  nav-active: "#2a2c28"
  nav-muted: "#b1b5ac"
  danger: "#f09d93"
  warning: "#ddc38d"
  focus: "#b4d4b9"
  field-border: "#4b5047"
  composer-border: "#454a40"
  composer-focus: "#84947a"
  table-header: "#292c27"
  table-hover: "#282b26"
  file-mark-surface: "#2a342c"
  status-ink: "#b9d8c0"
  status-surface: "#26352b"
  status-border: "#465e4a"
  error-surface: "#372724"
  error-ink: "#f2aea4"
  error-border: "#6a423b"
  pending-surface: "#342f23"
  pending-border: "#61553a"
  notice-border: "#3e5040"
  file-picker-surface: "#252824"
  file-picker-border: "#555e53"
  code-surface: "#242823"
  evidence-surface: "#28322b"
  evidence-border: "#465247"
  evidence-focus: "#354735"
  chart-secondary: "#a2ad9b"
  selection-surface: "#39523e"
  selection-ink: "#edf3e9"
  scrollbar: "#50564b"
  brand-mark: "#b4ccc1"
  welcome-mark: "#d4b498"
  new-analysis-surface: "#2c2f29"
  user-surface: "#2b2e28"
  new-analysis-hover: "#383d33"
  history-hover: "#252823"
  history-current: "#30352d"
  workspace-label: "#d1d7cb"
  context-ink: "#d2d8cc"
  disclosure-surface: "#2d322a"
  menu-surface: "#2c3029"
  suggestion-ink: "#d2d8cd"
  jump-border: "#51594a"
  jump-surface: "#2c3329"
  overlay-backdrop: "#00000080"
typography:
  display:
    fontFamily: '"Segoe UI", "Microsoft YaHei", "PingFang SC", system-ui, sans-serif'
    fontSize: "32px"
    fontWeight: 500
    lineHeight: 1.45
    letterSpacing: "-0.025em"
  headline:
    fontSize: "26px"
    fontWeight: 650
    lineHeight: 1.4
    letterSpacing: "-0.025em"
  title:
    fontSize: "16px"
    fontWeight: 700
    lineHeight: 1.5
  body:
    fontFamily: '"Segoe UI", "Microsoft YaHei", "PingFang SC", system-ui, sans-serif'
    fontSize: "15px"
    fontWeight: 400
    lineHeight: 1.65
  answer:
    fontSize: "16px"
    fontWeight: 400
    lineHeight: 1.9
  composer:
    fontFamily: '"Segoe UI", "Microsoft YaHei", "PingFang SC", system-ui, sans-serif'
    fontSize: "16px"
    fontWeight: 400
    lineHeight: 1.75
  user-message:
    fontSize: "14px"
    fontWeight: 400
    lineHeight: 1.8
  button:
    fontSize: "15px"
    fontWeight: 600
    lineHeight: 1.65
  button-compact:
    fontSize: "13px"
    fontWeight: 600
    lineHeight: 1.65
  navigation:
    fontSize: "13px"
    fontWeight: 400
    lineHeight: 1.65
  history:
    fontSize: "12px"
    fontWeight: 400
    lineHeight: 1.65
  interface-copy:
    fontSize: "13px"
    lineHeight: 1.8
  label:
    fontSize: "12px"
  metadata:
    fontSize: "11px"
  fine:
    fontSize: "10px"
  mobile-fine:
    fontSize: "9px"
  assistant-label:
    fontSize: "12px"
    fontWeight: 500
    lineHeight: 1.5
  guide-title:
    fontSize: "22px"
    fontWeight: 700
    lineHeight: 1.5
  guide-body:
    fontSize: "15px"
    lineHeight: 1.9
  answer-heading:
    fontSize: "12px"
    fontWeight: 500
    lineHeight: 1.5
  answer-subheading:
    fontSize: "16px"
    fontWeight: 650
    lineHeight: 1.5
  answer-minor-heading:
    fontSize: "17px"
    fontWeight: 650
    lineHeight: 1.5
  code:
    fontFamily: 'Consolas, "Microsoft YaHei", monospace'
    fontSize: "11px"
    lineHeight: 1.8
  answer-code:
    fontFamily: 'Consolas, "Cascadia Code", monospace'
    fontSize: "13px"
    lineHeight: 1.7
  evidence:
    fontFamily: 'Consolas, "Microsoft YaHei", monospace'
    fontSize: "12px"
    lineHeight: 1.5
rounded:
  focus-target: "3px"
  inline-code: "4px"
  status: "5px"
  control: "8px"
  send: "9px"
  message: "12px"
  panel: "14px"
  composer-field: "15px"
  guide: "16px"
  composer: "18px"
  composer-top: "18px 18px 0 0"
  composer-bottom: "0 0 16px 16px"
spacing:
  tight: "4px"
  close: "6px"
  compact: "8px"
  control-gap: "10px"
  small: "12px"
  medium: "16px"
  reading-inset: "18px"
  content: "20px"
  mobile-panel: "22px"
  panel: "24px"
  guide: "26px"
  section: "28px"
  section-gap: "30px"
  generous: "32px"
components:
  button-primary:
    backgroundColor: "{colors.accent}"
    textColor: "{colors.surface}"
    typography: "{typography.button}"
    rounded: "{rounded.control}"
    padding: "10px 16px"
  button-primary-hover:
    backgroundColor: "{colors.accent-strong}"
    textColor: "{colors.surface}"
  button-secondary:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    typography: "{typography.button}"
    rounded: "{rounded.control}"
    padding: "10px 16px"
  button-quiet:
    backgroundColor: "transparent"
    textColor: "{colors.muted}"
    typography: "{typography.button}"
    rounded: "{rounded.control}"
    padding: "8px 10px"
  button-default-hover:
    backgroundColor: "{colors.accent-soft}"
    textColor: "{colors.accent-strong}"
  button-send:
    backgroundColor: "{colors.accent}"
    textColor: "{colors.surface}"
    typography: "{typography.button-compact}"
    rounded: "{rounded.send}"
    padding: "7px 12px"
  field:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.control}"
    padding: "10px 12px"
  navigation:
    backgroundColor: "{colors.nav}"
    textColor: "{colors.nav-muted}"
    width: "264px"
    padding: "20px 14px 14px"
  navigation-narrow:
    width: "236px"
  navigation-wide:
    width: "280px"
  navigation-drawer:
    width: "min(280px, calc(100vw - 52px))"
    padding: "20px 14px 18px"
  navigation-item:
    backgroundColor: "transparent"
    textColor: "{colors.nav-muted}"
    typography: "{typography.navigation}"
    rounded: "{rounded.control}"
    padding: "9px 12px"
  navigation-active:
    backgroundColor: "{colors.nav-active}"
    textColor: "{colors.ink}"
  history-item:
    backgroundColor: "transparent"
    textColor: "{colors.nav-muted}"
    typography: "{typography.history}"
    rounded: "{rounded.control}"
    padding: "10px 11px"
  history-current:
    backgroundColor: "{colors.history-current}"
    textColor: "{colors.ink}"
  composer-measure:
    width: "min(100%, 760px)"
  composer-measure-wide:
    width: "min(100%, 800px)"
  question-composer:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    typography: "{typography.composer}"
    rounded: "{rounded.composer-top}"
    padding: "3px"
  composer-context:
    textColor: "{colors.context-ink}"
    rounded: "{rounded.composer-bottom}"
    padding: "5px 10px"
  topbar:
    height: "58px"
    padding: "0 22px"
  topbar-mobile:
    height: "52px"
    padding: "0 12px"
  welcome:
    textColor: "{colors.ink}"
    typography: "{typography.display}"
    padding: "32px 0 30px"
  user-message:
    backgroundColor: "{colors.user-surface}"
    textColor: "{colors.ink}"
    typography: "{typography.user-message}"
    rounded: "{rounded.message}"
    padding: "11px 17px"
  assistant-answer:
    textColor: "{colors.ink}"
    typography: "{typography.answer}"
  analysis-details-button:
    backgroundColor: "transparent"
    textColor: "{colors.muted}"
    typography: "{typography.metadata}"
    rounded: "{rounded.control}"
    padding: "4px 8px"
  analysis-inspector:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    padding: "24px"
    width: "440px"
  analysis-inspector-overlay:
    width: "min(480px, 100vw)"
  analysis-inspector-mobile:
    padding: "24px 22px"
  analysis-report:
    textColor: "{colors.ink}"
    typography: "{typography.interface-copy}"
  model-settings-dialog:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.guide}"
    padding: "0"
    width: "min(560px, calc(100vw - 32px))"
  model-settings-dialog-mobile:
    width: "calc(100vw - 24px)"
  live-activity:
    textColor: "{colors.muted}"
    typography: "{typography.history}"
  source-menu:
    backgroundColor: "{colors.menu-surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.message}"
    padding: "8px"
    width: "min(286px, calc(100vw - 50px))"
  beginner-guide:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.guide}"
    padding: "26px"
    width: "min(440px, calc(100vw - 32px))"
  status:
    backgroundColor: "{colors.status-surface}"
    textColor: "{colors.status-ink}"
    rounded: "{rounded.status}"
    padding: "4px 8px"
  status-failed:
    backgroundColor: "{colors.error-surface}"
    textColor: "{colors.danger}"
  status-pending:
    backgroundColor: "{colors.pending-surface}"
    textColor: "{colors.warning}"
  upload-panel:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.panel}"
    padding: "24px"
  file-picker:
    backgroundColor: "{colors.file-picker-surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.control}"
    padding: "32px 18px"
  evidence-link:
    backgroundColor: "{colors.evidence-surface}"
    textColor: "{colors.accent-strong}"
    typography: "{typography.evidence}"
    rounded: "{rounded.control}"
    padding: "8px 11px"
---

# Design System: 数据分析助手工作台

## Overview

**Creative North Star: "安静的分析对话"**

炭黑侧栏、暖深灰画布与略高一层的输入区构成安静的工作空间。浅绿集中在发送、选中、定位与真实状态；正文保持明亮中性，界面说明退后。系统无衬线字体、细线和克制的圆角服务持续阅读与操作，数据柱形标志保留产品识别。

本文件记录 2026-11-04 已实现并经独立审阅的深色界面，取代此前的浅色记录。视觉数值来自 [workspace.css](app/web/workspace.css)，结构与行为来自 [index.html](app/web/index.html)、[workspace.js](app/web/workspace.js) 和 [markdown.js](app/web/markdown.js)。[workspace surface brief](design/workspace.md) 保存首页与会话的组合策略及用户参考；参考用于布局方向，不复制 Claude 品牌。原生 HTML/CSS/JavaScript、系统字体、无新增框架/CDN/远程字体的约束见 [PRODUCT.md](PRODUCT.md)。

**Key Characteristics:**

- 深色哑光分层，浅绿动作，暖色欢迎标志；页面保持安静。
- 问题与回答拥有主要阅读尺度，历史、进展和资料准备保持可达。
- 答案直接阅读，执行、审查、来源和 E 编号在按需打开的分析详情中可核对；持续输入与滚动阅读互不打断。

## Colors

前置令牌保留源码的十六进制值；根自定义属性沿用原名，局部共用色按实际用途命名。只有一组主要操作色，失败与等待色承担语义。

### Primary

- **浅叶绿：** accent 承担发送、主要操作、图表主序列和执行指示；accent-strong 用于主按钮悬停、链接与证据文字；accent-soft 用于轻按钮悬停、提示和引用操作底色。
- **定位绿：** focus 是键盘轮廓，composer-focus 是提问区获得焦点时的边框。brand-mark 也用于在线与助手身份小标记，不代表所有分析均已成功。
- **选择绿：** selection-surface 与 selection-ink 成对用于文本选区；输入插入符使用 accent。

### Neutral

- **炭黑边界与暖灰画布：** nav、paper、surface 建立侧栏、主区与输入/面板层次。nav-active 表达入口选中或悬停；nav-muted 是侧栏默认文字。table-header 同时用于侧栏右侧细边界。
- **明亮正文与辅助灰：** ink 用于正文，muted 用于说明与摘要；workspace-label、context-ink、suggestion-ink 保留各自较轻的界面文字色。
- **字段与数据表面：** line 是常规分隔线；field-border、composer-border 分别界定普通字段与提问区。table-hover、file-mark-surface、file-picker-surface / file-picker-border、code-surface 承担列表悬停、文件标签、上传选择区和代码底色。
- **会话与历史：** new-analysis-surface / new-analysis-hover 用于新建按钮；user-surface 是用户消息的深灰底色。history-hover / history-current 区分历史悬停与当前会话。
- **可核对来源：** evidence-surface / evidence-border 是 E 编号按钮；evidence-focus 是定位到工具记录时短暂出现的底色。chart-secondary 区分图表对比序列。
- **语义状态：** status-ink / status-surface / status-border 表示资料可用；danger 与 error-* 表达失败及错误提示；warning 与 pending-* 表达排队、导入或清理。notice-border 配合常规说明。每一类保留状态文字。
- **浮动层与细节：** menu-surface、disclosure-surface、jump-surface / jump-border 区分添加数据菜单、费用说明和回到最新按钮；overlay-backdrop 复用于移动侧栏遮罩与原生 API 设置对话框背景；scrollbar 是细滚动条。welcome-mark 是欢迎区的暖砂色数据标志。

**The 文字可核对 Rule.** 状态、连接、审查和错误保留明确文字；颜色只加强识别，执行指示不能冒充成功。

## Typography

**Display Font / Body Font:** Segoe UI，依次回退到 Microsoft YaHei、PingFang SC、系统无衬线。根字号为 body，按钮与字段继承原生字体栈，不加载远程字体。

**Label/Mono Font:** 普通界面标签继续使用无衬线。工具记录和证据编号使用 Consolas / Microsoft YaHei；回答中的代码使用 Consolas / Cascadia Code。

### Hierarchy

| 用途 | 源码中的实际层级 |
| --- | --- |
| 欢迎标题 | display；桌面 32px，≤900px 为 29px，≤600px 为 26px；字重 500，行高 1.45。短窗口的另行覆盖见 Layout。 |
| 数据入口页面标题 | headline；≤600px 为 23px。分析对话视图隐藏重复页面标题，仅保留轻量会话顶栏。 |
| 回答正文 / 提问字段 | answer / composer 均为 16px；分别采用 1.9 / 1.75 行高。 |
| 用户消息 | user-message；14px、1.8 行高。 |
| 回答标题 | Markdown 一级标题从语义 h3 开始。按当前会话中的最终 CSS 级联，answer-heading / answer-subheading / answer-minor-heading 分别为 h3 的 12px / 500、h4 的 16px / 650、h5–h6 的 17px / 650，行高均为 1.5；h3 延续 muted 文字。 |
| 普通界面 | body 为 15px；导航为 13px，历史及执行摘要为 12px；普通字段为 13px。 |
| 辅助信息 | label / metadata / fine 为 12 / 11 / 10px；移动端保存/费用说明等最小层级为 mobile-fine（9px），不用于回答或提问正文。 |
| 助手身份 / 欢迎副文 | assistant-label 为 12px、500；欢迎副文桌面 13px，≤600px 为 12px。 |
| 按钮 | 普通按钮 15px、600；发送 13px，≤600px 为 12px。顶栏、数据入口和上下文条有更紧凑的原生控件规格。 |
| 指南 | 标题 guide-title；步骤标题以外的正文 guide-body。指南页头为 18px。 |
| 代码 / 证据 | code 是工具数据块；answer-code 是回答围栏代码。回答行内代码相对正文为 .88em；evidence 用于 E 编号按钮。 |

数字表格、图表标注和指南步骤采用等宽数字特性。回答段落与检索片段保留适当阅读宽度；普通消息段落上限 75ch，整体会话受组件宽度约束。字号是按用途建立的层级，没有统一倍率。

**The 等宽有用途 Rule.** 等宽字体用于代码、数据证据与编号；问题、回答和操作保持系统无衬线阅读节奏。

## Layout

当前页面组合以 [design/workspace.md](design/workspace.md) 为准。根应用占满可用视口（100vh 回退至 100dvh），文档本身不滚动。桌面使用侧栏与弹性主区；数据详情打开时增加一列。组件令牌中的 composer-measure 与 composer-measure-wide 是源码 width:100% 加 max-width 的等价宽度表达。

| 条件 | 实际响应规则 |
| --- | --- |
| 默认桌面 | 侧栏 264px；顶栏高 58px、左右 22px。主区数据页面内边距 30px 38px 40px；对话首页画布左右 32px。提问区与会话上限 760px，示例上限 700px。 |
| ≥1800px | 侧栏 280px；提问区与会话上限 800px。已确认条件和历史说明仍各自上限 760px。 |
| ≤1380px（分析详情） | 分析详情从桌面 440px 列变为右侧固定覆盖层，宽 min(480px, 100vw)、高 100dvh；主区不为它保留列。此规则只适用于分析详情。 |
| ≤1180px | 侧栏 236px；首页画布左右 28px；隐藏“只读分析”辅助标签。通用数据/来源详情变为右侧固定层，宽 min(420px, 100vw)、高 100dvh。 |
| ≤900px | 应用变为单列，分析详情仍使用上述覆盖层。侧栏成为默认关闭的固定抽屉，宽 min(280px, calc(100vw - 52px))；开启后主区 inert，外侧遮罩可关闭，Tab 在抽屉控件内循环，Escape 关闭并回到开关。顶栏左右 18px；数据页内边距 24px。 |
| ≤600px | 顶栏高 52px、左右 12px；会话标题上限 35vw 并截断。服务状态视觉上只保留圆点，其文字仍在 DOM。数据页内边距 22px 18px 30px；首页画布左右 18px。隐藏快捷键文字，保留实际 Ctrl / ⌘ + Enter 行为。 |
| ≤430px | 查询行间距 7px，查询按钮内边距 9px 12px；表单标签弹性基准 130px。 |
| 高度≤700px 且宽度≥601px | 欢迎标题 27px、欢迎区上下 18px，标志 30px；欢迎上方弹性区基准 125px，下方留白与示例间距缩小。此规则位于宽度规则之后。 |

首页用弹性留白将欢迎区与提问框置于主要视线范围，内容过高时首页画布可滚动。进入会话后欢迎区与示例隐藏；消息区独立纵向滚动，底部提问区留在同一应用视口内。会话消息区内边距为 12px 32px 24px，底部提问区宽 calc(100% - 64px)，上下内边距 8px / 12px。≤600px 时分别为 12px 18px 20px 与 calc(100% - 28px)、6px / 9px。用户消息靠右、最多 85% 宽，移动端最多 90%。

通用数据/来源详情保留桌面 380px 列及≤1180px 的 420px 上限；分析详情另用 analysis-inspector 的 440px 列及≤1380px 的 480px 上限。两者内容自行滚动，≤900px 保留 24px 22px 内边距。API 设置对话框桌面宽度及移动宽度见 model-settings-dialog 两个变体，最大高度分别为 calc(100dvh - 48px) 与≤600px 的 calc(100dvh - 24px)，内部纵向滚动。数据表在≤900px 保持 570px 最小宽度并在自身容器横向滚动；分析图最小宽度为 600px。图片核对在≤900px 由双列变为单列。不要让这些内容撑宽文档。

## Elevation & Depth

界面以哑光明度差和细边界建立层级。侧栏、会话、用户消息和上传面板没有投影；阴影用于浮动操作、定位按钮、指南与固定详情。保留源码的柔和黑色透明投影，不增加装饰渐变。

### Shadow Vocabulary

| 用途 | 实际 box-shadow |
| --- | --- |
| 添加数据菜单 | 0 12px 40px #00000060 |
| 数据与费用说明 | 0 12px 32px #00000050 |
| 回到最新 | 0 6px 18px #00000030 |
| 固定数据/来源详情（≤1180px）及分析详情（≤1380px） | -12px 0 36px #00000040 |
| 新手指南 | 0 16px 48px #00000070 |

层级顺序为回到最新（z-index 4）、费用说明（8）、添加数据（12）、固定详情（14）、桌面指南（15）、移动遮罩（23）、侧栏（24）、移动指南（28）。API 设置使用原生 dialog 的顶层模态与 backdrop，不通过更大的 z-index 模拟模态。

**The 平面分层 Rule.** 用明度、细线与布局关系组织内容；阴影只帮助识别浮动层和回到最新操作。

## Shapes

普通控件使用 control 圆角，状态标签使用 status，上传面板使用 panel。用户消息、菜单与费用说明使用 message；指南与 API 设置使用 guide 的 16px 圆角。提问区由 composer-top 和 composer-bottom 上下连接，边框连续；内部文本框使用 composer-field，发送按钮使用 send。表格外框为 control，行内代码为 inline-code，证据焦点目标为 focus-target。小状态点和指南步骤编号是圆形。

边界通常为 1px，文件选择区使用虚线。图标为源码内联线性 SVG：默认 20px、1.7px 描边、圆线端与连接；各控件采用源码的 13–19px 尺寸，侧栏数据标志为 28×32px、2.5px 描边，欢迎标志为 40px、2.3px 描边（≤600px 为 34px）。没有新增交付用栅格图片；QA 截图仅为证据。

## Components

### Buttons

主要操作用浅叶绿底与 surface 深色字；次按钮用 surface、field-border 和 ink；轻按钮透明底、muted 文字。普通按钮最小高度 42px、600 字重，使用前置 padding；悬停过渡为 background / color 各 160ms ease。主按钮悬停改用 accent-strong；其他按钮默认悬停使用 accent-soft 与 accent-strong。导航和历史有各自的覆盖值。

普通可交互控件的 focus-visible 是 3px focus 轮廓、外移 3px；disabled 为 opacity .55 且光标 not-allowed。图标按钮最小 34×34px、内边距 7px。发送最小高 36px（移动端 35px）；执行时文字变为“停止分析”，同一控件中止当前请求，不清掉用户正在填写的后续草稿。运行中禁用新建分析；恢复历史时暂时禁用发送。

### Inputs / Fields

普通字段为 field，字号 13px、最小高 42px，field-border 描边，placeholder 使用 muted，caret 使用 accent。提问字段另用 composer 字阶，透明底、无独立描边；焦点由整个提问区边框从 composer-border 切至 composer-focus 表达，因此文本框不叠加通用外轮廓。

提问区初始高 112px、最小 80px；会话中初始 84px、最小 68px。≤600px 的首页初始 104px，会话初始 70px、最小 62px。输入后按实际 scrollHeight 增高，上限 220px。Ctrl / ⌘ + Enter 与显式发送提交，普通 Enter 保留换行。

### Navigation

侧栏自上而下是标志、新建分析、四个真实入口、可折叠的最近分析，页脚依次是“API 设置”、本地工作空间说明与接口文档。API 设置入口为 12px，带 19px SVG 与 10px 配置摘要；未配置时摘要为“连接你自己的模型”，已配置时显示模型名称。其他入口最小高 39px；默认 nav-muted，悬停/选中是 nav-active 与 ink。历史项最小高 38px，单行省略，title 保留标题和时间；悬停使用 history-hover，当前会话使用 history-current。最近分析区域独立滚动，页脚不随列表挤出。

桌面可收起侧栏；移动抽屉遵循 Layout 的焦点、遮罩和 Escape 语义。恢复历史会切回对话并使用同一回答渲染器；历史快照保留“非当前最新数据”说明。主区的当前视图标记不充当导航点击目标。

### Conversation Composer & Thread

首页提问区下接“数据来源”上下文条；“添加数据”菜单向上打开，距触发位置 12px，宽度及颜色用 source-menu 令牌。菜单显示三项真实动作：表格、文档、图片/扫描件；打开时焦点到第一项，外点或 Escape 关闭，Escape 归还触发按钮。示例只填入字段，不自动提交。费用说明使用原生 details，展开内容宽 300px、最多 calc(100vw - 44px)。

用户消息使用 user-surface；它与 new-analysis-surface 接近但不同，不应合并。助手回答保持在画布上，无包裹卡片。回答使用本地 DOM Markdown：段落、列表、标题、表格和代码可阅读；原始 HTML 与图片语法保持文字，不加载远程图片，只允许显式 HTTP(S) 链接。

每条助手消息只呈现安全渲染的答案与标题行内的一个“分析详情”按钮；按钮为 analysis-details-button，最小高 28px、字重 400。运行中答案位置只保留简短的真实状态。执行列表、审查、E 编号、查询结果表、图表、来源、导出、保存时间与 SQL 示例操作均放在默认关闭的右侧分析详情，历史也用同一渲染路径；出现待核对信息时，按钮以 warning 小点和可访问名称提示。

执行记录由真实 SSE 的 start、plan、llm、tool、reflection、revision 类别生成。详情面板按需打开后，运行中的执行记录初始展开；工具失败保留“未完成”，最终事件、停止或连接失败后收为可展开摘要。审查状态与逐项依据始终位于详情，不因执行结束而显示为通过。运行圆点以 1.6s ease-in-out 无限脉冲，50% 时透明度 .35；减少动态效果时停用。

详情使用 analysis-report 的 13px / 1.8 界面阅读层级；原问题、当前会话条件与历史快照说明也在面板内。关闭详情后焦点回到原回答按钮，查看来源后可“返回分析详情”，新建分析关闭旧详情。保持原有查询、审查与导出能力，不将这些控件重新插入答案正文。

阅读上方消息时停止自动跟随；距底部小于 80px 恢复跟随，“回到最新”强制回到底部。定位按钮右侧 28px、距下方 210px；≤600px 为 18px / 188px。它不改变会话内容。

### Personal API Settings

未配置时，提问区上方用 12px 辅助文字与“配置 API”轻按钮提示；≤600px 为 11px。提交会保留问题草稿并打开设置，不创建分析消息或调用模型。原生 dialog 保护输入焦点，使用现有 surface、ink、composer-border、guide 圆角与 overlay-backdrop；不引入新配色。

设置页头内边距为 24px 26px 12px，标题 21px、副文 12px；表单为 0 26px 22px，字段标签 13px、输入 14px、说明 11px / 1.8，Key 与显示按钮之间为 8px。≤600px 时页头为 22px 20px 10px、标题 19px，表单为 0 20px 20px，输入为 16px。Key 默认遮盖，可主动显示；保存与关闭后清空输入，不回显已保存值。

“保存到本机”只写当前操作系统用户的本机配置；“测试连接”须主动点击，会发送不含业务数据的工具调用请求并可能计费。GET 只返回配置元数据，不含 Key；网页不将 Key 持久存入浏览器，未配置时不回退开发者环境 Key。运行中的分析保留其配置快照，修改从下一次分析生效。Windows 使用 DPAPI，POSIX 使用私密目录与 0600 文件；这是每个系统用户各自本机安装的边界，不是 SaaS 账号或租户隔离。显式 CLI `build_agent(settings)` 保留操作者环境配置；图片识别与 Embedding 使用各自本机的独立设置。Docker 仍仅发布 127.0.0.1、使用专用配置命名卷，并只显式允许受回环 Host 与同源约束的本地桥接访问。

### Status & Notices

资料状态采用 status 三色组合，并保留“可分析”“等待导入”“导入失败”等文字。常规说明使用 accent-soft、accent-strong 和 notice-border；错误说明使用 error-*。状态标签为 11px、4px 8px 内边距、1px 边界，不作为按钮。工具执行结束与审查通过是分别呈现的语义。

### Uploads / Data Containers

上传面板采用 upload-panel；≤900px 内边距为 20px 16px。文件选择区用 file-picker 与虚线边界，整块原生 file input 可点击，focus-within 使用通用焦点轮廓。表格先预览并核对字段后导入；来源、数值字符串与单位的意义不因视觉变更而改变。图片/扫描件先本地预览，再逐次主动同意外发并人工核对。界面保留这些真实步骤。

### Evidence & Charts

E 编号用 evidence-link、最小高度 38px、evidence-border；点击定位到对应工具记录，并以 2px focus 轮廓、6px 外移和 600ms ease-out 短暂底色提示。证据记录保留可展开原始数据；表格和图表可在自身区域横向滚动并获得键盘焦点。主序列为 accent，对比序列为 chart-secondary，负值为 danger，同时保留数值、图例与对应表格。

### Beginner Guide

指南是可收起的独立浮层，采用 beginner-guide，桌面上/右/下为 80px / 24px / 24px、最大高度 760px。≤600px 为上/下 16px、右 12px、宽 calc(100vw - 24px)、内边距 22px。中部内容自行滚动，页头和上/下一步持续可达。五步导航显示数字与当前步骤；各步动作连接现有入口、示例和证据。关闭后归还原触发位置，Escape 优先关闭指南。

全局 prefers-reduced-motion: reduce 关闭动画与过渡，并改用自动滚动；没有为面板开合新增动效。

## Do's and Don'ts

### Do:

- Do 从当前深色令牌延伸界面，保持侧栏、画布、输入区和浅绿动作的层级关系。
- Do 保留 16px 的问题与回答尺度、较小的真实界面层级，以及原生系统字体。
- Do 保留独立会话滚动、持续可见的追问输入、历史恢复和回到最新行为。
- Do 保留数值字符串、来源换行、状态文字、审查结果及 E 编号到记录的对应关系。
- Do 保留显式提交、外发同意、人工核对、合成演示标记、键盘焦点回程与减少动态效果支持。

### Don't:

- Don't 用颜色、脉冲或“执行结束”替代工具失败、审查未完成或数据限制的文字说明。
- Don't 在当前分析首页恢复已被用户否定的管理后台式内容堆叠，或把本页组合约束扩展为其他项目的品牌偏好。
- Don't 复制参考产品品牌，或添加未实现的模型切换、计划审批与任务调度控件。
- Don't 为后续界面引入前端框架、CDN、远程字体或图标运行时。
- Don't 让宽表格、图表或原始证据撑宽文档，也不要在用户阅读旧消息时强制跳回最新。
