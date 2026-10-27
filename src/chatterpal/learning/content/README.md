# 任务内容与版本

本目录保存由人工编写的英语练习情境。`TaskCatalog` 加载 JSON，`TaskDefinition` 校验字段和引用关系；加载内容本身不初始化提供商、模型或数据库。

## 当前内容

| Task ID | 情境 | 需要表达的信息 |
| --- | --- | --- |
| `weekend` | 描述周末 | 一项过去的活动、同行者或独自活动、感受或原因。 |
| `restaurant-order` | 在餐厅点餐 | 餐点与数量、饮品选择或谢绝、一个请求或问题。 |
| `hotel-check-in` | 办理酒店入住 | 入住意图和已有预订、虚构预订姓名与时长、一个住宿问题。 |

当前每项内容版本为 `1`，预计练习时间约 8 分钟。时长是产品估计，不代表学习效果。每项任务包含两道迁移问题，共六道；菜单、预订等信息仅用于练习。

## 内容结构

完整字段见 [`tasks.py`](../tasks.py)。主要分为：

- 标识与呈现：`task_id`、`content_version`、标题、说明、时长和双方角色。
- 对话目标：`objectives`、`required_information`、开场、两个完整示例和可用表达。
- 教练步骤：每个 `criterion_id` 对应一个问题、一个简化问题和一句起始提示。
- 重练：分别提供 `read_aloud` 和 `free_response`，自由表达允许其他有效措辞。
- 迁移复习：每道题有稳定的 `question_id`，并引用当前任务的评价条件。
- 来源：`source_reference` 记录编写依据；周末任务改编自早期聊天场景，餐厅和酒店任务为人工新增。

条件与问题 ID 在任务内必须唯一。步骤需要一一覆盖条件；迁移问题只能引用当前任务存在的条件。

## 版本与会话

`TaskCatalog.get_task(task_id, content_version)` 按明确版本取内容；不传版本时取当前版本。自定义目录包含同一任务的多个版本时，最后提供的版本被选为当前版本；重复的任务 ID / 版本组合会报错。

开始练习时，学习服务将所选内容保存为会话快照。反馈、重练和复习继续引用该快照，不将已有会话替换为新内容。更改任务目标或问题语义时应更新 `content_version`，并验证旧会话、评价依据和复习规则的兼容性。

新增内容还需更新 [`catalog.py`](../catalog.py) 的资源列表，并检查任务专属评价上下文；只增加一个 JSON 文件不会自动出现在目录中。任务达成需要来自用户原话的依据，无法验证时应保留不可评估状态，不能用关键词命中或回合数量代替。

## 验证

在仓库根目录运行针对性测试：

```bash
uv run --frozen python -m pytest -o addopts='' tests/learning/test_tasks_catalog.py tests/learning/test_tasks_coaching.py tests/learning/test_tasks_coaching_degradation.py -q
```

该命令关闭全包覆盖率选项以验证相关行为，不代表满足完整回归的 80% 覆盖率门槛。跨任务流程与复习问题还需按[开发指南](../../../../docs/development.md)运行相应 API 和浏览器检查；完整功能边界见[学习 API 契约](../../../../docs/learning_api_contract.md)。
