# B3交付记录：本地资料导入、来源与受控产物存储

日期：2026-09-09。已验收范围：离线代码与HTTP/CLI行为；真实模型业务、浏览器人工点检与URL/搜索未验收。

## 交付

1. **路径边界（S2-08本地）**：`src/harness/storage/paths.py` 提供 canonical/_resolve_through_links/
   is_under/ensure_under/resolve_under/ensure_relative_name。所有写入与索引访问先规范化再判包含性；
   符号链接与 Windows junction 一律解析到最终物理位置判定，**对“尚不存在的尾部”也解析中间链接**
   （实测 os.path.realpath 在该场景不展开 junction，已绕过该坑）。
2. **来源登记与导入（S2-01/05/06本地）**：`src/harness/storage/sources.py`。
   粘贴文本与 TXT/Markdown 文件导入到 `workspaces/jobs/<job_id>/sources/`；`sources.json` 保存清单索引，
   `<source_id>.meta.json` 保存段落定位。分类：ok/partial/duplicate/empty/unsupported/too_large/read_failed，
   全部登记可查。内容哈希 sha256 + 空白折叠规范化哈希；转载/同文只登记 duplicate_of，不重复存全文。
   标题+段落号+字符偏移可定位原文。GB18030 兼容，无法解码字节标记 partial 并保留其余正文。
   原始资料文件只读，从不回写或覆盖。
3. **受控产物（S2-07存储层）**：`src/harness/storage/artifacts.py`。
   artifact_id=`<kind>.v<N>` 自动版本递增、同 id 永不覆盖；先原子写全文再登记索引；
   读取校验内容哈希，索引损坏/重复 id/未登记孤儿文件一律拒绝；模型后续经 artifact_id
   或 `artifacts/` 下受控相对路径访问（B5 接通工具）。
4. **统一入口接入**：`TaskRequest.texts/files`（JSON 列表自动转元组，请求层校验上限）；
   `ResearchApplication.run()` 先导入并登记资料再启动 Runtime；零可用来源抛
   SourceImportError（明确消息），部分失败保留索引并继续；job.json 记录 import 摘要；
   request.json 快照不含粘贴正文（全文以 sources/ 为准）。
5. **CLI**：`--import-file PATH` / `--import-text TEXT` 可重复；输出 JSON 附 sources 摘要；
   导入业务错误打印完整 message。
6. **Web**：`/api/jobs/<job_id>/sources`、`/sources/<sid>/text`、`/artifacts`、`/artifacts/<aid>/content`
   只读端点（job/source/artifact id 白名单，全文经路径边界校验，GET 无写副作用）；
   页面新增 ⑳资料与产物 面板：来源状态/去重/全文与产物内容查看。

## 使用

```powershell
# 带本地资料运行（默认 Mock 离线）
.venv\Scripts\python -m src.interfaces.cli "整理这2份材料" --import-file D:\资料\笔记.md --import-text "补充粘贴。"
# 输出 JSON 中可见 root_job_id 与 sources 摘要；产物目录：
#   workspaces\jobs\job_<id>\sources.json / sources\<id>.md|txt / sources\<id>.meta.json

# 全部失败（空内容/不支持/不存在且无可用资料）时明确失败并给出原因
.venv\Scripts\python -m src.interfaces.cli "整理" --import-text "   "   # 退出码1，message含原因

# Web：提交表单任务下方新增“文件路径(逗号或换行分隔)/粘贴文本”输入；运行后在⑳面板点行看全文
.venv\Scripts\python -m src.interfaces.web.workbench --port 8765
```

限制（拟定产品值，可按真实样本调整）：单任务≤20个来源；单来源≤2MB；
任务累计存储≤10MB（当前为逐来源2MB+条数上限，累计总量在批量入口执行）；超限明确拒绝，不静默截断。

## 验证证据

- 全量：`.venv\Scripts\python -m pytest`，255 passed，21.57秒（B2记录基线199）。
- 定向新增：路径边界11（含真实 mklink /J junction 逃逸与不存在尾部穿越拒绝）、来源13
  （分类/GB18030/partial/去重/定位/超限/累计总量/索引损坏）、产物12（版本/防覆盖/哈希篡改/越界/孤儿/损坏索引）、
  请求与入口14（应用先导入后运行、零可用失败、部分失败保留、CLI子进程、快照不含正文）、Web端点5。
- 修复B2遗留偶发：/job 轮询撞上 run.json 未写 root_job_id 的窗口返回 note，谓词改为容忍窗口。
- 20个业务案例和10个故障案例定义仍有效，业务执行0次；无付费模型请求、无新增第三方依赖、未修改DSH。

## 兼容性、限制与回退

- 新代码只新增目录 `src/harness/storage/` 与既有文件小改（request/research/cli/workbench/model_gateway）；
  `TaskRequest` 新增字段带默认值，旧调用不受影响；request.json 去掉 texts 键属预期变化（正文以 sources/ 为准）。
- URL/搜索（S2-02/03/04/09）与网页转载去重待B4；S2-05/06/07的网页侧、模型读取工具与成稿链待B5。
- sources/artifacts 索引仍是 JSON 原子写，不是多文件事务；崩溃窗口下以索引为准对账（S4改进）。
- 页面只做 HTTP/HTML 级验证；真实浏览器人工点检与真实模型业务运行未执行。
- 回退：删除 storage 目录并还原上述小改即可；用户运行产物（jobs/sources/artifacts）不要删除。

下一步B4：URL获取与正文提取、一个搜索服务、网络安全边界。
