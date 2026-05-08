# Frame Description 服务回滚操作指南

> 适用版本：EduMind Backend v2.0+
> 最后更新：2026-05-08

---

## 1. 回滚原则

- **5 分钟内止血**：优先通过配置关闭功能，不改代码
- **代码回滚不超过 1 个 commit**：保持原子性
- **回滚后验证**：确认服务正常后再交付

---

## 2. 快速止血（无需改代码）

### 场景：描述服务异常、导致用户播放页面报错

**操作步骤**：

```bash
# Step 1: 编辑 .env，关闭功能
# 路径: /Users/yuan/final-work/edumind-backend/.env
vim /Users/yuan/final-work/edumind-backend/.env

# 修改以下行:
FRAME_DESC_ENABLED=false

# Step 2: 重启后端
cd /Users/yuan/final-work/edumind-backend
pkill -f "uvicorn app.main:app"
python run.py &

# Step 3: 验证
sleep 3
curl -s http://127.0.0.1:2004/api/frame_description/health
# 期望: {"enabled": false, ...}

# Step 4: 验证前端页面正常加载
# 打开 http://localhost:5173/videos/1/play
# 确认播放器面板不显示"实时画面描述"（或显示但禁用）
```

**影响**：功能关闭，不影响视频播放和字幕功能。

### 场景：Cloud Qwen-VL fallback 异常或不希望画面帧出本机

**操作步骤**：

```bash
# Step 1: 编辑 .env，仅关闭云端 fallback
FRAME_DESC_CLOUD_FALLBACK_ENABLED=false

# Step 2: 保持本地 Qwen3VL 主路径
FRAME_DESC_BACKEND=qwen3vl

# Step 3: 重启后端
cd /Users/yuan/final-work/edumind-backend
pkill -f "uvicorn app.main:app"
python run.py &
```

**影响**：本地 Qwen3VL 仍可用；本地不可用时直接走字幕描述和最小安全响应，不再调用通义千问视觉 API。

---

## 3. 回滚最新代码 Commit

### 场景：代码变更导致服务崩溃或功能异常

**操作步骤**：

```bash
cd /Users/yuan/final-work/edumind-backend

# Step 1: 确认最新 commit
git log --oneline -5
# 找到包含 frame_description 的最新 commit

# Step 2: 查看相关文件变更
git diff HEAD~1 --name-only | grep frame_desc

# Step 3: 单独回滚 frame_description 相关文件
git checkout HEAD~1 -- \
    app/schemas/frame_description.py \
    app/services/frame_description_service.py \
    app/services/qwen_vl_cloud_client.py \
    app/routers/frame_description.py

# Step 4: 如需要，回滚 governance gateway 变更
git checkout HEAD~1 -- app/agents/governance/tools_learning_flow.py

# Step 5: 回滚配置文件与日志启动配置（如果有）
git checkout HEAD~1 -- app/core/config.py app/main.py .env.example

# Step 6: 关闭功能作为双重保险
vim .env
# 确保: FRAME_DESC_ENABLED=false

# Step 7: 重启验证
pkill -f "uvicorn app.main:app"
python run.py &
sleep 3

# Step 8: 运行测试
python -m compileall app
curl -s http://127.0.0.1:2004/api/frame_description/health
```

### 完整回滚所有变更（安全方式）

```bash
cd /Users/yuan/final-work/edumind-backend

# 找到包含 Frame Description 的提交
git log --oneline --all | grep -i "frame\|实时描述\|画面描述" | head -10

# 假设需回滚的提交为 abc1234（从新到旧按顺序回滚）
git revert abc1234

# 推送回滚提交（避免 history rewrite）
git push

# 重启服务并验证
pkill -f "uvicorn app.main:app"
python run.py &
```

---

## 4. 提示词版本回滚

### 场景：描述质量下降，需回退到上一版本提示词

```bash
# 编辑 app/services/frame_description_service.py
# 找到 get_active_prompt_template() 函数

# 当前可能为:
def get_active_prompt_template() -> PromptTemplate:
    return PROMPT_TEMPLATES["v2"]

# 回滚到 v1:
def get_active_prompt_template() -> PromptTemplate:
    return PROMPT_TEMPLATES["v1"]

# 保存后无需重启（Python 会重新加载）
# 验证
curl -s http://127.0.0.1:2004/api/frame_description/health
```

---

## 5. 数据库回滚

Frame Description 服务**不使用数据库**存储状态（会话信息存于内存，重启清空）。

如果未来添加了数据库表：
```bash
# 查看是否有迁移文件
ls migrations/versions/ | grep frame_desc

# 回滚迁移（如果需要）
alembic downgrade -1
```

---

## 6. 前端回滚

### 场景：前端 Player.vue 导致页面崩溃

```bash
cd /Users/yuan/final-work/EduMind/mobile-frontend

# 回滚 Player.vue 和 frameDescription.js
git checkout HEAD~1 -- src/views/Player.vue src/api/frameDescription.js

# 重新构建
npm run build

# 或直接禁用 Player.vue 中的 frame description 集成
# 编辑 src/views/Player.vue
# 注释掉 fd-panel 相关行（行 63-149）
```

---

## 7. 回滚验证清单

完成回滚后，必须逐项验证：

- [ ] `curl http://127.0.0.1:2004/api/frame_description/health` 返回 200
- [ ] `python run.py` 启动无报错
- [ ] `python -m compileall app` 编译通过
- [ ] `pytest tests/smoke/test_app_startup.py -v` 通过
- [ ] `python scripts/validate_system_requirements.py` 通过
- [ ] 视频播放页面正常加载
- [ ] 视频播放、字幕、笔记功能正常
- [ ] 后端日志无 ERROR 级别异常
- [ ] 前端控制台无 JS 报错

---

## 8. 联系方式

| 场景 | 联系人 |
|------|--------|
| Vinci 服务问题 | 联系 Vinci 团队 |
| 后端问题 | 后端负责人 |
| 前端问题 | 前端负责人 |
| 生产故障 | 值班 SRE |
