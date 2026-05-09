# 部署指南（本地体验）

最快上手路径：5 分钟从 git clone 到浏览器看 dashboard。
不需要 OpenAI 密钥，不需要 anvil，不需要 Foundry。

## 1. 安装依赖

```bash
# Python 3.11+
pip install django django-cors-headers django-storages \
            numpy openai web3 eth-account selenium pytest
```

## 2. 创建本地 utils.py

第一次部署时项目会要你提供 `reverie/backend_server/utils.py`（在
`.gitignore` 里，每个开发者本地自配）。仓库已带模板：

```bash
# 已存在则跳过；不存在就从 README 模板抄一份
test -f reverie/backend_server/utils.py || cat > reverie/backend_server/utils.py <<'EOF'
openai_api_key = ""           # 留空 → llm_action_loop 静默 no-op
key_owner      = "local-dev"
maze_assets_loc = "../../environment/frontend_server/static_dirs/assets"
env_matrix      = f"{maze_assets_loc}/the_ville/matrix"
env_visuals     = f"{maze_assets_loc}/the_ville/visuals"
fs_storage      = "../../environment/frontend_server/storage"
fs_temp_storage = "../../environment/frontend_server/temp_storage"
collision_block_id = "32125"
debug = False
EOF
```

## 3. 启动 Django 后端

```bash
cd environment/frontend_server
DJANGO_SETTINGS_MODULE=frontend_server.settings.base \
  python3 manage.py runserver 0.0.0.0:8000
```

打开 http://127.0.0.1:8000/ —— 看到 dashboard。

playground 模式即时可玩（rule-based 经济 sim、canvas 编辑器）。
Live 模式需要先建一个 sim（下一步）。

## 4. 一键灌入 demo sim 数据

```bash
# 回到 repo 根
cd /path/to/generative_agents
python3 scripts/seed_demo_sim.py     # 默认 sim_code = demo_plaza_run
```

回到 dashboard：
1. 点右上角面板的 `Live` 按钮
2. 在 sim 输入框填 `demo_plaza_run`，回车
3. 1.5 秒后 6 路 fetch 自动开始

你会看到的内容：
- **股库存 / 价格** 来自 `vending_state.json`（Coke 14, Water 9, ...）
- **SLA 风险护栏** 来自 `sla_state.json`
- **供货商 / 買掛金** ¥58.84，下次支払 5/10 由 calbee
- **日报 KPI chips** + 14 天利润 sparkline（一周历史）
- **审计列表** 显示 6 条 journal 条目
- **drift 红条** 顶部显示 1 笔未提交（demo 故意留的）
- **Dispatch 试运行**：粘贴 `{"action":"PRICE_CHANGE","sku":"Coke","next_price_cents":162}` 点试运行

## 5. 跑测试套件（离线，无需后端）

```bash
pytest tests/ -q     # 应输出 128 passed
```

## 6. 进阶：接真 LLM

```bash
# 编辑 utils.py
openai_api_key = "sk-…"        # 你的 OpenAI key

# 模型路由（可选）
export MODEL_VENDY=gpt-4o
export MODEL_CUSTOMER=gpt-4o-mini
```

随后启动 reverie sim（下面）就会真的调用 LLM。

## 7. 进阶：跑完整仿真（reverie + Phaser 前端）

```bash
# 终端 1：Django 前端（已经在跑就跳过）
cd environment/frontend_server
python3 manage.py runserver 0.0.0.0:8000

# 终端 2：reverie 后端（认知循环）
cd reverie/backend_server
python3 reverie.py
# 提示输入 fork_sim_code:  base_vending_min
# 提示输入 sim_code:       my_first_run
```

打开 http://127.0.0.1:8000/simulator_home —— Phaser 渲染的广场地图。
打开 http://127.0.0.1:8000/  —— 同一 sim_code 的 dashboard live 模式。

## 8. 进阶：链上 TaskRegistry（anvil + Foundry）

```bash
# 装 Foundry（一次）
curl -L https://foundry.paradigm.xyz | bash
foundryup

# 终端 3：anvil
bash scripts/devchain_up.sh

# 终端 4：部 V2 + 写 .env.local
bash scripts/deploy_local.sh                 # V2 (默认)
# 或
bash scripts/deploy_local.sh --v1            # 老合约

# 跑合约测试
bash scripts/forge_test.sh

# reverie 启动时使用 live 模式
export BLOCKCHAIN_MODE=live
# .env.local 已经写好 RPC_URL / CONTRACT_ADDRESS / PRIVATE_KEY
```

可选：开 batch 窗口降低 tx 数（仿真 1 日 200 tx → 约 70 tx）

```bash
export TX_BATCH_WINDOW_SEC=0.5
```

## 9. 进阶：接真天气

```bash
export WEATHER_PROVIDER=http
export WEATHER_API_URL='https://api.openweathermap.org/data/2.5/weather?lat=35.68&lon=139.76&appid=YOUR_KEY'
export WEATHER_TTL_SEC=600
```

## 排错

- **`/landing/` 500 错误**：upstream landing 模板的小问题；不影响 `/`
- **dashboard 显示 "上次 sim：demo_plaza_run（点 Live 启动）"**：localStorage 记得，按 Live 即可
- **drift 红条不消失**：demo 故意留了 1 笔 stuck intent；要看真实状态删掉
  `environment/frontend_server/storage/demo_plaza_run/blockchain_journal.jsonl`
  里 `local_uuid: stuck-1` 那一行
- **/api/vending/* 返回 404**：sim_code 不存在；先跑 `seed_demo_sim.py`
- **LLM action loop 一直 no-op**：utils.py 里 `openai_api_key=""` 导致；填真 key 即可

## 文件清单速查

| 路径 | 作用 |
|---|---|
| `index.html` | dashboard（playground + live） |
| `environment/frontend_server/translator/vending_api.py` | `/api/vending/*` 端点 |
| `reverie/backend_server/vending/` | Python 业务模块 |
| `contracts/src/TaskRegistryV2.sol` | 合约 V2 |
| `scripts/seed_demo_sim.py` | 灌 demo 数据 |
| `scripts/devchain_up.sh` / `deploy_local.sh` | 链 |
| `scripts/forge_test.sh` | 合约测试 |
| `scripts/reconcile.py` | 链上链下对账 |
| `tests/` | pytest，128 项 |
| `VENDING.md` | 全部模块详细说明 |
