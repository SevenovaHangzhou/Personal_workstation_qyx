# 原生课程内容包约定

状态：星序已接入 LearningService、结构验证器与固定原生 Qt 阅读器。当前后台生成支持下列文字、公式、表格和练习块；图片和任意交互组件尚不支持。结构通过后按草稿显示，仍需教学核查。

## 目录

```text
<generation-id>/
  course-package.json
  review.md
  assets/              # 仅在实际有静态图像资源时建立
```

`course-package.json` 是严格 JSON，不含可执行函数或代码插件。`review.md` 对应课程标准验收清单，列出实际验证的证据和未完成事项。

## 顶层字段

| 字段 | 约定 |
|---|---|
| `format` | 固定为 `momo-learning-course-draft/v1`，表示草稿内容包，不表示应用验收通过 |
| `generation_id` | 本次生成的稳定唯一 ID；重试复用，避免重复建库 |
| `input` | 新请求为 `mode=subject_curriculum`、`subject`、`task_id`、`topic_id` 及输入版本，保留请求原字段；不伪造个人目标或掌握程度。旧内容包仍可读取 |
| `sources` | 实际获得的来源记录，见下文 |
| `knowledge_units` | 知识单元及先修引用；同主题旧 ID 可复用，新单元不得碰撞 |
| `coverage` | 学习对象的知识覆盖清单及未补齐领域，引用已有知识单元 |
| `learning_path` | 从基础到进阶的有序阶段，每章恰好属于一个阶段 |
| `lessons` | 完整课程内容及练习；每课包含稳定 ID 和修订版本 |
| `task_suggestions` | 可选的任务草稿，不自动写入任务仓库，不把知识先修 ID 当成任务 ID |
| `unresolved` | 来源、输入、组件或验证方面的具体缺口；不能以空列表掩盖已发现的问题 |

## 来源与知识单元

每项来源记录 `id`、`title`、`author_or_institution`、`locator`、`version`、`section`、`access_status`。`locator` 可以是外部 URL 或本地路径。仅在真实获得访问时间时填写 `accessed_at`。

`access_status` 使用 `content_read`、`metadata_only` 或 `unavailable`；题录和搜索结果不得算作已读正文。记录检索词和关键结论的证据定位，不复制整本书作为知识库。

每个知识单元包含 `id`、`title`、`scope`、`prerequisite_ids`、`source_ids` 和 `claims`。关键 `claim` 提供结论、适用条件和来源定位。未核实结论列入 `unresolved`。

新生成的 `coverage` 为 `[{id,title,knowledge_unit_ids:[单元ID],gaps:[具体缺口]}]`。全部已建单元须出现在覆盖清单中；暂未完成的领域可以不含单元，但必须说明实际缺口。范围依据学习对象的先修、概念、方法、应用、边界及进阶方向组织，不伪称穷尽一切知识。

`learning_path` 为 `[{id,title,summary,lesson_ids:[章节ID]}]`。阶段按由浅入深排列，全部章节恰好出现一次。每章增加 `depends_on:[前置章节ID]`；空数组表示可独立开始。前置章节不能位于后续阶段，依赖不能成环。程序在阶段内按显式依赖排序，不从标题推断先后。

每个知识单元必须有课程承接；所需先修单元应安排到本章或显式前置章节。章节的 `goals` 是生成者拟定的教学能力目标，不是需要用户填写的个人目标。

## 课程与内容块

每课包含 `id`、`revision`、`knowledge_unit_ids`、`title`、`prerequisites`、`goals`、`scope`、`pace`、`sections`、`questions` 和 `source_ids`。建议节奏说明依据，不伪装成用户承诺的期限。

每个小节包含稳定 `id`、标题和 `blocks`。块类型建议如下；后续实现须通过注册表声明实际支持范围：

| 类型 | 内容及原生显示意图 |
|---|---|
| `paragraph` | 正文与概念解释，保留可复制文字 |
| `list` | 步骤或并列条目，顺序语义明确 |
| `formula` | 公式、符号释义、单位及成立条件；原文可复制 |
| `table` | 表头、行、单位及说明 |
| `code_example` | 展示用源码、语言、环境与预期结果；不是可自动执行指令 |
| `figure` | 本地资源或结构化图数据、图注、替代文字和教学问题 |
| `self_check` | 问题、提示与折叠参考思路 |
| `interactive` | 已实现组件的注册 ID、参数及观察指导；未实现时列入缺口 |
| `callout` | 易错点、适用边界或进阶选读 |

每个练习包含稳定 `id`、`kind`（`choice` 或 `written`）、`prompt`、所检验目标、提示、参考过程、自查点和来源引用。选择题还提供选项与正确选项 ID。答案和用户作答分开存储；生成器不得写入用户作答。

正文完整性、来源准确性、可解释性和答案正确性仍按课程知识标准人工／模型复核。字段齐全不代表课程达标。

## 当前运行接口

应用的「创建课程」仅需学习对象（默认取任务名称），使用系统 Codex CLI 后台先建立知识库，再组织路径与编写课程；不询问用户掌握程度或目标，不更换认证、模型或配置目录。CLI 最终结构为 `{status,message,course_json,review}`，`status` 为 `review` 或 `needs_input`。`course_json` 为本约定内容包的严格 JSON 字符串；不能提供 HTML、脚本或代码插件代替内容包。

`input` 必须原样保留本次请求的 `mode`、`subject`、`task_id`、`title`、`description`、`note` 和 `input_version`；另加 `topic_id`。顶层 `search_queries` 为实际查询词数组。知识单元的 `claims` 至少含一项 `{statement,conditions,location,source_ids}`，记录结论、条件、证据定位及来源 ID。历史 `goal/background/scope` 内容包继续兼容，但新请求不要求也不补造这些字段。

当前块字段：

- `paragraph`、`formula`、`code_example`、`callout`：`{type,text}`。
- `list`：`{type,items:[字符串]}`。
- `table`：`{type,headers:[字符串],rows:[[字符串]]}`，行列数一致。
- `self_check`：`{type,prompt,answer}`，参考解释默认折叠。

`figure`、`interactive` 不在当前渲染注册表。用完整文字、公式或表格替代，并在 `unresolved` 中记录交互缺口；理解所必需的交互未实现时不能声称教学交付完成。

课内 `revision` 为字符串；`prerequisites`、`goals`、`scope`、`pace` 为字符串。题目含 `id,kind,prompt,goals,hint,reference_process,self_check,source_ids`；选择题另有 `options:[{id,text}]` 和正确选项 ID `correct`。

生成结果默认“待审阅”。仅对象本身存在无法从上下文确定的歧义时保存 `needs_input` 与具体对象问题；缺少个人背景、目标或期限不构成等待理由。来源不足时保留访问状态和缺口，不能伪造检索结果。原知行课程仅由用户明确选择绑定，不依据标题推断。

## 写入与恢复边界

生成目录先保存草稿。应用只有在引用、ID、知识依赖环和组件能力检查通过后，才将结构化草稿接入阅读器；教学核查及实际界面验证另记状态。

新结果校验后，由代码将知识库投影保存为 `knowledge-base.json`，阶段与章节前置关系保存为 `learning-path.json`；完整正文与练习仍保存在课程内容包。原生阅读器提供「学习路径」「课程与练习」「知识库与来源」；路径中的章节可直接点击阅读，不限制手动跳转。

资源路径限定为内容包内的普通文件相对路径；不接受绝对路径、上级跳转或可执行插件。课程更新不覆盖笔记、作答和自评。输入在生成期间被修改时保留旧结果为草稿，等待按最新输入重新生成或用户选择复用。
