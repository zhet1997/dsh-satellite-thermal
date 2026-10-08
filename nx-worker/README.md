# NX/Simcenter 独立执行器

在有 NX12/Simcenter Space Systems Thermal 与相应许可的 Windows 工作站部署。本执行器提供 HTTP 队列；轻量业务服务与 DSH 插件不需要安装 NX。Python 3.10+ 服务进程与 NX 内嵌 Python 相互独立。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install .
$env:NX_JOURNAL_EXECUTABLE = 'D:\Install\NX12\NXBIN\run_journal.exe'
$env:NX_WORK_ROOT = 'D:\ThermalRuns'
$env:NX_WORKER_HOST = '127.0.0.1'
$env:NX_WORKER_PORT = '8791'
# 网络部署由管理员设置 NX_WORKER_TOKEN；不要把令牌写进网页或工程快照。
.\.venv\Scripts\thermal-nx-worker.exe
```

`NX_STAGE_TIMEOUT_SECONDS` 控制每阶段超时，默认7200秒。NX12工作目录必须为ASCII路径。监听非回环地址时必须配置令牌；通过可信网络或HTTPS反向代理暴露。单执行器顺序执行队列，避免多个job争用NX许可；修改输入不会取消旧job。

接口：`GET /health`，`POST /jobs` multipart字段`snapshot`(JSON文本)、`geometry`(STEP或单STEP的ZIP)、可选`idempotencyKey`和`sha256`。也接受JSON `{idempotencyKey,snapshot,geometry:{filename,sha256,contentBase64}}`。`GET /jobs/{id}`返回状态、回读和工件；`POST /jobs/{id}/cancel`必须提交`{"confirm":true}`；`GET /jobs/{id}/artifacts/{name}`只下载登记工件。所有接口使用Bearer令牌（若已配置）。

每个job保留原始标准快照、输入SHA-256、冻结journal、阶段日志和原生工程。建模、重开回读、求解和结果提取分别启动NX进程。求解前可下载已保存的PRT/FEM/SIM、真实有限元边界GLB、节点/单元CSV和材料/条件回读。温度由NX结果读取器导出VTU；不提供模拟成功或随机温度回退。

取消只针对本job捕获的PID和进程创建时间，确认记录的进程树全部停止后才标记cancelled。服务崩溃后的活动job标为failed/interrupted并保留恢复信息，不会自动重跑仍可能存活的NX。检查`stages/processes.json`和原生文件后，新输入/新run可重新提交；相同幂等键不会改写旧工程。

静态构造与实际验证严格区分。NX12的真实会话、FEM/SIM创建、壳网格类型及辐射descriptor探测由仓库`runtime/nx-probe*`记录；完整工程成功须经过实际五阶段job。原3个journal保留在`thermal_executor/journals/vendor`，来源与散列在`SOURCE-MANIFEST.json`。新增代码不可根据仅有descriptor存在宣称工程验收通过。

当前实现边界：壳输入必须是实际sheet/midsurface，不会将实体静默变成壳。全模型完整辐射图使用All Radiation；互不重叠的完整子图以及独立的组件/面空间目标使用Enclosure Radiation，两个目标集分别表示正面和背面。实体+sheet无互辐射边的独立分组已通过真实NX完整求解；面级复杂组合仍需逐工程验证。不会给非完整连通图自动新增互辐射边，混合组件与所属面造成目标重叠、同组部分目标有空间环境、非1辐射倍率及多空间温度会明确报错。六向外热流对非轴对齐面要求显式面热流。遇到未覆盖场景会保留工程与诊断并明确失败，不伪造成功。

六向热流使用面级光学参数（空值继承组件）：太阳直射和反照乘吸收率α，红外乘发射率ε；标为已吸收的热流不再重复乘系数。薄壳正反两侧热流相加后施加到中面，当前标准输入的单组面光学参数适用于两侧。面参数覆盖使用原生Override Thermal Emissivity和Override Thermal Solar Absorptivity对象，重开SIM后逐值核对；实际工况热流也逐值核对并保留映射记录。

已运行的光学覆盖基准使用面ε=0.6、α=0.4、+Z方向100 W/m²及总吸热10.4 W，NX平均温度267.181396 K，解析参考267.165132 K。导热基准实际2100节点、8478个TET4单元，300/320 K边界下热流2 W，能量相对残差约8.68×10⁻⁶。原生条件的目标核验读取TargetSetManager与CaeGroup.GetEntities：面由继承的FACE_REGION_ID识别，单元由实际Mesh.Name与Label识别；重开SIM后验证组名、成员集合、单元数和成员散列。expectedTargets仅表示输入，targets与targetSets表示实际原生成员。

运行不依赖NX的存储、传输和进程取消测试：`python -m unittest discover -s tests -v`。真实小算例另由集成验收驱动，不包含在普通单元测试。
